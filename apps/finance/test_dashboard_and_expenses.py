from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import ExpenseCategory
from apps.partners.models import Customer, Supplier
from .invoice_services import issue_invoice
from .models import (
    Expense,
    ExpenseHistory,
    ExpensePayment,
    Invoice,
    InvoiceLine,
    Payment,
    Quote,
)
from .payment_services import record_invoice_payment


class DashboardAndExpenseTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="full-manager",
            password="testpassword123",
        )
        permissions = Permission.objects.filter(
            content_type__app_label="finance",
            codename__in=[
                "view_invoice",
                "add_invoice",
                "change_invoice",
                "view_quote",
                "add_quote",
                "change_quote",
                "view_payment",
                "add_payment",
                "change_payment",
                "view_expense",
                "add_expense",
                "change_expense",
                "add_expensepayment",
            ],
        )
        self.user.user_permissions.add(*permissions)
        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(name="Client Dashboard")
        self.supplier = Supplier.objects.create(name="Fournisseur Bureautique")
        self.category = ExpenseCategory.objects.create(name="Informatique")

    def test_dashboard_anonymous_redirects_to_login(self):
        anon_client = Client()
        response = anon_client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/connexion/", response["Location"])

    def test_dashboard_authenticated_computes_kpis(self):
        # 1. Invoice of 1200 MAD
        invoice = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description="Service A",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        issue_invoice(invoice.pk, self.user)

        # 2. Payment received 500 MAD
        record_invoice_payment(
            invoice_id=invoice.pk,
            amount=Decimal("500.00"),
            payment_date=date(2026, 10, 25),
            method=Payment.Method.BANK_TRANSFER,
            actor=self.user,
        )

        # 3. Expense of 300 MAD, 100 MAD paid
        expense = Expense.objects.create(
            category=self.category,
            supplier=self.supplier,
            description="Abonnement serveur",
            expense_date=date(2026, 10, 25),
            amount=Decimal("300.00"),
        )
        ExpensePayment.objects.create(
            expense=expense,
            payment_date=date(2026, 10, 25),
            amount=Decimal("100.00"),
            method=ExpensePayment.Method.CARD,
        )

        # 4. Quotes: 1 sent, 1 accepted
        q1 = Quote.objects.create(customer=self.customer)
        Quote.objects.filter(pk=q1.pk).update(status=Quote.Status.SENT, number="DEV-2026-0001")
        q2 = Quote.objects.create(customer=self.customer)
        Quote.objects.filter(pk=q2.pk).update(status=Quote.Status.ACCEPTED, number="DEV-2026-0002")

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tableau de bord")
        self.assertContains(response, "1 200,00 MAD")  # Invoiced
        self.assertContains(response, "500,00 MAD")    # Collections
        self.assertContains(response, "700,00 MAD")    # Pending receivables
        self.assertContains(response, "300,00 MAD")    # Expense

    def test_expense_crud_workflow(self):
        receipt_file = SimpleUploadedFile("recu.txt", b"Contenu du justificatif fiscal")

        # 1. Create expense
        response = self.client.post(
            reverse("expense-create"),
            {
                "category": self.category.pk,
                "supplier": self.supplier.pk,
                "description": "Achat fournitures bureau",
                "expense_date": "25/10/2026",
                "amount": "450.00",
                "receipt": receipt_file,
            },
        )
        self.assertEqual(response.status_code, 302)
        expense = Expense.objects.get(description="Achat fournitures bureau")
        self.assertEqual(expense.amount, Decimal("450.00"))
        self.assertEqual(expense.remaining_balance, Decimal("450.00"))
        self.assertEqual(expense.payment_status, "À payer")
        self.assertTrue(
            ExpenseHistory.objects.filter(
                expense=expense,
                event=ExpenseHistory.Event.CREATED,
            ).exists()
        )

        # 2. List view
        list_response = self.client.get(reverse("expense-list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Achat fournitures bureau")

        # 3. Detail view
        detail_response = self.client.get(reverse("expense-detail", args=[expense.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "450,00 MAD")

        # 4. Receipt view
        receipt_response = self.client.get(reverse("expense-receipt", args=[expense.pk]))
        self.assertEqual(receipt_response.status_code, 200)

        # 5. Partial payment (200 MAD)
        pay_response = self.client.post(
            reverse("expense-payment", args=[expense.pk]),
            {
                "payment_date": "25/10/2026",
                "amount": "200.00",
                "method": "bank_transfer",
                "reference": "VIR-FOURNISSEUR-01",
            },
        )
        self.assertEqual(pay_response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.amount_paid, Decimal("200.00"))
        self.assertEqual(expense.remaining_balance, Decimal("250.00"))
        self.assertEqual(expense.payment_status, "Partiellement payée")

        # 6. Final payment (250 MAD)
        pay_final = self.client.post(
            reverse("expense-payment", args=[expense.pk]),
            {
                "payment_date": "26/10/2026",
                "amount": "250.00",
                "method": "cheque",
                "reference": "CHQ-0012",
            },
        )
        self.assertEqual(pay_final.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.amount_paid, Decimal("450.00"))
        self.assertEqual(expense.remaining_balance, Decimal("0.00"))
        self.assertEqual(expense.payment_status, "Payée")

        # 7. Edit view is forbidden or handles error if amount is reduced below paid
        edit_response = self.client.post(
            reverse("expense-edit", args=[expense.pk]),
            {
                "category": self.category.pk,
                "description": "Achat fournitures bureau modifié",
                "expense_date": "25/10/2026",
                "amount": "100.00",  # below paid amount of 450!
            },
        )
        self.assertEqual(edit_response.status_code, 200)  # re-renders with error
        self.assertContains(edit_response, "Le montant ne peut pas être inférieur aux paiements déjà enregistrés")

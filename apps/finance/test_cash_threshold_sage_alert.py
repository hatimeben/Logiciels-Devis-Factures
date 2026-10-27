from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import ExpenseCategory
from apps.partners.models import Customer, Supplier
from .accounting_services import build_complete_fiduciary_pack_xlsx, get_payments_journal_data
from .invoice_services import issue_invoice
from .models import (
    CASH_PAYMENT_LEGAL_CEILING,
    Expense,
    ExpensePayment,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
)
from .payment_services import record_invoice_payment


class CashThresholdSageAlertTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="comptable-expert",
            password="testpassword123",
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=["finance", "partners"]
        )
        self.user.user_permissions.add(*perms)
        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(name="Atlas Holding SARL")
        self.supplier = Supplier.objects.create(name="Fournisseur IT Maroc")
        self.expense_category = ExpenseCategory.objects.create(name="Matériel & Consommables")

    def test_invoice_cash_payment_over_threshold_sage_alert(self):
        """
        Cas d'usage principal :
        Règlement en espèces de 14 924,75 MAD sur une facture émise.
        - La saisie n'est pas bloquée (flexibilité opérationnelle).
        - Message d'avertissement 'Sage' avec calcul de l'excédent de 9 924,75 MAD.
        - Facture marquée avec l'alerte fiscale orange.
        """
        inv = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=inv,
            description="Fourniture matériel et prestations",
            quantity=Decimal("1.000"),
            unit_price=Decimal("14924.75"),
            tax_percentage=Decimal("0.00"),
            tax_name="Sans taxe",
        )
        issue_invoice(inv.pk, self.user)
        inv.refresh_from_db()

        self.assertFalse(inv.has_cash_over_ceiling)

        # POST règlement en espèces de 14 924,75 MAD via la vue
        url = reverse("invoice-payment", args=[inv.pk])
        response = self.client.post(
            url,
            {
                "payment_date": date.today().isoformat(),
                "amount": "14924.75",
                "method": Payment.Method.CASH,
                "reference": "ESP-2026-001",
                "notes": "Règlement intégral en espèces par le client",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        inv.refresh_from_db()

        # 1. Vérification que la saisie a bien été acceptée (non bloquante)
        self.assertEqual(inv.total_paid, Decimal("14924.75"))
        self.assertEqual(inv.remaining_balance, Decimal("0.00"))
        self.assertEqual(inv.status, Invoice.Status.PAID)

        # 2. Vérification des propriétés fiscales du modèle
        payment = Payment.objects.filter(customer=self.customer).first()
        self.assertIsNotNone(payment)
        self.assertTrue(payment.is_cash)
        self.assertTrue(payment.is_cash_over_ceiling)
        self.assertEqual(payment.cash_excess_amount, Decimal("9924.75"))
        self.assertTrue(inv.has_cash_over_ceiling)
        self.assertEqual(inv.total_cash_excess, Decimal("9924.75"))

        # 3. Vérification du message d'avertissement 'Sage' exact
        messages_list = list(response.context["messages"])
        warning_messages = [m for m in messages_list if m.level_tag == "warning"]
        self.assertEqual(len(warning_messages), 1)
        expected_msg = (
            "Attention : Conformément à l'article 11-II du CGI marocain, les règlements en espèces sont "
            "déductibles dans la limite de 5 000 MAD TTC par jour et par fournisseur. "
            "La fraction excédentaire (soit 9 924,75 MAD) ne sera pas déductible fiscalement et devra être réintégrée."
        )
        self.assertIn("article 11-II du CGI marocain", warning_messages[0].message)
        self.assertIn("9 924,75 MAD", warning_messages[0].message)
        self.assertIn("ne sera pas déductible fiscalement et devra être réintégrée", warning_messages[0].message)

        # 4. Vérification de l'indicateur visuel sur la vue détail de la facture
        detail_response = self.client.get(reverse("invoice-detail", args=[inv.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "ALERTE ESPÈCES CGI")
        self.assertContains(detail_response, "Traçabilité Fiscale CGI (Article 11-II)")
        self.assertContains(detail_response, "9 924,75 MAD")
        self.assertContains(detail_response, "💵 Espèces")

    def test_payment_methods_categorization(self):
        """
        Vérifie la catégorisation distincte des modes de règlement :
        Virement, Chèque, Effet de commerce, Espèces.
        """
        self.assertIn("bill_of_exchange", [c[0] for c in Payment.Method.choices])
        self.assertIn("bank_transfer", [c[0] for c in Payment.Method.choices])
        self.assertIn("cheque", [c[0] for c in Payment.Method.choices])
        self.assertIn("cash", [c[0] for c in Payment.Method.choices])

        # Création de 4 paiements distincts
        p_vir = Payment.objects.create(
            customer=self.customer,
            payment_date=date.today(),
            amount=Decimal("10000.00"),
            method=Payment.Method.BANK_TRANSFER,
        )
        p_chq = Payment.objects.create(
            customer=self.customer,
            payment_date=date.today(),
            amount=Decimal("8000.00"),
            method=Payment.Method.CHEQUE,
        )
        p_eff = Payment.objects.create(
            customer=self.customer,
            payment_date=date.today(),
            amount=Decimal("12000.00"),
            method=Payment.Method.BILL_OF_EXCHANGE,
        )
        p_esp = Payment.objects.create(
            customer=self.customer,
            payment_date=date.today(),
            amount=Decimal("3000.00"),  # Sous le seuil de 5 000 MAD
            method=Payment.Method.CASH,
        )

        self.assertFalse(p_vir.is_cash_over_ceiling)
        self.assertFalse(p_chq.is_cash_over_ceiling)
        self.assertFalse(p_eff.is_cash_over_ceiling)
        self.assertFalse(p_esp.is_cash_over_ceiling)

    def test_expense_cash_payment_over_threshold_sage_alert(self):
        """
        Vérifie que pour les dépenses fournisseurs (achats), le règlement espèces > 5 000 MAD
        déclenche également l'avertissement d'article 11-II du CGI.
        """
        expense = Expense.objects.create(
            supplier=self.supplier,
            category=self.expense_category,
            expense_date=date.today(),
            description="Achat de matériel informatique au comptoir",
            amount=Decimal("8500.00"),
        )

        # POST paiement de dépense
        url = reverse("expense-payment", args=[expense.pk])
        response = self.client.post(
            url,
            {
                "payment_date": date.today().isoformat(),
                "amount": "8500.00",
                "method": ExpensePayment.Method.CASH,
                "reference": "BON-CAISSE-01",
                "notes": "Achat payé en espèces",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        expense.refresh_from_db()
        self.assertEqual(expense.amount_paid, Decimal("8500.00"))
        self.assertTrue(expense.has_cash_over_ceiling)
        self.assertEqual(expense.total_cash_excess, Decimal("3500.00"))

        messages_list = list(response.context["messages"])
        warning_messages = [m for m in messages_list if m.level_tag == "warning"]
        self.assertEqual(len(warning_messages), 1)
        self.assertIn("3 500,00 MAD", warning_messages[0].message)

    def test_fiduciary_export_highlights_cash_over_ceiling(self):
        """
        Vérifie que le journal des règlements (PCGM) et l'export fiduciaire
        identifient immédiatement la ligne en espèces pour l'expert-comptable.
        """
        inv = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=inv,
            description="Serveur",
            quantity=Decimal("1.000"),
            unit_price=Decimal("12000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        issue_invoice(inv.pk, self.user)
        inv.refresh_from_db()

        # Enregistrement paiement 14 400 MAD en espèces
        payment, alloc = record_invoice_payment(
            invoice_id=inv.pk,
            amount=Decimal("14400.00"),
            payment_date=date(2026, 10, 15),
            method=Payment.Method.CASH,
            reference="ESP-REC-99",
            actor=self.user,
        )

        journal = get_payments_journal_data(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(len(journal["entries"]), 1)
        entry = journal["entries"][0]

        self.assertTrue(entry["is_cash_over_ceiling"])
        self.assertEqual(entry["cash_excess_amount"], Decimal("9400.00"))
        self.assertIn("Plafond CGI dépassé", entry["fiscal_alert"])

        # Test génération Excel fiduciaire (xlsx)
        from .accounting_services import get_moroccan_vat_report_data, get_sales_journal_data
        vat_data = get_moroccan_vat_report_data(date(2026, 10, 1), date(2026, 10, 31))
        sales_data = get_sales_journal_data(date(2026, 10, 1), date(2026, 10, 31))
        xlsx_bytes = build_complete_fiduciary_pack_xlsx("Octobre 2026", vat_data, sales_data, journal)
        self.assertTrue(xlsx_bytes.startswith(b"PK"))

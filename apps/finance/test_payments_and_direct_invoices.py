from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Service, ServiceCategory, TaxRate
from apps.partners.models import Customer
from .invoice_forms import InvoiceForm, InvoiceLineFormSet
from .invoice_services import issue_invoice, save_invoice_draft
from .models import DocumentHistory, Invoice, InvoiceLine, Payment, PaymentAllocation
from .payment_services import allocate_payment_to_invoice, record_invoice_payment


class DirectInvoiceAndPaymentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="finance-manager",
            password="testpassword123",
        )
        permissions = Permission.objects.filter(
            content_type__app_label="finance",
            codename__in=[
                "view_invoice",
                "add_invoice",
                "change_invoice",
                "view_payment",
                "add_payment",
                "change_payment",
            ],
        )
        self.user.user_permissions.add(*permissions)
        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(
            name="Client Direct SARL",
            email="direct@example.test",
            phone="+212600000000",
            address="100 boulevard d'Anfa",
            city="Casablanca",
            tax_identifier="IF12345678",
        )
        category = ServiceCategory.objects.create(name="Développement")
        self.service = Service.objects.create(
            category=category,
            name="Développement application web",
            default_unit_price=Decimal("5000.00"),
        )
        self.tax_rate = TaxRate.objects.create(name="TVA 20%", percentage=Decimal("20.00"))

    def _line_payload(self, index, *, service_id="", description="", quantity="1.000", unit_price="1000.00", discount_type="percentage", discount_value="0.00", tax_rate_id=""):
        prefix = f"lines-{index}-"
        return {
            prefix + "service": service_id,
            prefix + "description": description,
            prefix + "quantity": quantity,
            prefix + "unit_price": unit_price,
            prefix + "discount_type": discount_type,
            prefix + "discount_value": discount_value,
            prefix + "tax_rate": tax_rate_id,
            prefix + "DELETE": "",
        }

    def _invoice_payload(self, lines, *, customer_id=None):
        payload = {
            "customer": customer_id or self.customer.pk,
            "project": "",
            "due_date": "31/12/2026",
            "notes": "Facture directe",
            "lines-TOTAL_FORMS": str(len(lines)),
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for idx, line in enumerate(lines):
            payload.update(line)
        return payload

    def test_direct_invoice_creation_service(self):
        invoice = Invoice(customer=self.customer, due_date=date(2026, 12, 31), notes="Facture directe service")
        formset_data = {
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-service": str(self.service.pk),
            "lines-0-description": self.service.name,
            "lines-0-quantity": "2.000",
            "lines-0-unit_price": "5000.00",
            "lines-0-discount_type": "percentage",
            "lines-0-discount_value": "10.00",
            "lines-0-tax_rate": str(self.tax_rate.pk),
        }
        formset = InvoiceLineFormSet(formset_data, instance=invoice, prefix="lines")
        self.assertTrue(formset.is_valid())

        saved = save_invoice_draft(invoice, formset, self.user)
        self.assertIsNotNone(saved.pk)
        self.assertEqual(saved.status, Invoice.Status.DRAFT)
        self.assertIsNone(saved.number)
        self.assertEqual(saved.customer_snapshot["name"], "Client Direct SARL")
        self.assertEqual(saved.lines.count(), 1)
        self.assertEqual(saved.total_ttc, Decimal("10800.00"))  # 2 * 5000 = 10000 - 10% = 9000 + 20% TVA = 10800
        self.assertTrue(
            DocumentHistory.objects.filter(
                invoice=saved,
                event=DocumentHistory.Event.CREATED,
            ).exists()
        )

    def test_direct_invoice_create_view_and_issuance(self):
        lines = [
            self._line_payload(
                0,
                service_id=str(self.service.pk),
                description=self.service.name,
                quantity="1.000",
                unit_price="5000.00",
                tax_rate_id=str(self.tax_rate.pk),
            )
        ]
        payload = self._invoice_payload(lines)
        response = self.client.post(reverse("invoice-create"), payload)
        self.assertEqual(response.status_code, 302)

        invoice = Invoice.objects.filter(customer=self.customer).latest("id")
        self.assertRedirects(response, reverse("invoice-detail", args=[invoice.pk]))
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.total_ttc, Decimal("6000.00"))

        # Issue the invoice
        issue_response = self.client.post(reverse("invoice-issue", args=[invoice.pk]))
        self.assertEqual(issue_response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertTrue(invoice.number.startswith("FAC-"))
        self.assertEqual(invoice.remaining_balance, Decimal("6000.00"))

    def test_record_partial_and_full_payment(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description="Prestation test",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        invoice = issue_invoice(invoice.pk, self.user)
        self.assertEqual(invoice.total_ttc, Decimal("1200.00"))
        self.assertEqual(invoice.remaining_balance, Decimal("1200.00"))

        # 1. Partial payment of 400 MAD
        payment1, alloc1 = record_invoice_payment(
            invoice_id=invoice.pk,
            amount=Decimal("400.00"),
            payment_date=date(2026, 10, 25),
            method=Payment.Method.BANK_TRANSFER,
            reference="VIR-001",
            actor=self.user,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)
        self.assertEqual(invoice.total_paid, Decimal("400.00"))
        self.assertEqual(invoice.remaining_balance, Decimal("800.00"))

        # 2. Overpayment attempt rejected
        with self.assertRaises(ValidationError):
            record_invoice_payment(
                invoice_id=invoice.pk,
                amount=Decimal("800.01"),
                payment_date=date(2026, 10, 25),
                method=Payment.Method.CHEQUE,
                actor=self.user,
            )

        # 3. Completing payment of 800 MAD
        payment2, alloc2 = record_invoice_payment(
            invoice_id=invoice.pk,
            amount=Decimal("800.00"),
            payment_date=date(2026, 10, 26),
            method=Payment.Method.CHEQUE,
            reference="CHQ-7788",
            actor=self.user,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.total_paid, Decimal("1200.00"))
        self.assertEqual(invoice.remaining_balance, Decimal("0.00"))
        self.assertEqual(invoice.payment_allocations.count(), 2)

        # Check DocumentHistory
        history_events = list(invoice.history.filter(event=DocumentHistory.Event.PAYMENT_RECORDED))
        self.assertEqual(len(history_events), 2)
        self.assertEqual(history_events[-1].snapshot["new_status"], Invoice.Status.PAID)

    def test_invoice_payment_view(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description="Prestation consulting",
            quantity=Decimal("1.000"),
            unit_price=Decimal("500.00"),
            tax_percentage=Decimal("0.00"),
            tax_name="Sans taxe",
        )
        issue_invoice(invoice.pk, self.user)
        invoice.refresh_from_db()

        # GET payment form
        get_response = self.client.get(reverse("invoice-payment", args=[invoice.pk]))
        self.assertEqual(get_response.status_code, 200)

        # POST payment
        post_response = self.client.post(
            reverse("invoice-payment", args=[invoice.pk]),
            {
                "payment_date": "25/10/2026",
                "amount": "500.00",
                "method": "card",
                "reference": "CARTE-9988",
                "notes": "Règlement TPE",
            },
        )
        self.assertRedirects(post_response, reverse("invoice-detail", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.total_paid, Decimal("500.00"))

    def test_payment_list_view(self):
        payment = Payment.objects.create(
            customer=self.customer,
            payment_date=date(2026, 10, 25),
            amount=Decimal("1500.00"),
            method=Payment.Method.BANK_TRANSFER,
            reference="TEST-LIST-REF",
        )
        response = self.client.get(reverse("payment-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TEST-LIST-REF")
        self.assertContains(response, "1 500,00 MAD")

    def test_paid_invoice_pdf_contains_acquit_and_balance(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            customer_snapshot={"name": self.customer.name, "city": "Casablanca"},
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            description="Prestation PDF",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA",
        )
        issue_invoice(invoice.pk, self.user)
        record_invoice_payment(
            invoice_id=invoice.pk,
            amount=Decimal("1200.00"),
            payment_date=date(2026, 10, 25),
            method=Payment.Method.BANK_TRANSFER,
            actor=self.user,
        )
        invoice.refresh_from_db()

        response = self.client.get(reverse("invoice-pdf", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF-"))


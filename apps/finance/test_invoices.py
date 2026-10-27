from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Service, ServiceCategory, TaxRate
from apps.partners.models import Customer

from .invoice_services import convert_quote_to_invoice, issue_invoice
from .models import (
    DocumentHistory,
    Invoice,
    InvoiceLine,
    InvoiceNumberingSettings,
    Quote,
    QuoteLine,
)
from .quote_calculations import calculate_quote_totals


class InvoiceModuleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="invoice-user")
        permissions = Permission.objects.filter(
            content_type__app_label="finance",
            codename__in=["view_invoice", "add_invoice", "change_invoice", "change_quote"],
        )
        self.user.user_permissions.add(*permissions)
        self.client = Client()
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            name="Client facture",
            email="client-facture@example.test",
            address="15 avenue Hassan II",
            city="Rabat",
        )
        category = ServiceCategory.objects.create(name="Conseil")
        self.service = Service.objects.create(
            category=category,
            name="Accompagnement Power BI",
            default_unit_price=Decimal("1000.00"),
        )
        self.tax_rate = TaxRate.objects.create(name="TVA", percentage=Decimal("20.00"))

    def _accepted_quote(self, status=Quote.Status.ACCEPTED):
        quote = Quote.objects.create(
            customer=self.customer,
            customer_snapshot={
                "name": "Client historique",
                "email": "historique@example.test",
                "address": "Ancienne adresse",
                "city": "Rabat",
            },
            status=Quote.Status.DRAFT,
            notes="Conditions acceptées",
        )
        QuoteLine.objects.create(
            quote=quote,
            service=self.service,
            description="Accompagnement Power BI",
            quantity=Decimal("2.000"),
            unit_price=Decimal("1000.00"),
            discount_type=QuoteLine.DiscountType.PERCENTAGE,
            discount_value=Decimal("10.00"),
            tax_rate=self.tax_rate,
            tax_percentage=self.tax_rate.percentage,
            tax_name=self.tax_rate.name,
            position=0,
        )
        Quote.objects.filter(pk=quote.pk).update(
            status=status,
            number=f"DEV-2026-{quote.pk + 20:04}",
            issue_date=date(2026, 10, 25),
        )
        quote.refresh_from_db()
        return quote

    def test_conversion_copies_lines_discounts_taxes_and_customer_snapshot(self):
        quote = self._accepted_quote()

        invoice = convert_quote_to_invoice(quote.pk, self.user)
        quote.refresh_from_db()
        line = invoice.lines.get()

        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertIsNone(invoice.number)
        self.assertEqual(invoice.quote_id, quote.pk)
        self.assertEqual(quote.status, Quote.Status.CONVERTED)
        self.assertEqual(invoice.customer_snapshot["name"], "Client historique")
        self.assertEqual(invoice.customer_snapshot["address"], "Ancienne adresse")
        self.assertEqual(line.description, "Accompagnement Power BI")
        self.assertEqual(line.quantity, Decimal("2.000"))
        self.assertEqual(line.unit_price, Decimal("1000.00"))
        self.assertEqual(line.discount_type, QuoteLine.DiscountType.PERCENTAGE)
        self.assertEqual(line.discount_value, Decimal("10.00"))
        self.assertEqual(line.tax_name, "TVA")
        self.assertEqual(line.tax_percentage, Decimal("20.00"))
        self.assertEqual(
            calculate_quote_totals(invoice.lines.all())["total"],
            Decimal("2160.00"),
        )
        self.assertTrue(
            DocumentHistory.objects.filter(
                quote=quote,
                snapshot__to_status=Quote.Status.CONVERTED,
                snapshot__invoice_id=invoice.pk,
            ).exists()
        )
        self.assertTrue(
            DocumentHistory.objects.filter(
                invoice=invoice,
                event=DocumentHistory.Event.CREATED,
            ).exists()
        )

    def test_conversion_rejects_unaccepted_and_already_converted_quotes(self):
        sent_quote = self._accepted_quote(Quote.Status.SENT)
        with self.assertRaises(ValidationError):
            convert_quote_to_invoice(sent_quote.pk, self.user)

        accepted_quote = self._accepted_quote()
        convert_quote_to_invoice(accepted_quote.pk, self.user)
        with self.assertRaises(ValidationError):
            convert_quote_to_invoice(accepted_quote.pk, self.user)
        self.assertEqual(Invoice.objects.filter(quote=accepted_quote).count(), 1)

    def test_conversion_view_requires_post_and_redirects_to_invoice(self):
        quote = self._accepted_quote()

        response = self.client.get(reverse("invoice-convert", args=[quote.pk]))
        self.assertEqual(response.status_code, 405)
        response = self.client.post(reverse("invoice-convert", args=[quote.pk]))
        invoice = Invoice.objects.get(quote=quote)
        self.assertRedirects(response, reverse("invoice-detail", args=[invoice.pk]))

    def test_invoice_numbering_is_fac_year_sequence_and_issue_is_historized(self):
        quote = self._accepted_quote()
        invoice = convert_quote_to_invoice(quote.pk, self.user)

        invoice = issue_invoice(invoice.pk, self.user)

        self.assertEqual(invoice.number, f"FAC-{timezone.localdate().year}-0001")
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(invoice.issue_date, timezone.localdate())
        self.assertTrue(
            DocumentHistory.objects.filter(
                invoice=invoice,
                event=DocumentHistory.Event.ISSUED,
                snapshot__to_status=Invoice.Status.ISSUED,
            ).exists()
        )

    def test_invoice_number_format_is_configurable(self):
        numbering = InvoiceNumberingSettings.objects.get(pk=1)
        numbering.prefix = "F"
        numbering.separator = "/"
        numbering.include_year = False
        numbering.sequence_width = 3
        numbering.save()
        invoice = convert_quote_to_invoice(self._accepted_quote().pk, self.user)

        invoice = issue_invoice(invoice.pk, self.user)

        self.assertEqual(invoice.number, "F/001")

    def test_invoice_without_lines_does_not_consume_a_number(self):
        invoice = Invoice.objects.create(customer=self.customer)
        with self.assertRaises(ValidationError):
            issue_invoice(invoice.pk, self.user)
        next_invoice = convert_quote_to_invoice(self._accepted_quote().pk, self.user)

        next_invoice = issue_invoice(next_invoice.pk, self.user)

        self.assertTrue(next_invoice.number.endswith("-0001"))

    def test_emitted_invoice_and_lines_are_immutable(self):
        invoice = convert_quote_to_invoice(self._accepted_quote().pk, self.user)
        issue_invoice(invoice.pk, self.user)
        invoice.refresh_from_db()
        invoice.notes = "Modification interdite"
        with self.assertRaises(ValidationError):
            invoice.save()
        line = invoice.lines.get()
        line.unit_price = Decimal("1.00")
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ValidationError):
            invoice.delete()

    def test_invoice_pdf_is_a4_and_draft_is_marked_unissued(self):
        invoice = convert_quote_to_invoice(self._accepted_quote().pk, self.user)

        response = self.client.get(reverse("invoice-pdf", args=[invoice.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(f"facture-brouillon-{invoice.pk}.pdf", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn(b"/MediaBox [ 0 0 595.2756 841.8898 ]", response.content)

    def test_invoice_pages_require_permissions(self):
        invoice = convert_quote_to_invoice(self._accepted_quote().pk, self.user)
        other = get_user_model().objects.create_user(username="invoice-no-permission")
        client = Client()
        client.force_login(other)

        self.assertEqual(client.get(reverse("invoice-list")).status_code, 403)
        self.assertEqual(client.get(reverse("invoice-detail", args=[invoice.pk])).status_code, 403)
        self.assertEqual(client.get(reverse("invoice-pdf", args=[invoice.pk])).status_code, 403)
        self.assertEqual(client.post(reverse("invoice-issue", args=[invoice.pk])).status_code, 403)

    def test_invoice_edit_draft_saves_due_date_and_notes_with_history(self):
        invoice = convert_quote_to_invoice(self._accepted_quote().pk, self.user)

        response = self.client.post(
            reverse("invoice-edit", args=[invoice.pk]),
            {"due_date": "30/11/2026", "notes": "Paiement par virement"},
        )

        self.assertRedirects(response, reverse("invoice-detail", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.due_date, date(2026, 11, 30))
        self.assertEqual(invoice.notes, "Paiement par virement")
        self.assertTrue(
            invoice.history.filter(
                event=DocumentHistory.Event.UPDATED,
                snapshot__after__notes="Paiement par virement",
            ).exists()
        )


class ConcurrentInvoiceNumberTests(TransactionTestCase):
    reset_sequences = True

    def test_simultaneous_issues_receive_distinct_numbers(self):
        customer = Customer.objects.create(name="Client facture concurrent")
        user = get_user_model().objects.create_user(username="invoice-concurrent")
        invoices = [
            Invoice.objects.create(customer=customer),
            Invoice.objects.create(customer=customer),
        ]
        for invoice in invoices:
            InvoiceLine.objects.create(
                invoice=invoice,
                description="Prestation",
                quantity=Decimal("1.000"),
                unit_price=Decimal("100.00"),
            )
        barrier = Barrier(len(invoices))

        def issue(invoice_id):
            close_old_connections()
            actor = get_user_model().objects.get(pk=user.pk)
            barrier.wait(timeout=10)
            number = issue_invoice(invoice_id, actor).number
            close_old_connections()
            return number

        with ThreadPoolExecutor(max_workers=2) as executor:
            numbers = list(executor.map(issue, [invoice.pk for invoice in invoices]))

        self.assertEqual(len(set(numbers)), 2)
        self.assertEqual(
            sorted(number.rsplit("-", 1)[1] for number in numbers),
            ["0001", "0002"],
        )

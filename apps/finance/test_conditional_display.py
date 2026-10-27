from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Service, ServiceCategory, TaxRate
from apps.partners.models import Customer
from .invoice_pdf import build_invoice_pdf
from .models import Invoice, InvoiceLine, Quote, QuoteLine
from .quote_pdf import build_quote_pdf


class ConditionalDisplayPDFAndDetailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="testuser",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name="Entreprise Alpha", email="alpha@example.com")
        self.category = ServiceCategory.objects.create(name="Informatique")
        self.tax_rate_20 = TaxRate.objects.create(name="TVA 20%", percentage=Decimal("20.00"), is_active=True)
        self.service = Service.objects.create(
            category=self.category,
            name="Développement Web",
            default_unit_price=Decimal("1000.00"),
        )

    def test_quote_without_valid_until_and_without_discount(self):
        quote = Quote.objects.create(customer=self.customer, valid_until=None)
        QuoteLine.objects.create(
            quote=quote,
            service=self.service,
            description="Service sans remise",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
            discount_type="percentage",
            discount_value=Decimal("0.00"),
            tax_rate=self.tax_rate_20,
            tax_name=self.tax_rate_20.name,
            tax_percentage=self.tax_rate_20.percentage,
        )
        # Test PDF output
        pdf_bytes = build_quote_pdf(quote)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

        # Test HTML detail view
        response = self.client.get(reverse("quote-detail", args=[quote.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Non définie")
        self.assertNotContains(response, "<th>Remise</th>")
        self.assertNotContains(response, "Total brut HT")
        self.assertContains(response, "Total HT")

    def test_quote_with_valid_until_and_with_discount(self):
        quote = Quote.objects.create(
            customer=self.customer,
            valid_until=date(2026, 12, 31),
        )
        QuoteLine.objects.create(
            quote=quote,
            service=self.service,
            description="Service avec remise",
            quantity=Decimal("2.000"),
            unit_price=Decimal("1000.00"),
            discount_type="percentage",
            discount_value=Decimal("10.00"),
            tax_rate=self.tax_rate_20,
            tax_name=self.tax_rate_20.name,
            tax_percentage=self.tax_rate_20.percentage,
        )
        # Test PDF output
        pdf_bytes = build_quote_pdf(quote)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

        # Test HTML detail view
        response = self.client.get(reverse("quote-detail", args=[quote.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "31/12/2026")
        self.assertContains(response, "<th>Remise</th>")
        self.assertContains(response, "Total brut HT")
        self.assertContains(response, "Remises")

    def test_invoice_without_due_date_and_without_discount(self):
        invoice = Invoice.objects.create(customer=self.customer, due_date=None)
        InvoiceLine.objects.create(
            invoice=invoice,
            service=self.service,
            description="Facturation standard",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
            discount_type="percentage",
            discount_value=Decimal("0.00"),
            tax_rate=self.tax_rate_20,
            tax_name=self.tax_rate_20.name,
            tax_percentage=self.tax_rate_20.percentage,
        )
        # Test PDF output
        pdf_bytes = build_invoice_pdf(invoice)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

        # Test HTML detail view
        response = self.client.get(reverse("invoice-detail", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Échéance : Non définie")
        self.assertNotContains(response, "<th>Remise</th>")
        self.assertNotContains(response, "Total brut HT")
        self.assertContains(response, "Total HT")

    def test_invoice_with_due_date_and_with_discount(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            due_date=date(2026, 11, 30),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            service=self.service,
            description="Facturation avec remise",
            quantity=Decimal("1.000"),
            unit_price=Decimal("500.00"),
            discount_type="fixed",
            discount_value=Decimal("50.00"),
            tax_rate=self.tax_rate_20,
            tax_name=self.tax_rate_20.name,
            tax_percentage=self.tax_rate_20.percentage,
        )
        # Test PDF output
        pdf_bytes = build_invoice_pdf(invoice)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

        # Test HTML detail view
        response = self.client.get(reverse("invoice-detail", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "30/11/2026")
        self.assertContains(response, "<th>Remise</th>")
        self.assertContains(response, "Total brut HT")
        self.assertContains(response, "Remises")

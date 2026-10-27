from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from PIL import Image

from apps.partners.models import Customer
from .invoice_pdf import build_invoice_pdf
from .invoice_services import issue_invoice
from .models import CompanySettings, Invoice, InvoiceLine, Quote
from .quote_pdf import build_quote_pdf


class CompanySettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="company-admin",
            password="testpassword123",
        )
        permissions = Permission.objects.filter(
            content_type__app_label="finance",
            codename__in=[
                "change_companysettings",
                "view_quote",
                "view_invoice",
                "change_invoice",
            ],
        )
        self.user.user_permissions.add(*permissions)
        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(name="Client Test Maroc")

    def _dummy_image(self):
        file_obj = BytesIO()
        image = Image.new("RGBA", (100, 50), (29, 78, 216, 255))
        image.save(file_obj, "PNG")
        file_obj.seek(0)
        return SimpleUploadedFile("logo.png", file_obj.read(), content_type="image/png")

    def test_company_settings_singleton_and_validation(self):
        company = CompanySettings.get_settings()
        self.assertEqual(company.pk, 1)

        # ICE validation
        company.ice = "001234567890001"
        company.full_clean()
        self.assertEqual(company.ice, "001234567890001")

        company.ice = "12345"  # invalid length
        with self.assertRaises(ValidationError):
            company.full_clean()

        # RIB validation
        company.ice = "001234567890001"
        company.rib = "007780000123456789012345"
        company.full_clean()
        self.assertEqual(company.rib, "007780000123456789012345")

        company.rib = "123"  # invalid
        with self.assertRaises(ValidationError):
            company.full_clean()

    def test_legal_notice_generation(self):
        company = CompanySettings.get_settings()
        company.name = "BENCHTECH INNOVATION"
        company.legal_form = "SARL"
        company.capital = Decimal("100000.00")
        company.ice = "001234567890001"
        company.tax_identifier = "12345678"
        company.trade_registry = "98765 Casablanca"
        company.patente = "445566"
        company.cnss = "1122334"
        company.save()

        notice = company.legal_notice()
        self.assertIn("BENCHTECH INNOVATION", notice)
        self.assertIn("SARL au capital de 100 000,00 MAD", notice)
        self.assertIn("ICE : 001234567890001", notice)
        self.assertIn("IF : 12345678", notice)
        self.assertIn("RC : 98765 Casablanca", notice)
        self.assertIn("Patente : 445566", notice)
        self.assertIn("CNSS : 1122334", notice)

    def test_company_settings_view_get_and_post(self):
        # GET
        response = self.client.get(reverse("company-settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paramètres de l'entreprise")

        # POST with logo and moroccan credentials
        logo = self._dummy_image()
        post_response = self.client.post(
            reverse("company-settings"),
            {
                "name": "BENCHTECH SOLUTIONS SARL",
                "tagline": "Prestations informatiques et formation",
                "legal_form": "SARL",
                "capital": "50000.00",
                "ice": "009988776655443",
                "tax_identifier": "87654321",
                "trade_registry": "112233 Rabat",
                "patente": "556677",
                "cnss": "9988776",
                "address": "12 avenue Mohammed V",
                "city": "Rabat",
                "postal_code": "10000",
                "country": "Maroc",
                "phone": "+212537000000",
                "email": "contact@benchtech.ma",
                "website": "www.benchtech.ma",
                "bank_name": "Attijariwafa bank",
                "rib": "007780000123456789012345",
                "logo": logo,
            },
        )
        self.assertRedirects(post_response, reverse("company-settings"))
        company = CompanySettings.get_settings()
        self.assertEqual(company.name, "BENCHTECH SOLUTIONS SARL")
        self.assertEqual(company.ice, "009988776655443")
        self.assertEqual(company.bank_name, "Attijariwafa bank")
        self.assertTrue(bool(company.logo))

    def test_pdf_rendering_with_company_settings(self):
        company = CompanySettings.get_settings()
        company.name = "BENCHTECH PRO"
        company.ice = "001122334455667"
        company.bank_name = "Banque Populaire"
        company.rib = "111222333444555666777888"
        company.logo = self._dummy_image()
        company.save()

        self.assertEqual(company.formatted_rib, "111 222 3334445556667778 88")

        # Test quote PDF
        quote = Quote.objects.create(customer=self.customer)
        quote_pdf_bytes = build_quote_pdf(quote)
        self.assertTrue(quote_pdf_bytes.startswith(b"%PDF-"))

        # Test invoice PDF
        invoice = Invoice.objects.create(customer=self.customer)
        InvoiceLine.objects.create(
            invoice=invoice,
            description="Prestation d'audit",
            quantity=Decimal("1.000"),
            unit_price=Decimal("2000.00"),
        )
        issue_invoice(invoice.pk, self.user)
        invoice.refresh_from_db()

        invoice_pdf_bytes = build_invoice_pdf(invoice)
        self.assertTrue(invoice_pdf_bytes.startswith(b"%PDF-"))

        # Test detail views display bank & formatted RIB
        quote_response = self.client.get(reverse("quote-detail", args=[quote.pk]))
        self.assertEqual(quote_response.status_code, 200)
        self.assertContains(quote_response, "Banque Populaire")
        self.assertContains(quote_response, "111 222 3334445556667778 88")

        invoice_response = self.client.get(reverse("invoice-detail", args=[invoice.pk]))
        self.assertEqual(invoice_response.status_code, 200)
        self.assertContains(invoice_response, "Banque Populaire")
        self.assertContains(invoice_response, "111 222 3334445556667778 88")


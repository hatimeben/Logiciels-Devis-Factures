from datetime import date
from decimal import Decimal
import io
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.finance.models import Invoice, Quote
from apps.finance.invoice_pdf import build_invoice_pdf
from apps.finance.invoice_docx import build_invoice_docx
from apps.finance.quote_pdf import build_quote_pdf
from apps.finance.quote_docx import build_quote_docx
from apps.partners.models import Customer


class CustomerIceDisplayTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="ice_admin", email="ice@test.com", password="password123"
        )
        self.client.force_login(self.user)

        self.customer_with_ice = Customer.objects.create(
            name="Entreprise Marocaine SARL",
            ice="002345678901234",
            tax_identifier="12345678",
            city="Casablanca",
            address="100 Boulevard d'Anfa",
        )
        self.customer_without_ice = Customer.objects.create(
            name="Client Sans ICE",
            ice="",
            city="Rabat",
        )

        self.today = date(2026, 11, 16)
        self.invoice = Invoice.objects.create(
            customer=self.customer_with_ice,
            issue_date=self.today,
            customer_snapshot={
                "name": self.customer_with_ice.name,
                "ice": self.customer_with_ice.ice,
                "tax_identifier": self.customer_with_ice.tax_identifier,
                "city": self.customer_with_ice.city,
                "address": self.customer_with_ice.address,
            },
        )
        self.invoice.lines.create(
            description="Prestation informatique",
            quantity=Decimal("1.00"),
            unit_price=Decimal("1000.00"),
            tax_percentage=Decimal("20.00"),
        )

        self.invoice_no_ice = Invoice.objects.create(
            customer=self.customer_without_ice,
            issue_date=self.today,
            customer_snapshot={
                "name": self.customer_without_ice.name,
                "ice": "",
                "city": self.customer_without_ice.city,
            },
        )
        self.invoice_no_ice.lines.create(
            description="Service maintenance",
            quantity=Decimal("1.00"),
            unit_price=Decimal("500.00"),
            tax_percentage=Decimal("20.00"),
        )

    def test_invoice_detail_shows_ice_badge_when_present(self):
        """Vérifie que la page détail affiche clairement l'ICE du client."""
        url = reverse("invoice-detail", args=[self.invoice.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "002345678901234")
        self.assertContains(resp, "ICE : 002345678901234")

    def test_invoice_detail_shows_warning_when_ice_missing(self):
        """Vérifie que l'alerte ICE manquant et le modal s'affichent quand le client n'a pas d'ICE."""
        url = reverse("invoice-detail", args=[self.invoice_no_ice.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ICE manquant")
        self.assertContains(resp, "edit-ice-modal")

    def test_invoice_docx_includes_customer_ice(self):
        """Vérifie que le document Word (.docx) contient l'ICE du client."""
        docx_bytes = build_invoice_docx(self.invoice)
        zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("002345678901234", doc_xml)
        self.assertIn("ICE :", doc_xml)

    def test_invoice_pdf_builds_without_error(self):
        """Vérifie que le PDF se compile sans erreur avec l'ICE."""
        pdf_bytes = build_invoice_pdf(self.invoice)
        self.assertGreater(len(pdf_bytes), 1000)

    def test_update_invoice_customer_ice_endpoint(self):
        """Vérifie que l'action rapide met à jour l'ICE du client et du snapshot."""
        url = reverse("invoice-update-ice", args=[self.invoice_no_ice.pk])
        resp = self.client.post(url, {"ice": "009988776655443"})
        self.assertRedirects(resp, reverse("invoice-detail", args=[self.invoice_no_ice.pk]))

        self.customer_without_ice.refresh_from_db()
        self.assertEqual(self.customer_without_ice.ice, "009988776655443")

        self.invoice_no_ice.refresh_from_db()
        self.assertEqual(self.invoice_no_ice.customer_snapshot.get("ice"), "009988776655443")

    def test_update_invoice_customer_ice_invalid_length(self):
        """Vérifie le rejet d'un ICE invalide (qui ne fait pas 15 chiffres)."""
        url = reverse("invoice-update-ice", args=[self.invoice_no_ice.pk])
        resp = self.client.post(url, {"ice": "12345"})  # trop court
        self.assertRedirects(resp, reverse("invoice-detail", args=[self.invoice_no_ice.pk]))

        self.customer_without_ice.refresh_from_db()
        self.assertEqual(self.customer_without_ice.ice, "")  # non modifié

    def test_quote_docx_and_pdf_with_ice(self):
        """Vérifie que les devis Word et PDF intègrent aussi l'ICE."""
        quote = Quote.objects.create(
            customer=self.customer_with_ice,
            issue_date=self.today,
            customer_snapshot={
                "name": self.customer_with_ice.name,
                "ice": self.customer_with_ice.ice,
            },
        )
        quote.lines.create(
            description="Devis test",
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00"),
        )
        pdf_bytes = build_quote_pdf(quote)
        self.assertGreater(len(pdf_bytes), 1000)

        docx_bytes = build_quote_docx(quote)
        zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("002345678901234", doc_xml)

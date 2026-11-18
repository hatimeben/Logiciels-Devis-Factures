from decimal import Decimal
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Article, Service, ServiceCategory
from apps.partners.models import Customer
from .backup_services import get_backup_dir, list_backups, perform_backup
from .models import Invoice, InvoiceLine, Quote, QuoteLine


class GlobalSearchAndBackupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="searchuser", password="password123"
        )
        perms = Permission.objects.filter(
            codename__in=[
                "view_invoice", "view_quote", "view_customer",
                "change_companysettings",
            ]
        )
        self.user.user_permissions.add(*perms)
        self.client = Client()
        self.client.login(username="searchuser", password="password123")

        # 1. Customer
        self.customer = Customer.objects.create(
            name="Alpha Technologies Maroc",
            city="Casablanca",
            ice="001122334455667",
        )

        # 2. Quote
        self.quote = Quote.objects.create(customer=self.customer)
        QuoteLine.objects.create(
            quote=self.quote,
            description="Audit infrastructure cloud Azure",
            quantity=Decimal("1.000"),
            unit_price=Decimal("5000.00"),
        )

        # 3. Invoice
        self.invoice = Invoice.objects.create(customer=self.customer)
        InvoiceLine.objects.create(
            invoice=self.invoice,
            description="Licences Power BI Pro annuelles",
            quantity=Decimal("5.000"),
            unit_price=Decimal("1200.00"),
        )

        # 4. Article
        self.article = Article.objects.create(
            designation="Serveur Dell PowerEdge R740",
            reference="DELL-R740-01",
            prix_vente_ht=Decimal("45000.00"),
            stock_actuel=Decimal("3.000"),
        )

        # 5. Service
        cat = ServiceCategory.objects.create(name="Formations")
        self.service = Service.objects.create(
            category=cat,
            name="Formation Python Django Avancée",
            default_unit_price=Decimal("6000.00"),
        )

    def test_search_requires_login(self):
        anonymous = Client()
        res = anonymous.get(reverse("global-search-api") + "?q=Alpha")
        self.assertEqual(res.status_code, 302)

    def test_search_short_query_returns_empty(self):
        res = self.client.get(reverse("global-search-api") + "?q=a")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["results"], [])

    def test_search_matches_customer(self):
        res = self.client.get(reverse("global-search-api") + "?q=Alpha")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(any(item["type"] == "client" and "Alpha" in item["title"] for item in data["results"]))

    def test_search_matches_quote_description(self):
        res = self.client.get(reverse("global-search-api") + "?q=Azure")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(any(item["type"] == "devis" for item in data["results"]))

    def test_search_matches_invoice_description(self):
        res = self.client.get(reverse("global-search-api") + "?q=Power BI")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(any(item["type"] == "facture" for item in data["results"]))

    def test_search_matches_article_designation(self):
        res = self.client.get(reverse("global-search-api") + "?q=PowerEdge")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(any(item["type"] == "stock" and "Dell" in item["title"] for item in data["results"]))

    def test_search_matches_service_name(self):
        res = self.client.get(reverse("global-search-api") + "?q=Python")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(any(item["type"] == "service" and "Python" in item["title"] for item in data["results"]))

    def test_perform_backup_creates_valid_zip(self):
        result = perform_backup(keep_days=7)
        self.assertTrue(result["success"])
        self.assertTrue(Path(result["archive_path"]).exists())
        self.assertTrue(result["filename"].endswith(".zip"))
        self.assertGreater(result["file_size"], 0)

        backups = list_backups()
        self.assertTrue(any(b["filename"] == result["filename"] for b in backups))

    def test_backup_create_view_and_download_view(self):
        # 1. Trigger backup POST
        res = self.client.post(reverse("backup-create"))
        self.assertRedirects(res, reverse("company-settings"))

        backups = list_backups()
        self.assertGreater(len(backups), 0)
        latest_filename = backups[0]["filename"]

        # 2. Download valid file
        dl_res = self.client.get(reverse("backup-download", args=[latest_filename]))
        self.assertEqual(dl_res.status_code, 200)
        self.assertIn("zip", dl_res["Content-Type"])

        # 3. Security: Non-matching backup name rejected
        bad_res = self.client.get(reverse("backup-download", args=["hack_backup.zip"]))
        self.assertEqual(bad_res.status_code, 403)

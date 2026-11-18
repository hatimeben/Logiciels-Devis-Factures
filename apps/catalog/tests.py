from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
import openpyxl

from apps.finance.accounting_services import (
    build_complete_fiduciary_pack_xlsx,
    get_moroccan_vat_report_data,
    get_payments_journal_data,
    get_sales_journal_data,
)
from apps.finance.invoice_services import issue_invoice
from apps.finance.models import Invoice, InvoiceLine, InvoiceNumberingSettings
from apps.partners.models import Customer, Supplier

from .models import Article, ExpenseCategory, MouvementStock, Service, ServiceCategory, TaxRate
from .stock_services import (
    decrementer_stock_depuis_facture,
    enregistrer_mouvement_stock,
    get_or_create_cash_customer,
)


class CatalogModelTests(TestCase):
    def test_custom_categories_services_and_tax_rates_are_persisted(self):
        expense_category = ExpenseCategory.objects.create(name="Déplacements")
        service_category = ServiceCategory.objects.create(name="Formation")
        service = Service.objects.create(
            category=service_category,
            name="Formation Excel",
            default_unit_price=Decimal("1250.50"),
        )
        tax_rate = TaxRate.objects.create(name="Taux paramétrable", percentage=Decimal("7.25"))

        self.assertEqual(expense_category.name, "Déplacements")
        self.assertEqual(service.category.name, "Formation")
        self.assertEqual(service.default_unit_price, Decimal("1250.50"))
        self.assertEqual(tax_rate.percentage, Decimal("7.25"))

    def test_service_name_is_unique_within_a_category(self):
        category = ServiceCategory.objects.create(name="Conseil")
        Service.objects.create(
            category=category,
            name="Audit",
            default_unit_price=Decimal("500.00"),
        )

        with self.assertRaises(ValidationError):
            Service.objects.create(
                category=category,
                name="Audit",
                default_unit_price=Decimal("500.00"),
            )

    def test_service_requires_a_default_sale_price(self):
        category = ServiceCategory.objects.create(name="Développement")

        with self.assertRaises(ValidationError):
            Service.objects.create(category=category, name="Application métier")


class StockManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="gestionnaire", password="pwd")
        self.client = Client()
        self.client.force_login(self.user)

        self.article = Article.objects.create(
            reference="ART-TEST-01",
            designation="Serveur Rack 1U",
            prix_vente_ht=Decimal("15000.00"),
            prix_achat_ht=Decimal("11000.00"),
            stock_actuel=Decimal("0.00"),
            stock_alerte=Decimal("2.00"),
            compte_stock="3111",
            compte_variation="6114",
        )

    def test_entree_stock_augmente_stock_et_cree_mouvement(self):
        mvt = enregistrer_mouvement_stock(
            article_id=self.article.pk,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=Decimal("8.00"),
            reference_document="BL-FOURN-001",
            motif="Réception fournisseur",
            auteur=self.user,
        )
        self.article.refresh_from_db()
        self.assertEqual(self.article.stock_actuel, Decimal("8.00"))
        self.assertEqual(mvt.stock_avant, Decimal("0.00"))
        self.assertEqual(mvt.stock_apres, Decimal("8.00"))
        self.assertFalse(self.article.en_rupture)
        self.assertFalse(self.article.sous_alerte)

    def test_sortie_stock_refusee_si_insuffisant(self):
        with self.assertRaises(ValidationError) as ctx:
            enregistrer_mouvement_stock(
                article_id=self.article.pk,
                type_mouvement=MouvementStock.TypeMouvement.SORTIE,
                quantite=Decimal("2.00"),
                autoriser_stock_negatif=False,
            )
        self.assertIn("Stock insuffisant", ctx.exception.messages[0])

    def test_sortie_stock_autorisee_et_alerte_declenchee(self):
        enregistrer_mouvement_stock(
            article_id=self.article.pk,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=Decimal("5.00"),
        )
        enregistrer_mouvement_stock(
            article_id=self.article.pk,
            type_mouvement=MouvementStock.TypeMouvement.SORTIE,
            quantite=Decimal("4.00"),
            reference_document="FAC-2026-0001",
        )
        self.article.refresh_from_db()
        self.assertEqual(self.article.stock_actuel, Decimal("1.00"))
        self.assertTrue(self.article.sous_alerte)
        self.assertFalse(self.article.en_rupture)

    def test_ajustement_inventaire(self):
        enregistrer_mouvement_stock(
            article_id=self.article.pk,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=Decimal("10.00"),
        )
        mvt = enregistrer_mouvement_stock(
            article_id=self.article.pk,
            type_mouvement=MouvementStock.TypeMouvement.AJUSTEMENT,
            quantite=Decimal("7.00"),
            motif="Inventaire physique trimestriel",
        )
        self.article.refresh_from_db()
        self.assertEqual(self.article.stock_actuel, Decimal("7.00"))
        self.assertEqual(mvt.stock_avant, Decimal("10.00"))
        self.assertEqual(mvt.stock_apres, Decimal("7.00"))

    def test_emission_facture_decremente_stock_automatiquement(self):
        InvoiceNumberingSettings.get_settings()
        customer = Customer.objects.create(name="Client Entreprise")

        # Approvisionnement initial de 10 unités
        enregistrer_mouvement_stock(
            article_id=self.article.pk,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=Decimal("10.00"),
        )

        invoice = Invoice.objects.create(customer=customer)
        InvoiceLine.objects.create(
            invoice=invoice,
            article=self.article,
            description=self.article.designation,
            quantity=Decimal("3.00"),
            unit_price=self.article.prix_vente_ht,
        )

        # Émission de la facture
        issued_invoice = issue_invoice(invoice.pk, self.user)
        self.assertEqual(issued_invoice.status, Invoice.Status.ISSUED)

        # Vérification de la décrémentation automatique
        self.article.refresh_from_db()
        self.assertEqual(self.article.stock_actuel, Decimal("7.00"))

        # Vérification du mouvement de stock créé
        mvt = MouvementStock.objects.get(reference_document=issued_invoice.number)
        self.assertEqual(mvt.type_mouvement, MouvementStock.TypeMouvement.SORTIE)
        self.assertEqual(mvt.quantite, Decimal("3.00"))
        self.assertEqual(mvt.stock_avant, Decimal("10.00"))
        self.assertEqual(mvt.stock_apres, Decimal("7.00"))

    def test_stock_dashboard_view(self):
        response = self.client.get(reverse("stock-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gestion des Stocks")
        self.assertContains(response, self.article.reference)

    def test_stock_reception_flow(self):
        supplier = Supplier.objects.create(name="Grossiste Informatique")
        response = self.client.post(
            reverse("stock-reception"),
            {
                "article": self.article.pk,
                "supplier": supplier.pk,
                "quantity": "15.00",
                "unit_price_ht": "10500.00",
                "reference_document": "BL-TEST-2026",
                "update_cost_price": "on",
                "notes": "Réception test",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.stock_actuel, Decimal("15.00"))
        self.assertEqual(self.article.prix_achat_ht, Decimal("10500.00"))

    def test_quick_cash_sale_view(self):
        response = self.client.get(reverse("stock-quick-cash-sale"))
        self.assertEqual(response.status_code, 302)
        # Vérifie qu'un client "Client Divers / Vente au Comptant" existe
        cash_customer = get_or_create_cash_customer()
        self.assertTrue(Invoice.objects.filter(customer=cash_customer).exists())

    def test_stock_movements_journal_view(self):
        enregistrer_mouvement_stock(
            article_id=self.article.pk,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=Decimal("4.00"),
            reference_document="BL-JOURNAL-01",
        )
        response = self.client.get(reverse("stock-movements"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BL-JOURNAL-01")

    def test_fiduciary_pack_includes_stock_sheet(self):
        enregistrer_mouvement_stock(
            article_id=self.article.pk,
            type_mouvement=MouvementStock.TypeMouvement.ENTREE,
            quantite=Decimal("10.00"),
        )
        vat_data = get_moroccan_vat_report_data(date(2026, 1, 1), date(2026, 12, 31), regime="encaissement")
        sales_data = get_sales_journal_data(date(2026, 1, 1), date(2026, 12, 31))
        payments_data = get_payments_journal_data(date(2026, 1, 1), date(2026, 12, 31))

        xlsx_bytes = build_complete_fiduciary_pack_xlsx("Année 2026", vat_data, sales_data, payments_data)
        wb = openpyxl.load_workbook(BytesIO(xlsx_bytes))
        self.assertIn("Inventaire & Stocks (3111)", wb.sheetnames)
        ws_stock = wb["Inventaire & Stocks (3111)"]
        self.assertEqual(ws_stock["A1"].value.split(" - ")[-1], "INVENTAIRE & VALORISATION DES STOCKS DE MARCHANDISES")


class IntegerQuantityValidationTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            reference="TEST-INT-01",
            designation="Serveur Test",
            prix_vente_ht=Decimal("15000.00"),
            stock_actuel=Decimal("10.00"),
        )

    def test_stock_reception_requires_integer_quantity(self):
        from apps.catalog.stock_forms import StockReceptionForm
        # Decimal fraction should fail
        form_invalid = StockReceptionForm(data={
            "article": self.article.pk,
            "quantity": "2.5",
            "reference_document": "BL-001",
        })
        self.assertFalse(form_invalid.is_valid())
        self.assertIn("quantity", form_invalid.errors)
        self.assertIn("nombre entier", form_invalid.errors["quantity"][0])

        # Integer should succeed
        form_valid = StockReceptionForm(data={
            "article": self.article.pk,
            "quantity": "3",
            "reference_document": "BL-001",
        })
        self.assertTrue(form_valid.is_valid())

    def test_invoice_and_quote_line_forms_require_integer_quantity(self):
        from apps.finance.invoice_forms import InvoiceLineForm
        from apps.finance.quote_forms import QuoteLineForm

        # InvoiceLineForm fraction rejection
        inv_form = InvoiceLineForm(data={
            "article": self.article.pk,
            "quantity": "1.75",
            "unit_price": "15000.00",
            "discount_type": "percentage",
            "discount_value": "0",
        })
        self.assertFalse(inv_form.is_valid())
        self.assertIn("quantity", inv_form.errors)
        self.assertIn("nombre entier", inv_form.errors["quantity"][0])

        # InvoiceLineForm valid integer
        inv_form_ok = InvoiceLineForm(data={
            "article": self.article.pk,
            "quantity": "2",
            "unit_price": "15000.00",
            "discount_type": "percentage",
            "discount_value": "0",
        })
        self.assertTrue(inv_form_ok.is_valid())

        # QuoteLineForm fraction rejection
        quote_form = QuoteLineForm(data={
            "article": self.article.pk,
            "quantity": "3.2",
            "unit_price": "15000.00",
            "discount_type": "percentage",
            "discount_value": "0",
        })
        self.assertFalse(quote_form.is_valid())
        self.assertIn("quantity", quote_form.errors)
        self.assertIn("nombre entier", quote_form.errors["quantity"][0])

        # QuoteLineForm valid integer
        quote_form_ok = QuoteLineForm(data={
            "article": self.article.pk,
            "quantity": "4",
            "unit_price": "15000.00",
            "discount_type": "percentage",
            "discount_value": "0",
        })
        self.assertTrue(quote_form_ok.is_valid())

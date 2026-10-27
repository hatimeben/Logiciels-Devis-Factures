from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Service, ServiceCategory, TaxRate
from apps.partners.models import Customer
from .analytics_services import (
    POLE_CONSEIL_IA,
    POLE_FORMATION,
    POLE_MATERIEL,
    classify_activity_pole,
    get_cashflow_and_aging_receivables,
    get_conversion_metrics,
    get_executive_dashboard_context,
    get_revenue_by_activity,
)
from .invoice_services import issue_invoice
from .payment_services import record_invoice_payment
from .models import Invoice, InvoiceLine, Payment, Quote, QuoteLine


class AnalyticsServicesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="bi-director",
            password="testpassword123",
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=["finance", "catalog", "partners"]
        )
        self.user.user_permissions.add(*perms)
        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(name="Groupe Maroc Performance")

        # Catégories de catalogue
        self.cat_ia = ServiceCategory.objects.create(name="Intelligence artificielle")
        self.cat_formation = ServiceCategory.objects.create(name="Formation informatique")
        self.cat_materiel = ServiceCategory.objects.create(name="Vente de matériel informatique")

        self.tax_20 = TaxRate.objects.create(name="TVA 20%", percentage=Decimal("20.00"), is_active=True)

        self.srv_ia = Service.objects.create(
            name="Développement d'agent IA",
            category=self.cat_ia,
            default_unit_price=Decimal("15000.00"),
        )
        self.srv_formation = Service.objects.create(
            name="Formation Microsoft Fabric & Power BI",
            category=self.cat_formation,
            default_unit_price=Decimal("8000.00"),
        )
        self.srv_materiel = Service.objects.create(
            name="Fourniture Serveur Dell PowerEdge",
            category=self.cat_materiel,
            default_unit_price=Decimal("35000.00"),
        )

    def test_classify_activity_pole(self):
        # 1. Conseil & IA
        pole_ia = classify_activity_pole(
            service_category_name="Intelligence artificielle",
            service_name="Architecture Agentique",
            description="Mission d'accompagnement IA",
        )
        self.assertEqual(pole_ia["key"], POLE_CONSEIL_IA["key"])

        # 2. Formation professionnelle
        pole_form = classify_activity_pole(
            service_category_name="Formation informatique",
            service_name="Formation Excel Avancé",
            description="Session de formation en présentiel",
        )
        self.assertEqual(pole_form["key"], POLE_FORMATION["key"])

        # 3. Vente de matériel informatique
        pole_mat = classify_activity_pole(
            service_category_name="Vente de matériel informatique",
            service_name="Serveur rackable",
            description="Fourniture de commutateur Switch Cisco",
        )
        self.assertEqual(pole_mat["key"], POLE_MATERIEL["key"])

    def test_revenue_by_activity_metrics(self):
        # Facture 1 : Conseil IA (15 000 MAD HT) + Formation (8 000 MAD HT)
        inv = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=inv,
            service=self.srv_ia,
            description="Mission Conseil IA",
            quantity=Decimal("1.000"),
            unit_price=Decimal("15000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        InvoiceLine.objects.create(
            invoice=inv,
            service=self.srv_formation,
            description="Session Formation",
            quantity=Decimal("1.000"),
            unit_price=Decimal("8000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        issue_invoice(inv.pk, self.user)

        # Facture 2 : Matériel IT (35 000 MAD HT)
        inv2 = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=inv2,
            service=self.srv_materiel,
            description="Serveur Data Center",
            quantity=Decimal("1.000"),
            unit_price=Decimal("35000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        issue_invoice(inv2.pk, self.user)

        revenue_data = get_revenue_by_activity()
        self.assertEqual(revenue_data["total_net_ht"], Decimal("58000.00"))
        self.assertEqual(revenue_data["total_ttc"], Decimal("69600.00"))

        poles_by_key = {p["key"]: p for p in revenue_data["poles"]}
        self.assertEqual(poles_by_key["conseil_ia"]["net_ht"], Decimal("15000.00"))
        self.assertEqual(poles_by_key["formation"]["net_ht"], Decimal("8000.00"))
        self.assertEqual(poles_by_key["materiel"]["net_ht"], Decimal("35000.00"))

        # Vérification des pourcentages
        # 15000 / 58000 ~ 25.9%
        # 8000 / 58000 ~ 13.8%
        # 35000 / 58000 ~ 60.3%
        self.assertAlmostEqual(poles_by_key["conseil_ia"]["percentage"], 25.9, delta=0.5)
        self.assertAlmostEqual(poles_by_key["formation"]["percentage"], 13.8, delta=0.5)
        self.assertAlmostEqual(poles_by_key["materiel"]["percentage"], 60.3, delta=0.5)

    def test_conversion_rate_metrics(self):
        # 4 devis émis : 2 convertis, 1 envoyé, 1 refusé
        # 1 devis brouillon (ne doit pas impacter le ratio de conversion des émis)
        q_draft = Quote.objects.create(customer=self.customer, status=Quote.Status.DRAFT)

        q_sent = Quote.objects.create(customer=self.customer)
        Quote.objects.filter(pk=q_sent.pk).update(status=Quote.Status.SENT, number="DEV-2026-0010")

        q_conv1 = Quote.objects.create(customer=self.customer)
        Quote.objects.filter(pk=q_conv1.pk).update(status=Quote.Status.CONVERTED, number="DEV-2026-0011")

        q_conv2 = Quote.objects.create(customer=self.customer)
        Quote.objects.filter(pk=q_conv2.pk).update(status=Quote.Status.CONVERTED, number="DEV-2026-0012")

        q_rej = Quote.objects.create(customer=self.customer)
        Quote.objects.filter(pk=q_rej.pk).update(status=Quote.Status.REJECTED, number="DEV-2026-0013")

        metrics = get_conversion_metrics()
        self.assertEqual(metrics["total_quotes_count"], 5)
        self.assertEqual(metrics["issued_count"], 4)
        self.assertEqual(metrics["converted_count"], 2)
        # 2 convertis / 4 émis = 50.0%
        self.assertEqual(metrics["conversion_rate"], 50.0)
        self.assertEqual(metrics["negotiation_count"], 1)
        self.assertEqual(metrics["rejected_count"], 1)
        self.assertEqual(metrics["draft_count"], 1)

    def test_cashflow_and_aging_receivables_metrics(self):
        today = date(2026, 10, 27)

        # Facture 1 : Saine (échéance dans le futur : +10 jours), 6000 MAD TTC, 2000 MAD réglés, reste 4000
        inv_healthy = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            due_date=today + timedelta(days=10),
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=inv_healthy,
            description="Prestation Saine",
            quantity=Decimal("1.000"),
            unit_price=Decimal("5000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        issue_invoice(inv_healthy.pk, self.user)
        record_invoice_payment(
            invoice_id=inv_healthy.pk,
            amount=Decimal("2000.00"),
            payment_date=today,
            method=Payment.Method.BANK_TRANSFER,
            actor=self.user,
        )

        # Facture 2 : Échue (retard de 45 jours), 1200 MAD TTC, non réglée
        inv_overdue = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
            due_date=today - timedelta(days=45),
            customer_snapshot={"name": self.customer.name},
        )
        InvoiceLine.objects.create(
            invoice=inv_overdue,
            description="Prestation Échue",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        issue_invoice(inv_overdue.pk, self.user)

        cashflow = get_cashflow_and_aging_receivables(today=today)

        # Total TTC facturé : 6000 + 1200 = 7200 MAD
        self.assertEqual(cashflow["total_invoiced_ttc"], Decimal("7200.00"))
        # Total réglé : 2000 MAD
        self.assertEqual(cashflow["total_collected_ttc"], Decimal("2000.00"))
        # Reste dû total : 4000 + 1200 = 5200 MAD
        self.assertEqual(cashflow["total_remaining_due"], Decimal("5200.00"))

        # Créances saines : 4000 MAD
        self.assertEqual(cashflow["healthy_total"], Decimal("4000.00"))
        self.assertEqual(cashflow["healthy_count"], 1)

        # Créances échues : 1200 MAD
        self.assertEqual(cashflow["overdue_total"], Decimal("1200.00"))
        self.assertEqual(cashflow["overdue_count"], 1)

        # Retard de 45 jours -> Tranche 30-60 jours
        self.assertEqual(cashflow["aging_30_60"], Decimal("1200.00"))
        self.assertEqual(cashflow["aging_less_30"], Decimal("0.00"))
        self.assertEqual(cashflow["aging_over_60"], Decimal("0.00"))

    def test_executive_dashboard_view_renders_all_kpis_and_slogan(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode("utf-8")
        # Titre et slogan officiel
        self.assertIn("Tableau de Bord Décisionnel", content)
        self.assertIn("shaping tomorrow, today", content)

        # Bloc 1 : Lignes d'activité
        self.assertIn("Ligne d'Activité", content)
        self.assertIn("Conseil en technologies &amp; IA", content)
        self.assertIn("Formation professionnelle en informatique", content)
        self.assertIn("Vente de matériel informatique", content)

        # Bloc 2 : Conversion
        self.assertIn("Taux de Conversion Commercial", content)
        self.assertIn("En cours de négociation", content)

        # Bloc 3 : Trésorerie & Encours
        self.assertIn("Encours Clients", content)
        self.assertIn("Créances Saines", content)
        self.assertIn("Créances Échues", content)
        self.assertIn("Balance Âgée des Créances Échues", content)

    def test_root_url_redirects_to_executive_dashboard(self):
        response = self.client.get("/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain, [("/tableau-de-bord/", 302)])

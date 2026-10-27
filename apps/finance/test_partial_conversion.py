from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Service, ServiceCategory, TaxRate
from apps.partners.models import Customer
from .invoice_services import convert_quote_to_invoice, issue_invoice
from .models import Invoice, InvoiceLine, Quote, QuoteLine


class PartialConversionAndAdjustmentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="commercial-lead",
            password="testpassword123",
        )
        perms = Permission.objects.filter(
            content_type__app_label__in=["finance", "catalog", "partners"]
        )
        self.user.user_permissions.add(*perms)
        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(name="Groupe Maroc Industrie")
        self.cat = ServiceCategory.objects.create(name="Matériel & Réseau")
        self.tax_20 = TaxRate.objects.create(name="TVA 20%", percentage=Decimal("20.00"), is_active=True)

        self.srv_serveur = Service.objects.create(
            name="Serveur Dell R750",
            category=self.cat,
            default_unit_price=Decimal("40000.00"),
        )
        self.srv_switch = Service.objects.create(
            name="Switch Cisco 48 ports",
            category=self.cat,
            default_unit_price=Decimal("12000.00"),
        )

    def _create_multi_item_accepted_quote(self):
        """Devis de 5 Serveurs et 10 Switches, accepté."""
        quote = Quote.objects.create(
            customer=self.customer,
            status=Quote.Status.DRAFT,
            customer_snapshot={"name": self.customer.name},
        )
        QuoteLine.objects.create(
            quote=quote,
            service=self.srv_serveur,
            description="Serveur Dell R750 rackable",
            quantity=Decimal("5.000"),
            unit_price=Decimal("40000.00"),
            tax_rate=self.tax_20,
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        QuoteLine.objects.create(
            quote=quote,
            service=self.srv_switch,
            description="Switch Cisco 48 ports PoE",
            quantity=Decimal("10.000"),
            unit_price=Decimal("12000.00"),
            tax_rate=self.tax_20,
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )
        Quote.objects.filter(pk=quote.pk).update(
            status=Quote.Status.ACCEPTED,
            number="DEV-2026-0100",
            issue_date=timezone.localdate(),
        )
        quote.refresh_from_db()
        return quote

    def test_partial_conversion_sequence_1_devis_to_n_factures(self):
        """
        1 Devis -> N Factures échelonnées :
        Tranche 1 (Acompte / Livraison 1) : 2 Serveurs et 4 Switches.
        Tranche 2 (Livraison intermédiaire) : 2 Serveurs et 3 Switches.
        Tranche 3 (Solde final) : 1 Serveur et 3 Switches.
        """
        quote = self._create_multi_item_accepted_quote()
        line_serveur, line_switch = list(quote.lines.order_by("id"))

        # --- TRANCHE 1 : Facturation partielle 1 ---
        inv1 = convert_quote_to_invoice(
            quote.pk,
            self.user,
            line_quantities={
                line_serveur.pk: Decimal("2.000"),
                line_switch.pk: Decimal("4.000"),
            },
        )
        quote.refresh_from_db()
        self.assertEqual(quote.status, Quote.Status.PARTIALLY_INVOICED)
        self.assertEqual(inv1.status, Invoice.Status.DRAFT)
        self.assertEqual(inv1.lines.count(), 2)

        line_serveur.refresh_from_db()
        line_switch.refresh_from_db()
        self.assertEqual(line_serveur.invoiced_quantity, Decimal("2.000"))
        self.assertEqual(line_serveur.remaining_quantity, Decimal("3.000"))
        self.assertEqual(line_switch.invoiced_quantity, Decimal("4.000"))
        self.assertEqual(line_switch.remaining_quantity, Decimal("6.000"))

        # Émission officielle de la Facture 1 (CGI Maroc)
        issue_invoice(inv1.pk, self.user)
        inv1.refresh_from_db()
        self.assertEqual(inv1.status, Invoice.Status.ISSUED)
        self.assertEqual(inv1.number, f"FAC-{timezone.localdate().year}-0001")

        # --- TRANCHE 2 : Facturation partielle 2 ---
        inv2 = convert_quote_to_invoice(
            quote.pk,
            self.user,
            line_quantities={
                line_serveur.pk: Decimal("2.000"),
                line_switch.pk: Decimal("3.000"),
            },
        )
        quote.refresh_from_db()
        self.assertEqual(quote.status, Quote.Status.PARTIALLY_INVOICED)

        line_serveur.refresh_from_db()
        line_switch.refresh_from_db()
        self.assertEqual(line_serveur.invoiced_quantity, Decimal("4.000"))
        self.assertEqual(line_serveur.remaining_quantity, Decimal("1.000"))
        self.assertEqual(line_switch.invoiced_quantity, Decimal("7.000"))
        self.assertEqual(line_switch.remaining_quantity, Decimal("3.000"))

        # Émission officielle Facture 2
        issue_invoice(inv2.pk, self.user)
        inv2.refresh_from_db()
        self.assertEqual(inv2.status, Invoice.Status.ISSUED)
        self.assertEqual(inv2.number, f"FAC-{timezone.localdate().year}-0002")

        # --- TRANCHE 3 : Solde résiduel automatique ---
        # Sans spécifier de quantités, le système facture exactement le reliquat (1 Serveur et 3 Switches)
        inv3 = convert_quote_to_invoice(quote.pk, self.user)
        quote.refresh_from_db()

        self.assertEqual(quote.status, Quote.Status.CONVERTED)  # Totalement facturé
        self.assertTrue(quote.is_fully_invoiced)

        line_serveur.refresh_from_db()
        line_switch.refresh_from_db()
        self.assertEqual(line_serveur.invoiced_quantity, Decimal("5.000"))
        self.assertEqual(line_serveur.remaining_quantity, Decimal("0.000"))
        self.assertEqual(line_switch.invoiced_quantity, Decimal("10.000"))
        self.assertEqual(line_switch.remaining_quantity, Decimal("0.000"))

        issue_invoice(inv3.pk, self.user)
        inv3.refresh_from_db()
        self.assertEqual(inv3.number, f"FAC-{timezone.localdate().year}-0003")

        # Le devis est désormais lié aux 3 factures distinctes
        self.assertEqual(quote.invoices.count(), 3)
        self.assertListEqual(
            list(quote.invoices.order_by("number").values_list("number", flat=True)),
            [
                f"FAC-{timezone.localdate().year}-0001",
                f"FAC-{timezone.localdate().year}-0002",
                f"FAC-{timezone.localdate().year}-0003",
            ],
        )

        # Une 4ème tentative de conversion doit être strictement rejetée
        with self.assertRaises(ValidationError) as ctx:
            convert_quote_to_invoice(quote.pk, self.user)
        self.assertIn("intégralement facturées", str(ctx.exception))

    def test_over_invoicing_is_strictly_prevented(self):
        """Contrôle anti-dépassement : impossible de facturer plus que la quantité devisée."""
        quote = self._create_multi_item_accepted_quote()
        line_serveur = quote.lines.first()

        # Demande de 6 serveurs alors qu'il n'y en a que 5 sur le devis
        with self.assertRaises(ValidationError) as ctx:
            convert_quote_to_invoice(
                quote.pk,
                self.user,
                line_quantities={line_serveur.pk: Decimal("6.000")},
            )
        self.assertIn("dépasse la quantité restante", str(ctx.exception))

    def test_adjustment_and_additional_items_on_draft_invoice(self):
        """
        Regroupement ou Ajustement :
        Création d'une facture depuis le devis, puis ajout d'un article hors devis
        (ex: câblage réseau supplémentaire) sur le brouillon avant émission.
        """
        quote = self._create_multi_item_accepted_quote()
        inv = convert_quote_to_invoice(quote.pk, self.user)
        self.assertEqual(inv.status, Invoice.Status.DRAFT)

        # Ajout d'une prestation d'installation non présente sur le devis
        extra_line = InvoiceLine.objects.create(
            invoice=inv,
            quote_line=None,  # Prestation additionnelle non devisée
            description="Prestation d'installation et câblage en baie",
            quantity=Decimal("1.000"),
            unit_price=Decimal("5000.00"),
            tax_rate=self.tax_20,
            tax_percentage=Decimal("20.00"),
            tax_name="TVA 20%",
        )

        # La facture conserve son lien logique avec le devis source
        self.assertEqual(inv.quote_id, quote.pk)
        self.assertEqual(inv.lines.count(), 3)  # 2 devisées + 1 additionnelle

        # Émission officielle
        issue_invoice(inv.pk, self.user)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.Status.ISSUED)
        self.assertTrue(inv.number.startswith("FAC-"))

    def test_quote_detail_view_shows_partial_conversion_options_and_linked_invoices(self):
        quote = self._create_multi_item_accepted_quote()
        # GET devis
        resp = self.client.get(reverse("quote-detail", args=[quote.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Facturation partielle")
        self.assertContains(resp, "Convertir en facture")

        # POST conversion partielle via web form
        line_serveur = quote.lines.first()
        post_data = {
            "is_partial": "1",
            f"qty_{line_serveur.pk}": "2.000",
        }
        post_resp = self.client.post(reverse("invoice-convert", args=[quote.pk]), post_data)
        self.assertEqual(post_resp.status_code, 302)

        quote.refresh_from_db()
        self.assertEqual(quote.status, Quote.Status.PARTIALLY_INVOICED)

        # Re-visite du devis : affiche la facture liée et le solde restant
        detail_resp = self.client.get(reverse("quote-detail", args=[quote.pk]))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "Factures générées depuis ce devis")
        self.assertContains(detail_resp, "Facturation en cours")
        self.assertContains(detail_resp, "Facturer tout le solde")


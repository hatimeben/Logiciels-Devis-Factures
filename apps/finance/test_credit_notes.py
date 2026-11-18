from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Article, MouvementStock, Service, ServiceCategory, TaxRate
from apps.partners.models import Customer
from .credit_note_services import create_credit_note_draft, issue_credit_note
from .invoice_services import issue_invoice
from .models import (
    CreditNote,
    CreditNoteLine,
    CreditNoteNumberingSettings,
    Invoice,
    InvoiceLine,
)


class CreditNoteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="test-credit-user")
        permissions = Permission.objects.filter(
            content_type__app_label__in=["finance", "catalog", "partners"],
        )
        self.user.user_permissions.add(*permissions)
        self.client = Client()
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(
            name="Société Alpha Maroc",
            email="contact@alpha.ma",
            ice="001234567890001",
            city="Casablanca",
        )
        self.tax20 = TaxRate.objects.create(name="TVA 20%", percentage=Decimal("20.00"))
        self.article = Article.objects.create(
            reference="ECR-DELL-24",
            designation="Écran Dell 24 pouces IPS",
            prix_achat_ht=Decimal("1200.00"),
            prix_vente_ht=Decimal("1800.00"),
            stock_actuel=Decimal("10.000"),
            stock_alerte=Decimal("2.000"),
        )
        cat_service = ServiceCategory.objects.create(name="Prestations")
        self.service = Service.objects.create(
            category=cat_service,
            name="Installation & Configuration",
            default_unit_price=Decimal("500.00"),
        )

        # Créer et émettre une facture avec 5 écrans et 2 installations
        self.invoice = Invoice.objects.create(
            customer=self.customer,
            status=Invoice.Status.DRAFT,
        )
        self.line_article = InvoiceLine.objects.create(
            invoice=self.invoice,
            article=self.article,
            description="Écran Dell 24 pouces IPS",
            quantity=Decimal("5.000"),
            unit_price=Decimal("1800.00"),
            tax_rate=self.tax20,
            position=1,
        )
        self.line_service = InvoiceLine.objects.create(
            invoice=self.invoice,
            service=self.service,
            description="Installation & Configuration",
            quantity=Decimal("2.000"),
            unit_price=Decimal("500.00"),
            tax_rate=self.tax20,
            position=2,
        )
        issue_invoice(self.invoice.pk, actor=self.user)
        self.invoice.refresh_from_db()
        self.article.refresh_from_db()
        # Le stock est passé de 10 à 5 suite à l'émission de la facture
        self.assertEqual(self.article.stock_actuel, Decimal("5.000"))

    def test_credit_note_draft_partial(self):
        """Création d'un avoir brouillon pour retour partiel (2 écrans sur 5)."""
        credit_note = create_credit_note_draft(
            invoice_id=self.invoice.pk,
            line_data={self.line_article.pk: Decimal("2.000")},
            reason=CreditNote.Reason.RETURN,
            reason_details="2 écrans avec carton endommagé",
            actor=self.user,
        )

        self.assertEqual(credit_note.status, CreditNote.Status.DRAFT)
        self.assertIsNone(credit_note.number)
        self.assertEqual(credit_note.lines.count(), 1)
        cn_line = credit_note.lines.first()
        self.assertEqual(cn_line.quantity, Decimal("2.000"))
        self.assertEqual(cn_line.unit_price, Decimal("1800.00"))

        # Vérifier les calculs de totaux
        # 2 * 1800 = 3600 HT, TVA 20% = 720, Total TTC = 4320
        self.assertEqual(credit_note.total_net, Decimal("3600.00"))
        self.assertEqual(credit_note.total_ttc, Decimal("4320.00"))

        # Vérifier que le stock n'est pas encore modifié en brouillon
        self.article.refresh_from_db()
        self.assertEqual(self.article.stock_actuel, Decimal("5.000"))

    def test_cannot_exceed_creditable_quantity(self):
        """Impossible de créditer plus que la quantité restante."""
        with self.assertRaises(ValidationError):
            create_credit_note_draft(
                invoice_id=self.invoice.pk,
                line_data={self.line_article.pk: Decimal("6.000")},
                reason=CreditNote.Reason.RETURN,
                actor=self.user,
            )

    def test_issue_credit_note_reintegrates_stock_and_assigns_number(self):
        """L'émission officielle attribue un numéro séquentiel et réintègre le stock."""
        credit_note = create_credit_note_draft(
            invoice_id=self.invoice.pk,
            line_data={
                self.line_article.pk: Decimal("2.000"),
                self.line_service.pk: Decimal("1.000"),
            },
            reason=CreditNote.Reason.RETURN,
            actor=self.user,
        )

        issued_cn = issue_credit_note(credit_note.pk, actor=self.user)
        self.assertEqual(issued_cn.status, CreditNote.Status.ISSUED)
        self.assertTrue(issued_cn.number.startswith("AVO-"))
        self.assertIsNotNone(issued_cn.issue_date)

        # Vérification réintégration stock : 5 + 2 = 7
        self.article.refresh_from_db()
        self.assertEqual(self.article.stock_actuel, Decimal("7.000"))

        # Vérification journal mouvement stock
        mouvement = MouvementStock.objects.filter(article=self.article, type_mouvement="ENTREE").first()
        self.assertIsNotNone(mouvement)
        self.assertEqual(mouvement.quantite, Decimal("2.000"))
        self.assertIn(issued_cn.number, mouvement.reference_document)

        # Vérification de remaining_creditable_quantity sur la ligne facture
        self.line_article.refresh_from_db()
        self.assertEqual(self.line_article.credited_quantity, Decimal("2.000"))
        self.assertEqual(self.line_article.remaining_creditable_quantity, Decimal("3.000"))

        # Vérification du solde de la facture
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.total_credited, issued_cn.total_ttc)

    def test_credit_note_views_and_exports(self):
        """Test des routes de formulaire, consultation, PDF et Word."""
        # 1. GET sur le formulaire de création
        resp = self.client.get(reverse("credit-note-create", args=[self.invoice.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Écran Dell 24 pouces IPS")

        # 2. POST pour créer l'avoir
        post_data = {
            f"qty_{self.line_article.pk}": "1",
            "reason": CreditNote.Reason.BILLING_ERROR,
            "reason_details": "Erreur sur quantité livrée",
            "notes": "Avoir à déduire sur prochaine commande",
        }
        resp = self.client.post(reverse("credit-note-create", args=[self.invoice.pk]), data=post_data)
        self.assertEqual(resp.status_code, 302)

        cn = CreditNote.objects.first()
        self.assertIsNotNone(cn)

        # 3. GET sur la vue détail
        resp_detail = self.client.get(reverse("credit-note-detail", args=[cn.pk]))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, "Écran Dell 24 pouces IPS")

        # 4. Émettre l'avoir via POST
        resp_issue = self.client.post(reverse("credit-note-issue", args=[cn.pk]))
        self.assertEqual(resp_issue.status_code, 302)
        cn.refresh_from_db()
        self.assertEqual(cn.status, CreditNote.Status.ISSUED)

        # 5. Télécharger le PDF
        resp_pdf = self.client.get(reverse("credit-note-pdf", args=[cn.pk]))
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf["Content-Type"], "application/pdf")

        # 6. Télécharger le Word (.docx)
        resp_docx = self.client.get(reverse("credit-note-docx", args=[cn.pk]))
        self.assertEqual(resp_docx.status_code, 200)
        self.assertIn("wordprocessingml", resp_docx["Content-Type"])

        # 7. Recherche globale Ctrl + K
        resp_search = self.client.get(reverse("global-search-api") + f"?q={cn.number[:4]}")
        self.assertEqual(resp_search.status_code, 200)
        data = resp_search.json()
        self.assertTrue(any(r["type"] == "avoir" for r in data["results"]))

from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
import openpyxl

from apps.catalog.models import ExpenseCategory
from apps.partners.models import Supplier
from .accounting_services import (
    build_complete_fiduciary_pack_xlsx,
    get_accounting_period_range,
    get_moroccan_vat_report_data,
    get_non_deductible_expenses_data,
    get_payments_journal_data,
    get_sales_journal_data,
)
from .expense_forms import ExpenseForm
from .models import Expense, ExpensePayment


class NonDeductibleExpensesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="fiscal_user",
            password="secretpassword",
            is_staff=True,
        )
        for perm_code in ["view_expense", "add_expense", "change_expense", "view_invoice"]:
            perm = Permission.objects.get(codename=perm_code)
            self.user.user_permissions.add(perm)
        self.client.force_login(self.user)

        self.category = ExpenseCategory.objects.create(name="Frais administratifs et amendes")
        self.supplier = Supplier.objects.create(
            name="Trésorerie Générale du Royaume",
            ice="000000000000001",
        )

    def test_full_non_deductible_expense(self):
        """Une amende 100% non déductible réintègre la totalité du montant."""
        expense = Expense.objects.create(
            category=self.category,
            supplier=self.supplier,
            description="Amende de circulation & pénalité fiscale",
            expense_date=date(2026, 5, 10),
            amount=Decimal("1500.00"),
            is_tax_deductible=False,
            non_deductible_reason=Expense.NonDeductibleReason.FINES_PENALTIES,
            fiscal_notes="Amende non déductible en vertu de l'art. 11-I du CGI",
        )
        self.assertTrue(expense.has_fiscal_reintegration)
        self.assertEqual(expense.fiscal_reintegration_amount, Decimal("1500.00"))
        self.assertIn("Art. 11-I CGI", expense.effective_reintegration_reason)

    def test_partial_non_deductible_expense(self):
        """Une charge partiellement déductible (ex: cadeau client ou quote-part non justifiée)."""
        expense = Expense.objects.create(
            category=self.category,
            supplier=self.supplier,
            description="Cadeaux fin d'année clients",
            expense_date=date(2026, 12, 15),
            amount=Decimal("10000.00"),
            is_tax_deductible=False,
            non_deductible_reason=Expense.NonDeductibleReason.CLIENT_GIFTS,
            non_deductible_amount=Decimal("4000.00"),
            fiscal_notes="4 000 DH de cadeaux ne comportent pas le logo de la société",
        )
        self.assertTrue(expense.has_fiscal_reintegration)
        self.assertEqual(expense.fiscal_reintegration_amount, Decimal("4000.00"))

    def test_validation_non_deductible_amount_cannot_exceed_total(self):
        """Le montant non déductible ne peut pas dépasser le montant total de la dépense."""
        expense = Expense(
            category=self.category,
            description="Test incohérent",
            expense_date=date(2026, 6, 1),
            amount=Decimal("1000.00"),
            is_tax_deductible=False,
            non_deductible_amount=Decimal("1500.00"),
        )
        with self.assertRaises(ValidationError):
            expense.full_clean()

    def test_cash_payment_over_ceiling_auto_reintegration(self):
        """Un règlement en espèces de 8 000 MAD déclenche l'excédent non déductible de 3 000 MAD (Art. 11-II CGI)."""
        expense = Expense.objects.create(
            category=self.category,
            supplier=self.supplier,
            description="Achat comptant quincaillerie",
            expense_date=date(2026, 4, 15),
            amount=Decimal("8000.00"),
            is_tax_deductible=True,  # Marqué déductible a priori
        )
        ExpensePayment.objects.create(
            expense=expense,
            payment_date=date(2026, 4, 15),
            amount=Decimal("8000.00"),
            method=ExpensePayment.Method.CASH,
        )

        expense.refresh_from_db()
        self.assertTrue(expense.has_cash_over_ceiling)
        self.assertEqual(expense.total_cash_excess, Decimal("3000.00"))
        # Même si is_tax_deductible était True, l'excédent espèces génère une réintégration fiscale automatique
        self.assertTrue(expense.has_fiscal_reintegration)
        self.assertEqual(expense.fiscal_reintegration_amount, Decimal("3000.00"))
        self.assertIn("Art. 11-II CGI", expense.effective_reintegration_reason)

    def test_get_non_deductible_expenses_data_aggregation(self):
        """Agrégation des données pour la liasse fiscale (Tableau N°3 IS)."""
        # 1. Dépense 100% amende : 1 500 DH
        Expense.objects.create(
            category=self.category,
            description="Pénalité CNSS",
            expense_date=date(2026, 3, 10),
            amount=Decimal("1500.00"),
            is_tax_deductible=False,
            non_deductible_reason=Expense.NonDeductibleReason.FINES_PENALTIES,
        )
        # 2. Dépense avec excédent espèces : 7 000 DH (excédent 2 000 DH)
        exp_cash = Expense.objects.create(
            category=self.category,
            description="Fournitures payées en espèces",
            expense_date=date(2026, 3, 20),
            amount=Decimal("7000.00"),
            is_tax_deductible=True,
        )
        ExpensePayment.objects.create(
            expense=exp_cash,
            payment_date=date(2026, 3, 20),
            amount=Decimal("7000.00"),
            method=ExpensePayment.Method.CASH,
        )

        data = get_non_deductible_expenses_data(date(2026, 1, 1), date(2026, 3, 31))
        self.assertEqual(data["totals"]["count"], 2)
        # Total comptabilisé : 1500 + 7000 = 8500
        self.assertEqual(data["totals"]["total_recorded"], Decimal("8500.00"))
        # Total réintégration : 1500 + 2000 = 3500
        self.assertEqual(data["totals"]["total_reintegration"], Decimal("3500.00"))
        self.assertTrue(len(data["summary_reasons"]) >= 2)

    def test_fiduciary_pack_includes_reintegration_sheet(self):
        """Le Pack Fiduciaire Excel contient l'onglet 'Réintégrations IS (Tab 3)'."""
        Expense.objects.create(
            category=self.category,
            description="Amende radar",
            expense_date=date(2026, 2, 5),
            amount=Decimal("300.00"),
            is_tax_deductible=False,
            non_deductible_reason=Expense.NonDeductibleReason.FINES_PENALTIES,
        )
        start_date, end_date, period_label = get_accounting_period_range(2026, "quarter", 1)
        vat_data = get_moroccan_vat_report_data(start_date, end_date)
        sales_data = get_sales_journal_data(start_date, end_date)
        payments_data = get_payments_journal_data(start_date, end_date)
        non_deductible_data = get_non_deductible_expenses_data(start_date, end_date)

        excel_bytes = build_complete_fiduciary_pack_xlsx(
            period_label, vat_data, sales_data, payments_data, non_deductible_data=non_deductible_data
        )
        wb = openpyxl.load_workbook(BytesIO(excel_bytes))
        self.assertIn("Réintégrations IS (Tab 3)", wb.sheetnames)
        ws = wb["Réintégrations IS (Tab 3)"]
        self.assertIn("TABLEAU N°3", ws["A1"].value)

    def test_reintegration_csv_export_endpoint(self):
        """L'URL d'export CSV des réintégrations fiscales fonctionne correctement."""
        url = reverse("accounting-export-reintegration-csv") + "?year=2026&period_type=year&period_value=1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8-sig")

    def test_dashboard_reintegrations_tab(self):
        """L'onglet 'reintegrations' s'affiche sur le tableau de bord comptable."""
        url = reverse("accounting-dashboard") + "?year=2026&period_type=year&period_value=1&tab=reintegrations"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "État des charges non déductibles")
        self.assertEqual(response.context["active_tab"], "reintegrations")

    def test_auto_detection_by_category(self):
        """Si la catégorie est configurée non-déductible par défaut (ex: Amendes), le formulaire applique la règle automatiquement."""
        cat_fines = ExpenseCategory.objects.create(
            name="Amendes et sanctions pénales",
            pcge_account="6583",
            is_tax_deductible_default=False,
            default_non_deductible_reason="fines_penalties",
        )
        form = ExpenseForm(data={
            "description": "Infraction administrative",
            "category": cat_fines.pk,
            "amount": "2000.00",
            "expense_date": "2026-05-12",
        })
        self.assertTrue(form.is_valid(), form.errors)
        expense = form.save()
        self.assertFalse(expense.is_tax_deductible)
        self.assertEqual(expense.non_deductible_reason, Expense.NonDeductibleReason.FINES_PENALTIES)
        self.assertEqual(expense.fiscal_reintegration_amount, Decimal("2000.00"))

    def test_auto_detection_by_description_keywords(self):
        """La détection intelligente par mot-clé ('amende', 'radar', 'pénalité') bascule automatiquement en non déductible."""
        form = ExpenseForm(data={
            "description": "Amende radar excès de vitesse véhicule livraison",
            "category": self.category.pk,  # Catégorie standard
            "amount": "300.00",
            "expense_date": "2026-06-20",
        })
        self.assertTrue(form.is_valid(), form.errors)
        expense = form.save()
        self.assertFalse(expense.is_tax_deductible)
        self.assertEqual(expense.non_deductible_reason, Expense.NonDeductibleReason.FINES_PENALTIES)
        self.assertEqual(expense.fiscal_reintegration_amount, Decimal("300.00"))



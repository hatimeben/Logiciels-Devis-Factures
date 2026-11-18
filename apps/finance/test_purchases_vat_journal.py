from datetime import date
from decimal import Decimal
from io import BytesIO
import openpyxl

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import ExpenseCategory
from apps.finance.models import Expense, ExpensePayment
from apps.finance.accounting_services import (
    get_accounting_period_range,
    get_moroccan_vat_report_data,
    get_sales_journal_data,
    get_payments_journal_data,
    get_non_deductible_expenses_data,
    get_purchases_journal_data,
    build_complete_fiduciary_pack_xlsx,
)
from apps.partners.models import Supplier


class PurchasesVatJournalTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin_test", email="admin@test.com", password="password123"
        )
        self.client.force_login(self.user)

        # Fournisseurs
        self.supplier_station = Supplier.objects.create(
            name="Station Afriquia", ice="001122334455667"
        )
        self.supplier_it = Supplier.objects.create(
            name="Maroc Bureau", ice="009988776655443"
        )
        self.supplier_bank = Supplier.objects.create(
            name="Banque Populaire", ice="004455667788990"
        )

        # Catégories PCGE
        self.cat_fuel = ExpenseCategory.objects.create(
            name="Carburant & Déplacements",
            pcge_account="61251",
            is_vat_recoverable=False,
            vat_rate=Decimal("0.00"),
            is_tax_deductible_default=True,
        )
        self.cat_it = ExpenseCategory.objects.create(
            name="Matériel informatique",
            pcge_account="6111",
            is_vat_recoverable=True,
            vat_rate=Decimal("20.00"),
            is_tax_deductible_default=True,
        )
        self.cat_bank = ExpenseCategory.objects.create(
            name="Frais bancaires",
            pcge_account="6147",
            is_vat_recoverable=True,
            vat_rate=Decimal("10.00"),
            is_tax_deductible_default=True,
        )

        self.today = date(2026, 11, 2)

    def test_fuel_expense_has_zero_recoverable_vat_and_is_100_percent_ttc(self):
        """Vérifie que le carburant (61251) est exclu du droit à déduction TVA (Art. 106) et déductible à l'IS."""
        fuel_exp = Expense.objects.create(
            category=self.cat_fuel,
            supplier=self.supplier_station,
            description="Plein gasoil véhicule société",
            expense_date=self.today,
            amount=Decimal("1500.00"),
            is_tax_deductible=True,
        )

        self.assertFalse(fuel_exp.effective_is_vat_recoverable)
        self.assertEqual(fuel_exp.effective_tax_rate, Decimal("0.00"))
        # Base HT = Montant total (charge TTC pure)
        self.assertEqual(fuel_exp.base_ht, Decimal("1500.00"))
        # TVA récupérable = 0,00 MAD
        self.assertEqual(fuel_exp.vat_amount, Decimal("0.00"))
        # Reste 100% déductible à l'IS
        self.assertTrue(fuel_exp.is_tax_deductible)
        self.assertIn("Art. 106", fuel_exp.vat_status_label)

    def test_standard_expense_has_20_percent_recoverable_vat(self):
        """Vérifie qu'un achat de matériel informatique à 20% ventile HT et TVA récupérable."""
        it_exp = Expense.objects.create(
            category=self.cat_it,
            supplier=self.supplier_it,
            description="Achat écran 27 pouces",
            expense_date=self.today,
            amount=Decimal("1200.00"),
            is_tax_deductible=True,
        )

        self.assertTrue(it_exp.effective_is_vat_recoverable)
        self.assertEqual(it_exp.effective_tax_rate, Decimal("20.00"))
        self.assertEqual(it_exp.base_ht, Decimal("1000.00"))
        self.assertEqual(it_exp.vat_amount, Decimal("200.00"))
        self.assertIn("Récupérable", it_exp.vat_status_label)

    def test_bank_fee_expense_has_10_percent_recoverable_vat(self):
        """Vérifie que les frais bancaires à 10% ventilent HT et TVA récupérable."""
        bank_exp = Expense.objects.create(
            category=self.cat_bank,
            supplier=self.supplier_bank,
            description="Commission tenue de compte",
            expense_date=self.today,
            amount=Decimal("110.00"),
            is_tax_deductible=True,
        )

        self.assertTrue(bank_exp.effective_is_vat_recoverable)
        self.assertEqual(bank_exp.effective_tax_rate, Decimal("10.00"))
        self.assertEqual(bank_exp.base_ht, Decimal("100.00"))
        self.assertEqual(bank_exp.vat_amount, Decimal("10.00"))

    def test_purchases_journal_service_totals_and_reconciliation(self):
        """Vérifie le calcul du Journal des Achats (AC) et la ventilation par compte PCGE."""
        Expense.objects.create(
            category=self.cat_fuel,
            supplier=self.supplier_station,
            description="Gasoil Ford",
            expense_date=self.today,
            amount=Decimal("1500.00"),
        )
        Expense.objects.create(
            category=self.cat_it,
            supplier=self.supplier_it,
            description="Ordinateur PC",
            expense_date=self.today,
            amount=Decimal("6000.00"),
        )

        data = get_purchases_journal_data(self.today, self.today)
        totals = data["totals"]

        self.assertEqual(totals["count"], 2)
        # Total TTC = 1500 + 6000 = 7500.00 MAD
        self.assertEqual(totals["total_ttc"], Decimal("7500.00"))
        # TVA récupérable = 0 (carburant) + 1000 (PC 6000 TTC = 5000 HT + 1000 TVA) = 1000.00 MAD
        self.assertEqual(totals["recoverable_vat"], Decimal("1000.00"))
        # Base HT = 1500 (carburant TTC en charge) + 5000 (PC HT) = 6500.00 MAD
        self.assertEqual(totals["total_ht"], Decimal("6500.00"))
        # Carburant suivi séparément
        self.assertEqual(totals["fuel_total_ttc"], Decimal("1500.00"))

        # Vérification des écritures comptables
        entries = data["accounting_entries"]
        self.assertTrue(any(e["account"] == "61251000" and e["debit"] == Decimal("1500.00") for e in entries))
        self.assertTrue(any(e["account"] == "34552000" and e["debit"] == Decimal("1000.00") for e in entries))
        self.assertTrue(any(e["account"] == "44110000" and e["credit"] == Decimal("6000.00") for e in entries))

    def test_fiduciary_pack_xlsx_includes_purchases_sheet(self):
        """Vérifie que l'export Excel Pack Fiduciaire inclut l'onglet Journal Achats (AC)."""
        Expense.objects.create(
            category=self.cat_fuel,
            supplier=self.supplier_station,
            description="Carburant test",
            expense_date=self.today,
            amount=Decimal("800.00"),
        )

        vat_data = get_moroccan_vat_report_data(self.today, self.today)
        sales_data = get_sales_journal_data(self.today, self.today)
        payments_data = get_payments_journal_data(self.today, self.today)
        purchases_data = get_purchases_journal_data(self.today, self.today)

        xlsx_bytes = build_complete_fiduciary_pack_xlsx(
            "T4 2026", vat_data, sales_data, payments_data, purchases_data=purchases_data
        )

        wb = openpyxl.load_workbook(BytesIO(xlsx_bytes))
        sheet_names = wb.sheetnames
        self.assertIn("Journal Achats (AC)", sheet_names)
        self.assertIn("Déclaration TVA & Relevé", sheet_names)

        ws_ac = wb["Journal Achats (AC)"]
        self.assertIn("JOURNAL DES ACHATS", str(ws_ac["A1"].value))

    def test_export_purchases_csv_endpoint(self):
        """Vérifie le téléchargement du fichier CSV du Journal des Achats."""
        Expense.objects.create(
            category=self.cat_fuel,
            supplier=self.supplier_station,
            description="Gasoil tournée client",
            expense_date=self.today,
            amount=Decimal("500.00"),
        )

        url = reverse("accounting-export-purchases-csv")
        response = self.client.get(url, {"year": 2026, "period_type": "quarter", "period_value": 4})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        content = response.content.decode("utf-8-sig")
        self.assertIn("Journal_Achats_AC", response["Content-Disposition"])
        self.assertIn("Gasoil tournée client", content)
        self.assertIn("61251", content)

    def test_accounting_dashboard_with_achats_tab(self):
        """Vérifie que le dashboard comptable charge l'onglet achats et affiche la balance fiscale."""
        Expense.objects.create(
            category=self.cat_fuel,
            supplier=self.supplier_station,
            description="Gasoil",
            expense_date=self.today,
            amount=Decimal("1500.00"),
        )

        url = reverse("accounting-dashboard")
        response = self.client.get(url, {"year": 2026, "period_type": "quarter", "period_value": 4, "tab": "achats"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "JOURNAL DES ACHATS & FRAIS GÉNÉRAUX")
        self.assertContains(response, "61251")
        self.assertContains(response, "TVA Déductible s/ Charges")

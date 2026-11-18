import calendar
import csv
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.db.models import Sum
from django.utils import timezone
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import CompanySettings, Invoice, Payment, PaymentAllocation
from .quote_calculations import calculate_quote_line, calculate_quote_totals, round_money


def _money(val):
    if val is None:
        val = Decimal("0.00")
    return f"{val:,.2f}".replace(",", " ").replace(".", ",")


def get_accounting_period_range(year, period_type="month", period_value=1):
    """
    Retourne (start_date, end_date, period_label) pour les déclarations comptables et TVA marocaines.
    """
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = timezone.localdate().year

    month_names = [
        "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
    ]

    if period_type == "month":
        try:
            m = max(1, min(12, int(period_value)))
        except (TypeError, ValueError):
            m = timezone.localdate().month
        last_day = calendar.monthrange(year, m)[1]
        start_date = date(year, m, 1)
        end_date = date(year, m, last_day)
        label = f"{month_names[m]} {year}"
    elif period_type == "quarter":
        try:
            q = max(1, min(4, int(period_value)))
        except (TypeError, ValueError):
            q = (timezone.localdate().month - 1) // 3 + 1
        start_month = (q - 1) * 3 + 1
        end_month = q * 3
        last_day = calendar.monthrange(year, end_month)[1]
        start_date = date(year, start_month, 1)
        end_date = date(year, end_month, last_day)
        label = f"Trimestre T{q} ({month_names[start_month]} - {month_names[end_month]}) {year}"
    else:  # year
        start_date = date(year, 1, 1)
        end_date = date(year, 12, 31)
        label = f"Année {year}"

    return start_date, end_date, label


def get_moroccan_vat_report_data(start_date, end_date, regime="encaissement"):
    """
    Calcule l'état récapitulatif de la TVA collectée selon le CGI marocain.
    - Régime des encaissements (droit commun / standard) : TVA exigible lors du règlement.
    - Régime des débits (sur option) : TVA exigible lors de la facturation.
    """
    rows = []
    summary_by_rate = {}
    order_num = 1

    total_base_ht = Decimal("0.00")
    total_tax_amount = Decimal("0.00")
    total_ttc = Decimal("0.00")

    def _add_to_summary(rate, name, base_ht, tax_amount, ttc):
        rate_key = Decimal(str(rate)) if rate is not None else Decimal("0.00")
        if rate_key not in summary_by_rate:
            summary_by_rate[rate_key] = {
                "rate": rate_key,
                "name": name or f"TVA {rate_key:.0f}%",
                "base_ht": Decimal("0.00"),
                "tax_amount": Decimal("0.00"),
                "total_ttc": Decimal("0.00"),
            }
        summary_by_rate[rate_key]["base_ht"] += base_ht
        summary_by_rate[rate_key]["tax_amount"] += tax_amount
        summary_by_rate[rate_key]["total_ttc"] += ttc

    if regime == "encaissement":
        # Encaissements perçus dans la période sur factures émises
        allocations = (
            PaymentAllocation.objects.filter(
                payment__payment_date__range=(start_date, end_date),
                invoice__status__in=[
                    Invoice.Status.ISSUED,
                    Invoice.Status.PARTIALLY_PAID,
                    Invoice.Status.PAID,
                ],
            )
            .select_related("payment", "invoice", "invoice__customer")
            .prefetch_related("invoice__lines")
            .order_by("payment__payment_date", "invoice__number", "id")
        )

        for alloc in allocations:
            inv = alloc.invoice
            payment = alloc.payment
            customer = inv.customer
            snapshot = inv.customer_snapshot or {}

            inv_ttc = inv.total_ttc
            if inv_ttc <= Decimal("0.00"):
                continue

            ratio = alloc.amount / inv_ttc

            # Ventilation par ligne de prestation selon le taux de TVA
            lines = list(inv.lines.all())
            if not lines:
                continue

            for line in lines:
                amounts = calculate_quote_line(
                    line.quantity,
                    line.unit_price,
                    line.discount_type,
                    line.discount_value,
                    line.tax_percentage,
                )
                rate = line.tax_percentage or Decimal("0.00")
                line_ht = amounts["net"] * ratio
                line_tax = amounts["tax"] * ratio
                line_ttc = amounts["total"] * ratio

                rows.append({
                    "order_num": order_num,
                    "invoice_number": inv.number or f"FAC-{inv.pk}",
                    "invoice_date": inv.issue_date or inv.created_at.date(),
                    "customer_name": snapshot.get("name") or customer.name,
                    "customer_ice": snapshot.get("ice") or getattr(customer, "ice", "") or "—",
                    "customer_if": snapshot.get("tax_identifier") or customer.tax_identifier or "—",
                    "payment_date": payment.payment_date,
                    "payment_method": payment.get_method_display(),
                    "payment_reference": payment.reference or "—",
                    "description": line.description,
                    "tax_rate": rate,
                    "tax_name": line.tax_name or ("Sans TVA" if rate == 0 else f"TVA {rate:.0f}%"),
                    "base_ht": line_ht,
                    "tax_amount": line_tax,
                    "total_ttc": line_ttc,
                })
                order_num += 1

                _add_to_summary(rate, line.tax_name, line_ht, line_tax, line_ttc)
                total_base_ht += line_ht
                total_tax_amount += line_tax
                total_ttc += line_ttc

    else:
        # Régime des débits : toutes les factures émises sur la période
        invoices = (
            Invoice.objects.filter(
                issue_date__range=(start_date, end_date),
                status__in=[
                    Invoice.Status.ISSUED,
                    Invoice.Status.PARTIALLY_PAID,
                    Invoice.Status.PAID,
                ],
            )
            .select_related("customer")
            .prefetch_related("lines")
            .order_by("issue_date", "number", "id")
        )

        for inv in invoices:
            customer = inv.customer
            snapshot = inv.customer_snapshot or {}

            for line in inv.lines.all():
                amounts = calculate_quote_line(
                    line.quantity,
                    line.unit_price,
                    line.discount_type,
                    line.discount_value,
                    line.tax_percentage,
                )
                rate = line.tax_percentage or Decimal("0.00")
                line_ht = amounts["net"]
                line_tax = amounts["tax"]
                line_ttc = amounts["total"]

                rows.append({
                    "order_num": order_num,
                    "invoice_number": inv.number or f"FAC-{inv.pk}",
                    "invoice_date": inv.issue_date,
                    "customer_name": snapshot.get("name") or customer.name,
                    "customer_ice": snapshot.get("ice") or getattr(customer, "ice", "") or "—",
                    "customer_if": snapshot.get("tax_identifier") or customer.tax_identifier or "—",
                    "payment_date": inv.issue_date,
                    "payment_method": "À l'émission (Débits)",
                    "payment_reference": "Facturation",
                    "description": line.description,
                    "tax_rate": rate,
                    "tax_name": line.tax_name or ("Sans TVA" if rate == 0 else f"TVA {rate:.0f}%"),
                    "base_ht": line_ht,
                    "tax_amount": line_tax,
                    "total_ttc": line_ttc,
                })
                order_num += 1

                _add_to_summary(rate, line.tax_name, line_ht, line_tax, line_ttc)
                total_base_ht += line_ht
                total_tax_amount += line_tax
                total_ttc += line_ttc

    sorted_summary = [summary_by_rate[k] for k in sorted(summary_by_rate.keys(), reverse=True)]

    return {
        "regime": regime,
        "start_date": start_date,
        "end_date": end_date,
        "rows": rows,
        "summary": sorted_summary,
        "totals": {
            "base_ht": total_base_ht,
            "tax_amount": total_tax_amount,
            "total_ttc": total_ttc,
            "count": len(rows),
        },
    }


def get_sales_journal_data(start_date, end_date):
    """
    Génère le journal des ventes (VE) conforme au Plan Comptable Marocain.
    """
    invoices = (
        Invoice.objects.filter(
            issue_date__range=(start_date, end_date),
            status__in=[
                Invoice.Status.ISSUED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.PAID,
            ],
        )
        .select_related("customer")
        .prefetch_related("lines")
        .order_by("issue_date", "number", "id")
    )

    entries = []
    accounting_entries = []

    total_gross = Decimal("0.00")
    total_discount = Decimal("0.00")
    total_net = Decimal("0.00")
    total_tax = Decimal("0.00")
    total_ttc = Decimal("0.00")

    for inv in invoices:
        totals = calculate_quote_totals(inv.lines.all())
        snapshot = inv.customer_snapshot or {}
        cust_name = snapshot.get("name") or inv.customer.name
        cust_ice = snapshot.get("ice") or getattr(inv.customer, "ice", "") or "—"
        doc_num = inv.number or f"FAC-{inv.pk}"

        entries.append({
            "date": inv.issue_date,
            "number": doc_num,
            "customer_name": cust_name,
            "customer_ice": cust_ice,
            "gross": totals["gross"],
            "discount": totals["discount"],
            "net": totals["net"],
            "tax": totals["tax"],
            "total": totals["total"],
            "paid": inv.total_paid,
            "balance": inv.remaining_balance,
            "status_display": inv.get_status_display(),
        })

        total_gross += totals["gross"]
        total_discount += totals["discount"]
        total_net += totals["net"]
        total_tax += totals["tax"]
        total_ttc += totals["total"]

        # Écritures comptables (PCGM)
        # 1. Débit Client 3421
        accounting_entries.append({
            "date": inv.issue_date,
            "journal": "VE",
            "piece": doc_num,
            "account": "34210000",
            "account_label": f"Clients - {cust_name}",
            "libelle": f"Facture {doc_num}",
            "debit": totals["total"],
            "credit": Decimal("0.00"),
        })
        # 2. Crédit Ventes 7124
        accounting_entries.append({
            "date": inv.issue_date,
            "journal": "VE",
            "piece": doc_num,
            "account": "71240000",
            "account_label": "Prestations de services / Ventes",
            "libelle": f"Facture {doc_num}",
            "debit": Decimal("0.00"),
            "credit": totals["net"],
        })
        # 3. Crédit État TVA facturée 4455 (si TVA > 0)
        if totals["tax"] > Decimal("0.00"):
            accounting_entries.append({
                "date": inv.issue_date,
                "journal": "VE",
                "piece": doc_num,
                "account": "44550000",
                "account_label": "État, TVA facturée",
                "libelle": f"TVA s/ Facture {doc_num}",
                "debit": Decimal("0.00"),
                "credit": totals["tax"],
            })

    return {
        "start_date": start_date,
        "end_date": end_date,
        "entries": entries,
        "accounting_entries": accounting_entries,
        "totals": {
            "gross": total_gross,
            "discount": total_discount,
            "net": total_net,
            "tax": total_tax,
            "total": total_ttc,
            "count": len(entries),
        },
    }


def get_payments_journal_data(start_date, end_date):
    """
    Génère le journal des encaissements / règlements clients (TR/RG).
    """
    allocations = (
        PaymentAllocation.objects.filter(
            payment__payment_date__range=(start_date, end_date),
            invoice__status__in=[
                Invoice.Status.ISSUED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.PAID,
            ],
        )
        .select_related("payment", "invoice", "invoice__customer")
        .order_by("payment__payment_date", "id")
    )

    entries = []
    accounting_entries = []
    total_amount = Decimal("0.00")

    for alloc in allocations:
        p = alloc.payment
        inv = alloc.invoice
        snapshot = inv.customer_snapshot or {}
        cust_name = snapshot.get("name") or inv.customer.name
        doc_num = inv.number or f"FAC-{inv.pk}"
        amount = alloc.amount

        # Compte de trésorerie PCGM selon le mode de paiement
        if p.method == Payment.Method.BANK_TRANSFER:
            account_code = "51410000"
            account_name = "Banque"
        elif p.method == Payment.Method.CHEQUE:
            account_code = "51111000"
            account_name = "Chèques à encaisser"
        elif p.method == Payment.Method.BILL_OF_EXCHANGE:
            account_code = "51130000"
            account_name = "Effets à encaisser"
        elif p.method == Payment.Method.CASH:
            account_code = "51610000"
            account_name = "Caisse Centrale"
        else:
            account_code = "51610000"
            account_name = "Caisse / Règlements"

        is_cash_over = p.is_cash_over_ceiling
        cash_excess = p.cash_excess_amount
        fiscal_alert = (
            f"⚠️ Plafond CGI dépassé (>5k) : réint. {cash_excess} MAD"
            if is_cash_over else "Conforme"
        )

        entries.append({
            "date": p.payment_date,
            "payment_id": p.pk,
            "method": p.method,
            "method_display": p.get_method_display(),
            "reference": p.reference or "—",
            "invoice_number": doc_num,
            "customer_name": cust_name,
            "amount": amount,
            "notes": p.notes or "",
            "is_cash_over_ceiling": is_cash_over,
            "cash_excess_amount": cash_excess,
            "fiscal_alert": fiscal_alert,
        })

        total_amount += amount

        # Libellé avec tag d'alerte si dépassement espèces
        alert_tag = " [⚠️ PLAFOND CGI >5K]" if is_cash_over else ""

        # Écriture Débit Trésorerie
        accounting_entries.append({
            "date": p.payment_date,
            "journal": "TR",
            "piece": f"REG-{p.pk}",
            "account": account_code,
            "account_label": account_name,
            "libelle": f"Règlt {p.get_method_display()} {doc_num} - {cust_name}{alert_tag}",
            "debit": amount,
            "credit": Decimal("0.00"),
        })
        # Écriture Crédit Client
        accounting_entries.append({
            "date": p.payment_date,
            "journal": "TR",
            "piece": f"REG-{p.pk}",
            "account": "34210000",
            "account_label": f"Clients - {cust_name}",
            "libelle": f"Règlt {doc_num}{alert_tag}",
            "debit": Decimal("0.00"),
            "credit": amount,
        })

    return {
        "start_date": start_date,
        "end_date": end_date,
        "entries": entries,
        "accounting_entries": accounting_entries,
        "totals": {
            "amount": total_amount,
            "count": len(entries),
        },
    }


def get_non_deductible_expenses_data(start_date, end_date):
    """
    Calcule l'état fiscal des charges non déductibles et des réintégrations
    extra-comptables (IS - Tableau N°3 de la liasse fiscale marocaine / CGI).
    """
    from .models import Expense
    expenses = (
        Expense.objects.filter(expense_date__range=[start_date, end_date])
        .select_related("category", "supplier", "project")
        .prefetch_related("payments")
        .order_by("expense_date", "id")
    )

    rows = []
    total_recorded = Decimal("0.00")
    total_reintegration = Decimal("0.00")
    by_reason = {}

    order = 1
    for exp in expenses:
        if exp.has_fiscal_reintegration:
            reint_amt = exp.fiscal_reintegration_amount
            reason_lbl = exp.effective_reintegration_reason
            total_recorded += exp.amount
            total_reintegration += reint_amt

            by_reason[reason_lbl] = by_reason.get(reason_lbl, Decimal("0.00")) + reint_amt

            rows.append({
                "order_num": order,
                "id": exp.pk,
                "expense_date": exp.expense_date,
                "description": exp.description,
                "category_name": exp.category.name if exp.category else "—",
                "supplier_name": exp.supplier.name if exp.supplier else "Divers / Sans fournisseur",
                "supplier_ice": exp.supplier.ice if (exp.supplier and exp.supplier.ice) else "—",
                "amount": exp.amount,
                "reintegration_amount": reint_amt,
                "reason_label": reason_lbl,
                "fiscal_notes": exp.fiscal_notes,
                "has_cash_over_ceiling": exp.has_cash_over_ceiling,
                "cash_excess": exp.total_cash_excess,
            })
            order += 1

    summary_reasons = [
        {"reason": k, "amount": v}
        for k, v in sorted(by_reason.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "rows": rows,
        "summary_reasons": summary_reasons,
        "totals": {
            "count": len(rows),
            "total_recorded": round_money(total_recorded),
            "total_reintegration": round_money(total_reintegration),
        },
    }


def get_purchases_journal_data(start_date, end_date):
    """
    Calcule le Journal des Achats et Charges d'exploitation (AC) selon le PCGE marocain :
    - Ventilation par compte PCGE (6111, 61251, 6136, 6145, 6147...)
    - Traitement spécifique Carburant (Art. 106-I-2° CGI) : 0 DH de TVA récupérable, 100% TTC pur en charge
    - Traitement des amendes (Art. 11-I CGI) : 0 DH TVA récupérable
    - Charges d'exploitation : Base HT, TVA Récupérable (Compte 34552), Total TTC
    - Écritures comptables en partie double
    """
    from .models import Expense
    expenses = (
        Expense.objects.filter(expense_date__range=[start_date, end_date])
        .select_related("category", "supplier", "project")
        .prefetch_related("payments")
        .order_by("expense_date", "id")
    )

    rows = []
    total_ht = Decimal("0.00")
    total_recoverable_vat = Decimal("0.00")
    total_ttc = Decimal("0.00")
    fuel_total_ttc = Decimal("0.00")
    summary_by_account = {}
    accounting_entries = []

    order = 1
    for exp in expenses:
        piece = f"DEP-{exp.pk:04d}"
        cat_name = exp.category.name if exp.category else "Dépense générale"
        pcge = exp.category.pcge_account if exp.category else "6111"
        sup_name = exp.supplier.name if exp.supplier else "Divers / Sans fournisseur"
        sup_ice = exp.supplier.ice if (exp.supplier and exp.supplier.ice) else "—"

        ht = exp.base_ht
        vat = exp.vat_amount
        ttc = exp.amount
        rate = exp.effective_tax_rate
        is_recup = exp.effective_is_vat_recoverable
        status_lbl = exp.vat_status_label

        total_ht += ht
        total_recoverable_vat += vat
        total_ttc += ttc
        if pcge == "61251" or "carburant" in cat_name.lower():
            fuel_total_ttc += ttc

        # Regroupement par compte PCGE
        if pcge not in summary_by_account:
            summary_by_account[pcge] = {
                "account": pcge,
                "name": cat_name,
                "count": 0,
                "base_ht": Decimal("0.00"),
                "vat_amount": Decimal("0.00"),
                "total_ttc": Decimal("0.00"),
            }
        summary_by_account[pcge]["count"] += 1
        summary_by_account[pcge]["base_ht"] += ht
        summary_by_account[pcge]["vat_amount"] += vat
        summary_by_account[pcge]["total_ttc"] += ttc

        first_pmt = exp.payments.first()
        pmt_method = first_pmt.get_method_display() if first_pmt else "Non réglé"
        pmt_date = first_pmt.payment_date if first_pmt else exp.expense_date

        rows.append({
            "order_num": order,
            "id": exp.pk,
            "piece": piece,
            "expense_date": exp.expense_date,
            "category_name": cat_name,
            "pcge_account": pcge,
            "supplier_name": sup_name,
            "supplier_ice": sup_ice,
            "description": exp.description,
            "payment_method": pmt_method,
            "payment_date": pmt_date,
            "base_ht": ht,
            "tax_rate": rate,
            "vat_amount": vat,
            "total_ttc": ttc,
            "is_vat_recoverable": is_recup,
            "vat_status_label": status_lbl,
            "is_tax_deductible": exp.is_tax_deductible,
            "receipt_url": exp.receipt.url if exp.receipt else "",
        })
        order += 1

        # Écritures comptables Journal des Achats (AC)
        # 1. Débit Compte de charge 61xxx (pour le HT, ou pour le TTC si TVA non récupérable / Carburant)
        accounting_entries.append({
            "date": exp.expense_date,
            "journal": "AC",
            "piece": piece,
            "account": pcge.ljust(8, "0"),
            "account_label": f"{cat_name} ({pcge})",
            "libelle": f"Fact/Ticket {piece} - {exp.description[:40]}",
            "debit": ht,
            "credit": Decimal("0.00"),
        })
        # 2. Débit État TVA récupérable sur charges 34552000 (si TVA récupérable > 0)
        if vat > Decimal("0.00"):
            accounting_entries.append({
                "date": exp.expense_date,
                "journal": "AC",
                "piece": piece,
                "account": "34552000",
                "account_label": "État, TVA récupérable sur charges",
                "libelle": f"TVA s/ {piece} ({rate:.0f}%)",
                "debit": vat,
                "credit": Decimal("0.00"),
            })
        # 3. Crédit Fournisseur 44110000 (pour le TTC total)
        accounting_entries.append({
            "date": exp.expense_date,
            "journal": "AC",
            "piece": piece,
            "account": "44110000",
            "account_label": "Fournisseurs",
            "libelle": f"{sup_name[:40]} - {piece}",
            "debit": Decimal("0.00"),
            "credit": ttc,
        })

    summary_list = sorted(summary_by_account.values(), key=lambda x: x["account"])

    return {
        "rows": rows,
        "summary_by_account": summary_list,
        "accounting_entries": accounting_entries,
        "totals": {
            "count": len(rows),
            "total_ht": round_money(total_ht),
            "recoverable_vat": round_money(total_recoverable_vat),
            "fuel_total_ttc": round_money(fuel_total_ttc),
            "total_ttc": round_money(total_ttc),
        },
    }


def build_complete_fiduciary_pack_xlsx(period_label, vat_data, sales_data, payments_data, non_deductible_data=None, purchases_data=None):
    """
    Génère le classeur Excel Haute Définition (.xlsx) avec 3 onglets formatés pour le fiduciaire :
    1. Déclaration TVA & Encaissements
    2. Journal des Ventes (VE)
    3. Journal des Règlements (TR)
    """
    company = CompanySettings.get_settings()
    if non_deductible_data is None and vat_data.get("start_date") and vat_data.get("end_date"):
        non_deductible_data = get_non_deductible_expenses_data(vat_data["start_date"], vat_data["end_date"])
    if non_deductible_data is None:
        non_deductible_data = {"rows": [], "summary_reasons": [], "totals": {"count": 0, "total_recorded": Decimal("0.00"), "total_reintegration": Decimal("0.00")}}
    if purchases_data is None and vat_data.get("start_date") and vat_data.get("end_date"):
        purchases_data = get_purchases_journal_data(vat_data["start_date"], vat_data["end_date"])
    if purchases_data is None:
        purchases_data = {
            "rows": [],
            "summary_by_account": [],
            "accounting_entries": [],
            "totals": {
                "count": 0,
                "total_ht": Decimal("0.00"),
                "recoverable_vat": Decimal("0.00"),
                "fuel_total_ttc": Decimal("0.00"),
                "total_ttc": Decimal("0.00"),
            },
        }
    wb = openpyxl.Workbook()

    # Styles d'excellence fiduciaire : Bleu Nuit & Or
    font_title = Font(name="Calibri", size=15, bold=True, color="0F172A")
    font_subtitle = Font(name="Calibri", size=11, bold=True, color="C59B27")
    font_company = Font(name="Calibri", size=9.5, color="475569")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=9.5, color="0F172A")
    font_bold = Font(name="Calibri", size=9.5, bold=True, color="0F172A")
    font_kpi_label = Font(name="Calibri", size=9, bold=True, color="64748B")
    font_kpi_val = Font(name="Calibri", size=13, bold=True, color="0F172A")

    fill_header = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    fill_subtotal = PatternFill(start_color="FAF5E8", end_color="FAF5E8", fill_type="solid")
    fill_zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    fill_kpi = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_warning = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
    font_warning = Font(name="Calibri", size=10, bold=True, color="92400E")

    thin_border = Side(style="thin", color="E2E8F0")
    gold_top = Side(style="medium", color="C59B27")
    cell_border = Border(left=thin_border, right=thin_border, top=thin_border, bottom=thin_border)
    total_border = Border(top=gold_top, bottom=Side(style="double", color="0F172A"), left=thin_border, right=thin_border)

    money_fmt = '#,##0.00 "MAD"'
    date_fmt = "DD/MM/YYYY"

    # ==========================================
    # ONGLET 1 : DÉCLARATION DE TVA MAROCAINE
    # ==========================================
    ws_tva = wb.active
    ws_tva.title = "Déclaration TVA & Relevé"
    ws_tva.views.sheetView[0].showGridLines = True

    # Entête Entreprise
    ws_tva["A1"] = f"{company.name} — DÉCLARATION DE TVA ({'RÉGIME DES ENCAISSEMENTS' if vat_data['regime'] == 'encaissement' else 'RÉGIME DES DÉBITS'})"
    ws_tva["A1"].font = font_title
    ws_tva["A2"] = f"Période fiscale : {period_label} | {company.legal_notice()}"
    ws_tva["A2"].font = font_company

    # Blocs KPI
    kpis = [
        ("Base HT Imposable", vat_data["totals"]["base_ht"], "A4", "B5"),
        ("TVA Collectée Exigible", vat_data["totals"]["tax_amount"], "D4", "E5"),
        ("Total TTC Opérations", vat_data["totals"]["total_ttc"], "G4", "H5"),
    ]
    for label, val, top_left, bot_right in kpis:
        col1 = top_left[0]
        row1 = int(top_left[1])
        ws_tva[top_left] = label
        ws_tva[top_left].font = font_kpi_label
        ws_tva[f"{col1}{row1+1}"] = float(val)
        ws_tva[f"{col1}{row1+1}"].font = font_kpi_val
        ws_tva[f"{col1}{row1+1}"].number_format = money_fmt

    # Tableau Récapitulatif par Taux
    ws_tva["A7"] = "SYNTHÈSE DE LA TVA COLLECTÉE PAR TAUX"
    ws_tva["A7"].font = font_subtitle

    tva_headers = ["Taux de TVA", "Libellé de la Taxe", "Base Hors Taxe (MAD)", "Montant TVA (MAD)", "Total TTC (MAD)"]
    for col_idx, h in enumerate(tva_headers, start=1):
        c = ws_tva.cell(row=8, column=col_idx, value=h)
        c.font = font_header
        c.fill = fill_header
        c.alignment = Alignment(horizontal="center" if col_idx <= 2 else "right")
        c.border = cell_border

    row_idx = 9
    for item in vat_data["summary"]:
        ws_tva.cell(row=row_idx, column=1, value=f"{item['rate']:.0f} %").alignment = Alignment(horizontal="center")
        ws_tva.cell(row=row_idx, column=2, value=item["name"]).alignment = Alignment(horizontal="left")
        ws_tva.cell(row=row_idx, column=3, value=float(item["base_ht"])).number_format = money_fmt
        ws_tva.cell(row=row_idx, column=4, value=float(item["tax_amount"])).number_format = money_fmt
        ws_tva.cell(row=row_idx, column=5, value=float(item["total_ttc"])).number_format = money_fmt
        for ci in range(1, 6):
            cell = ws_tva.cell(row=row_idx, column=ci)
            cell.font = font_data
            cell.border = cell_border
        row_idx += 1

    # Total Synthèse
    ws_tva.cell(row=row_idx, column=1, value="TOTAL TVA").font = font_bold
    ws_tva.cell(row=row_idx, column=2, value="").font = font_bold
    ws_tva.cell(row=row_idx, column=3, value=float(vat_data["totals"]["base_ht"])).number_format = money_fmt
    ws_tva.cell(row=row_idx, column=4, value=float(vat_data["totals"]["tax_amount"])).number_format = money_fmt
    ws_tva.cell(row=row_idx, column=5, value=float(vat_data["totals"]["total_ttc"])).number_format = money_fmt
    for ci in range(1, 6):
        cell = ws_tva.cell(row=row_idx, column=ci)
        cell.font = font_bold
        cell.fill = fill_subtotal
        cell.border = total_border
    row_idx += 2

    # Tableau Détaillé des Opérations (Bordereau DGI)
    ws_tva.cell(row=row_idx, column=1, value="RELEVÉ DÉTAILLÉ DES OPÉRATIONS (CONFORME DGI)").font = font_subtitle
    row_idx += 1

    detail_headers = [
        "N°", "N° Facture", "Date Facture", "Client / Raison Sociale", "ICE Client", "IF Client",
        "Date Règlement", "Mode", "Référence", "Base HT (MAD)", "Taux", "TVA Collectée (MAD)", "TTC Encaissé (MAD)"
    ]
    for col_idx, h in enumerate(detail_headers, start=1):
        c = ws_tva.cell(row=row_idx, column=col_idx, value=h)
        c.font = font_header
        c.fill = fill_header
        c.alignment = Alignment(horizontal="center" if col_idx in [1, 2, 3, 7, 8, 11] else "right" if col_idx >= 10 else "left")
        c.border = cell_border

    row_idx += 1
    for r in vat_data["rows"]:
        ws_tva.cell(row=row_idx, column=1, value=r["order_num"]).alignment = Alignment(horizontal="center")
        ws_tva.cell(row=row_idx, column=2, value=r["invoice_number"]).alignment = Alignment(horizontal="center")
        ws_tva.cell(row=row_idx, column=3, value=r["invoice_date"].strftime("%d/%m/%Y")).alignment = Alignment(horizontal="center")
        ws_tva.cell(row=row_idx, column=4, value=r["customer_name"]).alignment = Alignment(horizontal="left")
        ws_tva.cell(row=row_idx, column=5, value=r["customer_ice"]).alignment = Alignment(horizontal="center")
        ws_tva.cell(row=row_idx, column=6, value=r["customer_if"]).alignment = Alignment(horizontal="center")
        ws_tva.cell(row=row_idx, column=7, value=r["payment_date"].strftime("%d/%m/%Y") if isinstance(r["payment_date"], date) else str(r["payment_date"])).alignment = Alignment(horizontal="center")
        ws_tva.cell(row=row_idx, column=8, value=r["payment_method"]).alignment = Alignment(horizontal="left")
        ws_tva.cell(row=row_idx, column=9, value=r["payment_reference"]).alignment = Alignment(horizontal="left")
        ws_tva.cell(row=row_idx, column=10, value=float(r["base_ht"])).number_format = money_fmt
        ws_tva.cell(row=row_idx, column=11, value=f"{r['tax_rate']:.0f} %").alignment = Alignment(horizontal="center")
        ws_tva.cell(row=row_idx, column=12, value=float(r["tax_amount"])).number_format = money_fmt
        ws_tva.cell(row=row_idx, column=13, value=float(r["total_ttc"])).number_format = money_fmt

        for ci in range(1, 14):
            c = ws_tva.cell(row=row_idx, column=ci)
            c.font = font_data
            c.border = cell_border
            if row_idx % 2 == 0:
                c.fill = fill_zebra
        row_idx += 1

    # Total relevé détaillé
    ws_tva.cell(row=row_idx, column=4, value="TOTAL TVA COLLECTÉE SUR ENCAISSEMENTS").font = font_bold
    ws_tva.cell(row=row_idx, column=10, value=float(vat_data["totals"]["base_ht"])).number_format = money_fmt
    ws_tva.cell(row=row_idx, column=12, value=float(vat_data["totals"]["tax_amount"])).number_format = money_fmt
    ws_tva.cell(row=row_idx, column=13, value=float(vat_data["totals"]["total_ttc"])).number_format = money_fmt
    for ci in range(1, 14):
        c = ws_tva.cell(row=row_idx, column=ci)
        c.font = font_bold
        c.fill = fill_subtotal
        c.border = total_border
    row_idx += 2

    # Tableau Réconciliation Nette de TVA
    ws_tva.cell(row=row_idx, column=1, value="CALCUL DU SOLDE NET DE TVA (TVA NETTE DUE OU CRÉDIT DE TVA)").font = font_subtitle
    row_idx += 1

    rec_vat = purchases_data.get("totals", {}).get("recoverable_vat", Decimal("0.00"))
    coll_vat = vat_data["totals"]["tax_amount"]
    net_vat = coll_vat - rec_vat
    fuel_ttc = purchases_data.get("totals", {}).get("fuel_total_ttc", Decimal("0.00"))

    recon_lines = [
        ("1. TVA Collectée Exigible (Ventes & Prestations encaissées)", coll_vat, "C59B27"),
        ("2. (-) TVA Déductible / Récupérable sur Charges & Achats (Compte 34552)", rec_vat, "0F172A"),
        ("3. (=) SOLDE NET DE TVA " + ("(TVA Nette à décaisser / régler à la DGI)" if net_vat >= 0 else "(Crédit de TVA à reporter)"), abs(net_vat), "10B981" if net_vat < 0 else "BE123C"),
        ("Rappel fiscal Carburant (Art. 106-I-2° CGI) : Consommé en TTC pur (0 DH TVA récupérée)", fuel_ttc, "64748B"),
    ]
    for lbl, val, color in recon_lines:
        ws_tva.cell(row=row_idx, column=1, value=lbl).font = Font(name="Calibri", size=9.5, bold=True, color=color)
        c_val = ws_tva.cell(row=row_idx, column=12, value=float(val))
        c_val.font = Font(name="Calibri", size=10, bold=True, color=color)
        c_val.number_format = money_fmt
        c_val.border = cell_border
        row_idx += 1

    # ==========================================
    # ONGLET 2 : JOURNAL DES VENTES (VE)
    # ==========================================
    ws_ve = wb.create_sheet(title="Journal des Ventes")
    ws_ve.views.sheetView[0].showGridLines = True

    ws_ve["A1"] = f"{company.name} — JOURNAL DES VENTES (VE)"
    ws_ve["A1"].font = font_title
    ws_ve["A2"] = f"Période : {period_label} | {len(sales_data['entries'])} factures émises"
    ws_ve["A2"].font = font_company

    ve_headers = [
        "Date Pièce", "Journal", "N° Facture", "Client", "ICE Client",
        "Total Brut HT", "Remises", "Total Net HT (MAD)", "TVA (MAD)", "Total TTC (MAD)", "Réglé", "Solde Dû", "Statut"
    ]
    for col_idx, h in enumerate(ve_headers, start=1):
        c = ws_ve.cell(row=4, column=col_idx, value=h)
        c.font = font_header
        c.fill = fill_header
        c.alignment = Alignment(horizontal="center" if col_idx in [1, 2, 3, 5, 13] else "right" if 6 <= col_idx <= 12 else "left")
        c.border = cell_border

    row_idx = 5
    for e in sales_data["entries"]:
        ws_ve.cell(row=row_idx, column=1, value=e["date"].strftime("%d/%m/%Y")).alignment = Alignment(horizontal="center")
        ws_ve.cell(row=row_idx, column=2, value="VE").alignment = Alignment(horizontal="center")
        ws_ve.cell(row=row_idx, column=3, value=e["number"]).alignment = Alignment(horizontal="center")
        ws_ve.cell(row=row_idx, column=4, value=e["customer_name"]).alignment = Alignment(horizontal="left")
        ws_ve.cell(row=row_idx, column=5, value=e["customer_ice"]).alignment = Alignment(horizontal="center")
        ws_ve.cell(row=row_idx, column=6, value=float(e["gross"])).number_format = money_fmt
        ws_ve.cell(row=row_idx, column=7, value=float(e["discount"])).number_format = money_fmt
        ws_ve.cell(row=row_idx, column=8, value=float(e["net"])).number_format = money_fmt
        ws_ve.cell(row=row_idx, column=9, value=float(e["tax"])).number_format = money_fmt
        ws_ve.cell(row=row_idx, column=10, value=float(e["total"])).number_format = money_fmt
        ws_ve.cell(row=row_idx, column=11, value=float(e["paid"])).number_format = money_fmt
        ws_ve.cell(row=row_idx, column=12, value=float(e["balance"])).number_format = money_fmt
        ws_ve.cell(row=row_idx, column=13, value=e["status_display"]).alignment = Alignment(horizontal="center")

        for ci in range(1, 14):
            c = ws_ve.cell(row=row_idx, column=ci)
            c.font = font_data
            c.border = cell_border
            if row_idx % 2 == 0:
                c.fill = fill_zebra
        row_idx += 1

    # Totaux Ventes
    ws_ve.cell(row=row_idx, column=4, value="TOTAUX VENTES").font = font_bold
    ws_ve.cell(row=row_idx, column=6, value=float(sales_data["totals"]["gross"])).number_format = money_fmt
    ws_ve.cell(row=row_idx, column=7, value=float(sales_data["totals"]["discount"])).number_format = money_fmt
    ws_ve.cell(row=row_idx, column=8, value=float(sales_data["totals"]["net"])).number_format = money_fmt
    ws_ve.cell(row=row_idx, column=9, value=float(sales_data["totals"]["tax"])).number_format = money_fmt
    ws_ve.cell(row=row_idx, column=10, value=float(sales_data["totals"]["total"])).number_format = money_fmt
    for ci in range(1, 14):
        c = ws_ve.cell(row=row_idx, column=ci)
        c.font = font_bold
        c.fill = fill_subtotal
        c.border = total_border

    # Écritures comptables Ventes (PCGM) en dessous
    row_idx += 3
    ws_ve.cell(row=row_idx, column=1, value="ÉCRITURES COMPTABLES EN PARTIE DOUBLE (PLAN COMPTABLE MAROCAIN)").font = font_subtitle
    row_idx += 1
    ecr_headers = ["Date", "Journal", "N° Pièce", "Compte", "Intitulé du Compte", "Libellé de l'Écriture", "Débit (MAD)", "Crédit (MAD)"]
    for col_idx, h in enumerate(ecr_headers, start=1):
        c = ws_ve.cell(row=row_idx, column=col_idx, value=h)
        c.font = font_header
        c.fill = fill_header
        c.alignment = Alignment(horizontal="center" if col_idx in [1, 2, 3, 4] else "right" if col_idx >= 7 else "left")
        c.border = cell_border
    row_idx += 1

    total_deb = Decimal("0.00")
    total_crd = Decimal("0.00")
    for a in sales_data["accounting_entries"]:
        ws_ve.cell(row=row_idx, column=1, value=a["date"].strftime("%d/%m/%Y")).alignment = Alignment(horizontal="center")
        ws_ve.cell(row=row_idx, column=2, value=a["journal"]).alignment = Alignment(horizontal="center")
        ws_ve.cell(row=row_idx, column=3, value=a["piece"]).alignment = Alignment(horizontal="center")
        ws_ve.cell(row=row_idx, column=4, value=a["account"]).alignment = Alignment(horizontal="center")
        ws_ve.cell(row=row_idx, column=5, value=a["account_label"]).alignment = Alignment(horizontal="left")
        ws_ve.cell(row=row_idx, column=6, value=a["libelle"]).alignment = Alignment(horizontal="left")
        ws_ve.cell(row=row_idx, column=7, value=float(a["debit"])).number_format = money_fmt
        ws_ve.cell(row=row_idx, column=8, value=float(a["credit"])).number_format = money_fmt
        total_deb += a["debit"]
        total_crd += a["credit"]

        for ci in range(1, 9):
            c = ws_ve.cell(row=row_idx, column=ci)
            c.font = font_data
            c.border = cell_border
        row_idx += 1

    ws_ve.cell(row=row_idx, column=6, value="TOTAL ÉCRITURES").font = font_bold
    ws_ve.cell(row=row_idx, column=7, value=float(total_deb)).number_format = money_fmt
    ws_ve.cell(row=row_idx, column=8, value=float(total_crd)).number_format = money_fmt
    for ci in range(1, 9):
        c = ws_ve.cell(row=row_idx, column=ci)
        c.font = font_bold
        c.fill = fill_subtotal
        c.border = total_border

    # ==========================================
    # ONGLET 3 : JOURNAL DES RÈGLEMENTS (TR)
    # ==========================================
    ws_tr = wb.create_sheet(title="Journal des Règlements")
    ws_tr.views.sheetView[0].showGridLines = True

    ws_tr["A1"] = f"{company.name} — JOURNAL DES RÈGLEMENTS & ENCAISSEMENTS (TR)"
    ws_tr["A1"].font = font_title
    ws_tr["A2"] = f"Période : {period_label} | {len(payments_data['entries'])} encaissements"
    ws_tr["A2"].font = font_company

    tr_headers = [
        "Date Règlement", "N° Quittance", "Mode de Règlement", "Référence / N° Chèque",
        "Facture Concernée", "Client", "Montant Encaissé (MAD)", "Alerte Fiscale CGI (Art. 11-II)", "Remarques"
    ]
    for col_idx, h in enumerate(tr_headers, start=1):
        c = ws_tr.cell(row=4, column=col_idx, value=h)
        c.font = font_header
        c.fill = fill_header
        c.alignment = Alignment(horizontal="center" if col_idx in [1, 2, 3, 5] else "right" if col_idx == 7 else "left")
        c.border = cell_border

    row_idx = 5
    for p in payments_data["entries"]:
        ws_tr.cell(row=row_idx, column=1, value=p["date"].strftime("%d/%m/%Y")).alignment = Alignment(horizontal="center")
        ws_tr.cell(row=row_idx, column=2, value=f"REG-{p['payment_id']}").alignment = Alignment(horizontal="center")
        ws_tr.cell(row=row_idx, column=3, value=p["method_display"]).alignment = Alignment(horizontal="center")
        ws_tr.cell(row=row_idx, column=4, value=p["reference"]).alignment = Alignment(horizontal="left")
        ws_tr.cell(row=row_idx, column=5, value=p["invoice_number"]).alignment = Alignment(horizontal="center")
        ws_tr.cell(row=row_idx, column=6, value=p["customer_name"]).alignment = Alignment(horizontal="left")
        ws_tr.cell(row=row_idx, column=7, value=float(p["amount"])).number_format = money_fmt
        
        c_alert = ws_tr.cell(row=row_idx, column=8, value=p["fiscal_alert"])
        c_alert.alignment = Alignment(horizontal="left")
        if p.get("is_cash_over_ceiling"):
            c_alert.fill = fill_warning
            c_alert.font = font_warning
            
        ws_tr.cell(row=row_idx, column=9, value=p["notes"]).alignment = Alignment(horizontal="left")

        for ci in range(1, 10):
            c = ws_tr.cell(row=row_idx, column=ci)
            c.border = cell_border
            if ci != 8 or not p.get("is_cash_over_ceiling"):
                c.font = font_data
                if row_idx % 2 == 0:
                    c.fill = fill_zebra
        row_idx += 1

    ws_tr.cell(row=row_idx, column=6, value="TOTAL ENCAISSEMENTS").font = font_bold
    ws_tr.cell(row=row_idx, column=7, value=float(payments_data["totals"]["amount"])).number_format = money_fmt
    for ci in range(1, 10):
        c = ws_tr.cell(row=row_idx, column=ci)
        c.font = font_bold
        c.fill = fill_subtotal
        c.border = total_border

    # 4. ONGLET 4 : INVENTAIRE ET VALORISATION DES STOCKS (PCGE MAROC - COMPTE 3111)
    from apps.catalog.models import Article
    ws_st = wb.create_sheet(title="Inventaire & Stocks (3111)")
    ws_st.views.sheetView[0].showGridLines = True

    ws_st["A1"] = f"{company.name} - INVENTAIRE & VALORISATION DES STOCKS DE MARCHANDISES"
    ws_st["A1"].font = font_title
    ws_st["A2"] = "Arrêté d'inventaire | Conforme Plan Comptable Général des Entreprises (PCGE Maroc)"
    ws_st["A2"].font = font_subtitle

    headers_st = [
        "Compte PCGE",
        "Référence",
        "Désignation de la Marchandise",
        "Nature",
        "Quantité en Stock",
        "Coût Achat Unitaire HT (MAD)",
        "Valeur Totale Stock HT (MAD)",
        "Prix Vente Conseillé HT",
        "Marge Brute HT",
        "Statut Logistique",
    ]
    for col_idx, h in enumerate(headers_st, 1):
        cell = ws_st.cell(row=4, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.border = cell_border
        cell.alignment = Alignment(horizontal="center" if col_idx in [1, 2, 5, 10] else ("right" if col_idx in [6, 7, 8, 9] else "left"))

    articles = Article.objects.filter(is_active=True).order_by("reference")
    row_st_idx = 5
    total_valeur_stock = Decimal("0.00")

    for art in articles:
        val_stock = art.valeur_stock_ht
        total_valeur_stock += val_stock

        ws_st.cell(row=row_st_idx, column=1, value=art.compte_stock).alignment = Alignment(horizontal="center")
        ws_st.cell(row=row_st_idx, column=2, value=art.reference).alignment = Alignment(horizontal="center")
        ws_st.cell(row=row_st_idx, column=3, value=art.designation).alignment = Alignment(horizontal="left")
        ws_st.cell(row=row_st_idx, column=4, value=art.get_nature_produit_display()).alignment = Alignment(horizontal="left")
        ws_st.cell(row=row_st_idx, column=5, value=float(art.stock_actuel)).number_format = "#,##0.00"
        ws_st.cell(row=row_st_idx, column=6, value=float(art.prix_achat_ht)).number_format = money_fmt
        ws_st.cell(row=row_st_idx, column=7, value=float(val_stock)).number_format = money_fmt
        ws_st.cell(row=row_st_idx, column=8, value=float(art.prix_vente_ht)).number_format = money_fmt
        ws_st.cell(row=row_st_idx, column=9, value=float(art.marge_unitaire_ht)).number_format = money_fmt

        statut_str = "Rupture" if art.en_rupture else ("Alerte Seuil" if art.sous_alerte else "En Stock")
        ws_st.cell(row=row_st_idx, column=10, value=statut_str).alignment = Alignment(horizontal="center")

        for ci in range(1, 11):
            c = ws_st.cell(row=row_st_idx, column=ci)
            c.border = cell_border
            c.font = font_data
            if row_st_idx % 2 == 0:
                c.fill = fill_zebra
        row_st_idx += 1

    ws_st.cell(row=row_st_idx, column=3, value="TOTAL ACTIF CIRCULANT (STOCKS COMPTE 3111)").font = font_bold
    ws_st.cell(row=row_st_idx, column=7, value=float(total_valeur_stock)).number_format = money_fmt
    for ci in range(1, 11):
        c = ws_st.cell(row=row_st_idx, column=ci)
        c.font = font_bold
        c.fill = fill_subtotal
        c.border = total_border

    row_st_idx += 2
    ws_st.cell(row=row_st_idx, column=2, value="CONFORMITÉ COMPTABLE PCGE - ÉCRITURE D'INVENTAIRE / VARIATION DES STOCKS :").font = font_bold
    row_st_idx += 1
    ws_st.cell(row=row_st_idx, column=2, value=f"• Débit Compte 3111 (Marchandises en stock) : {float(total_valeur_stock):,.2f} MAD").font = font_company
    row_st_idx += 1
    ws_st.cell(row=row_st_idx, column=2, value=f"• Crédit Compte 6114 (Variation des stocks de marchandises) : {float(total_valeur_stock):,.2f} MAD").font = font_company


    # =========================================================================
    # ONGLET 5 : CHARGES NON DÉDUCTIBLES & RÉINTÉGRATIONS FISCALES (TABLEAU N°3 IS)
    # =========================================================================
    ws_reint = wb.create_sheet(title="Réintégrations IS (Tab 3)")
    ws_reint.views.sheetView[0].showGridLines = True

    ws_reint["A1"] = f"{company.name} - ÉTAT DES CHARGES NON DÉDUCTIBLES & RÉINTÉGRATIONS FISCALES (IS - TABLEAU N°3)"
    ws_reint["A1"].font = font_title
    ws_reint["A2"] = f"Période fiscale : {period_label} | Conforme CGI Maroc (Articles 10, 11 & 106) | {company.legal_notice()}"
    ws_reint["A2"].font = font_company

    if non_deductible_data is None and vat_data.get("start_date") and vat_data.get("end_date"):
        non_deductible_data = get_non_deductible_expenses_data(vat_data["start_date"], vat_data["end_date"])

    reint_totals = non_deductible_data.get("totals", {}) if non_deductible_data else {}
    tot_rec = reint_totals.get("total_recorded", Decimal("0.00"))
    tot_reint = reint_totals.get("total_reintegration", Decimal("0.00"))
    count_reint = reint_totals.get("count", 0)

    # Cartouches KPIs
    kpis_reint = [
        ("Total Charges avec Réintégration", tot_rec, "A4", "B5"),
        ("TOTAL RÉINTÉGRATIONS FISCALES (IS)", tot_reint, "D4", "E5"),
        ("Nombre de Dépenses Concernées", count_reint, "G4", "H5"),
    ]
    for label, val, top_left, bottom_right in kpis_reint:
        col1 = top_left[0]
        row1 = int(top_left[1])
        ws_reint[top_left] = label
        ws_reint[top_left].font = font_kpi_label
        ws_reint[f"{col1}{row1+1}"] = float(val) if isinstance(val, (int, float, Decimal)) else val
        ws_reint[f"{col1}{row1+1}"].font = font_kpi_val
        if isinstance(val, (Decimal, float)):
            ws_reint[f"{col1}{row1+1}"].number_format = money_fmt

    # Tableau récapitulatif par motif légal CGI
    ws_reint["A7"] = "SYNTHÈSE DES RÉINTÉGRATIONS PAR MOTIF LÉGAL (CGI MAROC)"
    ws_reint["A7"].font = font_subtitle

    reint_summary_headers = ["Motif Légal (CGI)", "Montant à Réintégrer (MAD)"]
    for col_idx, h in enumerate(reint_summary_headers, start=1):
        c = ws_reint.cell(row=8, column=col_idx, value=h)
        c.font = font_header
        c.fill = fill_header
        c.alignment = Alignment(horizontal="center" if col_idx == 2 else "left")

    row_sum_idx = 9
    summary_reasons = non_deductible_data.get("summary_reasons", []) if non_deductible_data else []
    for item in summary_reasons:
        ws_reint.cell(row=row_sum_idx, column=1, value=item["reason"]).font = font_bold
        ws_reint.cell(row=row_sum_idx, column=2, value=float(item["amount"])).number_format = money_fmt
        for ci in [1, 2]:
            c = ws_reint.cell(row=row_sum_idx, column=ci)
            c.border = cell_border
        row_sum_idx += 1

    if not summary_reasons:
        ws_reint.cell(row=row_sum_idx, column=1, value="Aucune charge non déductible sur la période").font = font_company
        for ci in [1, 2]:
            ws_reint.cell(row=row_sum_idx, column=ci).border = cell_border
        row_sum_idx += 1

    # Tableau détaillé
    row_dt_idx = row_sum_idx + 2
    ws_reint.cell(row=row_dt_idx, column=1, value="DÉTAIL DES CHARGES ET JUSTIFICATIONS FISCALES (TABLEAU N°3 LIASSE IS)").font = font_subtitle
    row_dt_idx += 1

    reint_dt_headers = [
        "N°", "Date", "Description / Pièce", "Catégorie", "Fournisseur", "ICE Fournisseur",
        "Montant Comptable TTC", "Montant Réintégré (IS)", "Motif Légal (CGI Maroc)", "Observations / Justification"
    ]
    for col_idx, h in enumerate(reint_dt_headers, start=1):
        c = ws_reint.cell(row=row_dt_idx, column=col_idx, value=h)
        c.font = font_header
        c.fill = fill_header
        c.alignment = Alignment(horizontal="center" if col_idx in [1, 2, 6, 7, 8] else "left")

    row_dt_idx += 1
    reint_rows = non_deductible_data.get("rows", []) if non_deductible_data else []
    for r in reint_rows:
        ws_reint.cell(row=row_dt_idx, column=1, value=r["order_num"]).alignment = Alignment(horizontal="center")
        ws_reint.cell(row=row_dt_idx, column=2, value=r["expense_date"].strftime("%d/%m/%Y")).alignment = Alignment(horizontal="center")
        ws_reint.cell(row=row_dt_idx, column=3, value=r["description"]).alignment = Alignment(horizontal="left")
        ws_reint.cell(row=row_dt_idx, column=4, value=r["category_name"]).alignment = Alignment(horizontal="left")
        ws_reint.cell(row=row_dt_idx, column=5, value=r["supplier_name"]).alignment = Alignment(horizontal="left")
        ws_reint.cell(row=row_dt_idx, column=6, value=r["supplier_ice"]).alignment = Alignment(horizontal="center")
        ws_reint.cell(row=row_dt_idx, column=7, value=float(r["amount"])).number_format = money_fmt
        ws_reint.cell(row=row_dt_idx, column=8, value=float(r["reintegration_amount"])).number_format = money_fmt
        ws_reint.cell(row=row_dt_idx, column=9, value=r["reason_label"]).alignment = Alignment(horizontal="left")
        ws_reint.cell(row=row_dt_idx, column=10, value=r["fiscal_notes"] or "—").alignment = Alignment(horizontal="left")

        for ci in range(1, 11):
            c = ws_reint.cell(row=row_dt_idx, column=ci)
            c.border = cell_border
            c.font = font_data
            if row_dt_idx % 2 == 0:
                c.fill = fill_zebra
        row_dt_idx += 1

    # Ligne de totalisation
    ws_reint.cell(row=row_dt_idx, column=3, value="TOTAL DES RÉINTÉGRATIONS EXTRA-COMPTABLES (IS)").font = font_bold
    ws_reint.cell(row=row_dt_idx, column=7, value=float(tot_rec)).number_format = money_fmt
    ws_reint.cell(row=row_dt_idx, column=8, value=float(tot_reint)).number_format = money_fmt
    for ci in range(1, 11):
        c = ws_reint.cell(row=row_dt_idx, column=ci)
        c.font = font_bold
        c.fill = fill_subtotal
        c.border = total_border

        # Auto-ajustement de la largeur des colonnes sur tous les onglets
    # ==========================================
    # ONGLET 6 : JOURNAL DES ACHATS & CHARGES (AC)
    # ==========================================
    ws_ac = wb.create_sheet(title="Journal Achats (AC)")
    ws_ac.views.sheetView[0].showGridLines = True

    ws_ac["A1"] = f"JOURNAL DES ACHATS & CHARGES (AC) — {period_label.upper()}"
    ws_ac["A1"].font = font_title
    ws_ac["A2"] = f"Société : {company.name} | Conforme PCGE Maroc & Article 106 CGI"
    ws_ac["A2"].font = font_company

    # Synthèse par compte PCGE
    ws_ac["A4"] = "SYNTHÈSE DES CHARGES PAR COMPTE PCGE"
    ws_ac["A4"].font = font_bold

    ac_kpi_headers = ["Compte PCGE", "Intitulé du Compte", "Nb Charges", "Base HT (MAD)", "TVA Récupérable (MAD)", "Total TTC (MAD)"]
    for col_idx, h in enumerate(ac_kpi_headers, start=1):
        cell = ws_ac.cell(row=5, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center" if col_idx in [1, 3] else ("right" if col_idx >= 4 else "left"), vertical="center")
        cell.border = cell_border

    row_ac_kpi = 6
    for item in purchases_data.get("summary_by_account", []):
        ws_ac.cell(row=row_ac_kpi, column=1, value=item["account"]).alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_kpi, column=2, value=item["name"]).alignment = Alignment(horizontal="left")
        ws_ac.cell(row=row_ac_kpi, column=3, value=item["count"]).alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_kpi, column=4, value=float(item["base_ht"])).number_format = money_fmt
        ws_ac.cell(row=row_ac_kpi, column=5, value=float(item["vat_amount"])).number_format = money_fmt
        ws_ac.cell(row=row_ac_kpi, column=6, value=float(item["total_ttc"])).number_format = money_fmt
        for ci in range(1, 7):
            c = ws_ac.cell(row=row_ac_kpi, column=ci)
            c.font = font_data
            c.border = cell_border
        row_ac_kpi += 1

    # Totaux synthèse
    ac_totals = purchases_data.get("totals", {})
    ws_ac.cell(row=row_ac_kpi, column=2, value="TOTAL DES CHARGES D'EXPLOITATION").font = font_bold
    ws_ac.cell(row=row_ac_kpi, column=3, value=ac_totals.get("count", 0)).alignment = Alignment(horizontal="center")
    ws_ac.cell(row=row_ac_kpi, column=4, value=float(ac_totals.get("total_ht", 0))).number_format = money_fmt
    ws_ac.cell(row=row_ac_kpi, column=5, value=float(ac_totals.get("recoverable_vat", 0))).number_format = money_fmt
    ws_ac.cell(row=row_ac_kpi, column=6, value=float(ac_totals.get("total_ttc", 0))).number_format = money_fmt
    for ci in range(1, 7):
        c = ws_ac.cell(row=row_ac_kpi, column=ci)
        c.font = font_bold
        c.fill = fill_subtotal
        c.border = total_border

    # Tableau détaillé des achats
    row_ac_dt = row_ac_kpi + 2
    ws_ac.cell(row=row_ac_dt, column=1, value="DÉTAIL DU JOURNAL DES ACHATS ET FRAIS GÉNÉRAUX").font = font_bold
    row_ac_dt += 1

    ac_dt_headers = [
        "N°", "Date", "N° Pièce", "Compte PCGE", "Catégorie", "Fournisseur", "ICE Fournisseur",
        "Description", "Règlement", "Montant HT (MAD)", "Taux TVA", "TVA Récupérable (MAD)",
        "Total TTC (MAD)", "Régime TVA (Art. 106)", "Déductibilité IS"
    ]
    for col_idx, h in enumerate(ac_dt_headers, start=1):
        cell = ws_ac.cell(row=row_ac_dt, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center" if col_idx in [1, 2, 3, 4, 7, 11] else ("right" if col_idx in [10, 12, 13] else "left"), vertical="center")
        cell.border = cell_border

    row_ac_dt += 1
    for r in purchases_data.get("rows", []):
        ws_ac.cell(row=row_ac_dt, column=1, value=r["order_num"]).alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_dt, column=2, value=r["expense_date"].strftime("%d/%m/%Y") if isinstance(r["expense_date"], date) else str(r["expense_date"])).alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_dt, column=3, value=r["piece"]).alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_dt, column=4, value=r["pcge_account"]).alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_dt, column=5, value=r["category_name"]).alignment = Alignment(horizontal="left")
        ws_ac.cell(row=row_ac_dt, column=6, value=r["supplier_name"]).alignment = Alignment(horizontal="left")
        ws_ac.cell(row=row_ac_dt, column=7, value=r["supplier_ice"]).alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_dt, column=8, value=r["description"]).alignment = Alignment(horizontal="left")
        ws_ac.cell(row=row_ac_dt, column=9, value=r["payment_method"]).alignment = Alignment(horizontal="left")
        ws_ac.cell(row=row_ac_dt, column=10, value=float(r["base_ht"])).number_format = money_fmt
        ws_ac.cell(row=row_ac_dt, column=11, value=f"{r['tax_rate']:.0f} %").alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_dt, column=12, value=float(r["vat_amount"])).number_format = money_fmt
        ws_ac.cell(row=row_ac_dt, column=13, value=float(r["total_ttc"])).number_format = money_fmt
        ws_ac.cell(row=row_ac_dt, column=14, value=r["vat_status_label"]).alignment = Alignment(horizontal="center")
        ws_ac.cell(row=row_ac_dt, column=15, value="Déductible" if r["is_tax_deductible"] else "À réintégrer (Tab 3)").alignment = Alignment(horizontal="center")

        for ci in range(1, 16):
            c = ws_ac.cell(row=row_ac_dt, column=ci)
            c.font = font_data
            c.border = cell_border
            if row_ac_dt % 2 == 0:
                c.fill = fill_zebra
        row_ac_dt += 1

    # Total ligne détaillée
    ws_ac.cell(row=row_ac_dt, column=8, value="TOTAL DU JOURNAL DES ACHATS").font = font_bold
    ws_ac.cell(row=row_ac_dt, column=10, value=float(ac_totals.get("total_ht", 0))).number_format = money_fmt
    ws_ac.cell(row=row_ac_dt, column=12, value=float(ac_totals.get("recoverable_vat", 0))).number_format = money_fmt
    ws_ac.cell(row=row_ac_dt, column=13, value=float(ac_totals.get("total_ttc", 0))).number_format = money_fmt
    for ci in range(1, 16):
        c = ws_ac.cell(row=row_ac_dt, column=ci)
        c.font = font_bold
        c.fill = fill_subtotal
        c.border = total_border

    for sheet in [ws_tva, ws_ve, ws_tr, ws_st, ws_reint, ws_ac]:
        for col in sheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.row in [1, 2]:
                    continue
                if cell.value:
                    val_str = str(cell.value)
                    max_len = max(max_len, len(val_str))
            sheet.column_dimensions[col_letter].width = max(max_len + 3, 12)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_csv_export(headers, rows_dict_list, key_order):
    """
    Export CSV encodé en UTF-8 avec BOM (utf-8-sig) et séparateur point-virgule (;)
    pour ouverture directe et native dans Microsoft Excel sans assistant d'import.
    """
    buffer = BytesIO()
    # Écriture du BOM UTF-8
    buffer.write(b"\xef\xbb\xbf")
    writer_wrapper = BytesIO()

    # On utilise un buffer texte temporaire
    lines = []
    lines.append(";".join(f'"{h}"' for h in headers))
    for r in rows_dict_list:
        row_vals = []
        for k in key_order:
            val = r.get(k, "")
            if isinstance(val, (int, float, Decimal)):
                val_str = f"{val:.2f}".replace(".", ",")
            elif isinstance(val, date):
                val_str = val.strftime("%d/%m/%Y")
            else:
                val_str = str(val).replace('"', '""')
            row_vals.append(f'"{val_str}"')
        lines.append(";".join(row_vals))

    return "\r\n".join(lines).encode("utf-8-sig")


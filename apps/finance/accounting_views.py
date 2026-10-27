from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .accounting_services import (
    build_complete_fiduciary_pack_xlsx,
    build_csv_export,
    get_accounting_period_range,
    get_moroccan_vat_report_data,
    get_payments_journal_data,
    get_sales_journal_data,
)
from .models import CompanySettings


def _get_filter_params(request):
    today = timezone.localdate()
    year = request.GET.get("year")
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = today.year

    period_type = request.GET.get("period_type", "quarter")
    if period_type not in {"month", "quarter", "year"}:
        period_type = "quarter"

    default_period_value = ((today.month - 1) // 3 + 1) if period_type == "quarter" else today.month
    period_value = request.GET.get("period_value", default_period_value)
    try:
        period_value = int(period_value)
    except (TypeError, ValueError):
        period_value = default_period_value

    regime = request.GET.get("regime", "encaissement")
    if regime not in {"encaissement", "debit"}:
        regime = "encaissement"

    active_tab = request.GET.get("tab", "tva")
    if active_tab not in {"tva", "ventes", "reglements"}:
        active_tab = "tva"

    return year, period_type, period_value, regime, active_tab


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def accounting_dashboard(request):
    year, period_type, period_value, regime, active_tab = _get_filter_params(request)
    start_date, end_date, period_label = get_accounting_period_range(year, period_type, period_value)

    vat_data = get_moroccan_vat_report_data(start_date, end_date, regime=regime)
    sales_data = get_sales_journal_data(start_date, end_date)
    payments_data = get_payments_journal_data(start_date, end_date)
    company = CompanySettings.get_settings()

    current_year = timezone.localdate().year
    available_years = list(range(current_year - 3, current_year + 2))

    months_list = [
        (1, "Janvier"), (2, "Février"), (3, "Mars"), (4, "Avril"),
        (5, "Mai"), (6, "Juin"), (7, "Juillet"), (8, "Août"),
        (9, "Septembre"), (10, "Octobre"), (11, "Novembre"), (12, "Décembre"),
    ]
    quarters_list = [
        (1, "T1 (Janvier - Mars)"),
        (2, "T2 (Avril - Juin)"),
        (3, "T3 (Juillet - Septembre)"),
        (4, "T4 (Octobre - Décembre)"),
    ]

    context = {
        "company": company,
        "year": year,
        "period_type": period_type,
        "period_value": period_value,
        "regime": regime,
        "active_tab": active_tab,
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
        "available_years": available_years,
        "months_list": months_list,
        "quarters_list": quarters_list,
        "vat_data": vat_data,
        "sales_data": sales_data,
        "payments_data": payments_data,
    }
    return render(request, "finance/accounting/dashboard.html", context)


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def export_fiduciary_pack_xlsx(request):
    year, period_type, period_value, regime, _ = _get_filter_params(request)
    start_date, end_date, period_label = get_accounting_period_range(year, period_type, period_value)

    vat_data = get_moroccan_vat_report_data(start_date, end_date, regime=regime)
    sales_data = get_sales_journal_data(start_date, end_date)
    payments_data = get_payments_journal_data(start_date, end_date)

    content = build_complete_fiduciary_pack_xlsx(period_label, vat_data, sales_data, payments_data)
    safe_label = period_label.replace(" ", "_").replace("(", "").replace(")", "").replace("-", "_")
    filename = f"Pack_Fiduciaire_{safe_label}.xlsx"

    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def export_vat_csv(request):
    year, period_type, period_value, regime, _ = _get_filter_params(request)
    start_date, end_date, period_label = get_accounting_period_range(year, period_type, period_value)
    vat_data = get_moroccan_vat_report_data(start_date, end_date, regime=regime)

    headers = [
        "N° Ordre", "N° Facture", "Date Facture", "Client", "ICE Client", "IF Client",
        "Date Règlement", "Mode Règlement", "Référence", "Base HT (MAD)", "Taux TVA (%)", "TVA Collectée (MAD)", "TTC Encaissé (MAD)"
    ]
    key_order = [
        "order_num", "invoice_number", "invoice_date", "customer_name", "customer_ice", "customer_if",
        "payment_date", "payment_method", "payment_reference", "base_ht", "tax_rate", "tax_amount", "total_ttc"
    ]
    csv_bytes = build_csv_export(headers, vat_data["rows"], key_order)
    safe_label = period_label.replace(" ", "_")
    filename = f"Declaration_TVA_{safe_label}.csv"

    response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def export_sales_csv(request):
    year, period_type, period_value, _, _ = _get_filter_params(request)
    start_date, end_date, period_label = get_accounting_period_range(year, period_type, period_value)
    sales_data = get_sales_journal_data(start_date, end_date)

    headers = [
        "Date Pièce", "N° Facture", "Client", "ICE Client", "Total Brut HT (MAD)",
        "Remises (MAD)", "Total Net HT (MAD)", "TVA (MAD)", "Total TTC (MAD)", "Montant Réglé (MAD)", "Solde Restant (MAD)", "Statut"
    ]
    key_order = [
        "date", "number", "customer_name", "customer_ice", "gross",
        "discount", "net", "tax", "total", "paid", "balance", "status_display"
    ]
    csv_bytes = build_csv_export(headers, sales_data["entries"], key_order)
    safe_label = period_label.replace(" ", "_")
    filename = f"Journal_Ventes_{safe_label}.csv"

    response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def export_payments_csv(request):
    year, period_type, period_value, _, _ = _get_filter_params(request)
    start_date, end_date, period_label = get_accounting_period_range(year, period_type, period_value)
    payments_data = get_payments_journal_data(start_date, end_date)

    headers = [
        "Date Règlement", "N° Reçu", "Mode de Règlement", "Référence",
        "N° Facture", "Client", "Montant Encaissé (MAD)", "Alerte Fiscale CGI (Art. 11-II)", "Remarques"
    ]
    key_order = [
        "date", "payment_id", "method_display", "reference",
        "invoice_number", "customer_name", "amount", "fiscal_alert", "notes"
    ]
    csv_bytes = build_csv_export(headers, payments_data["entries"], key_order)
    safe_label = period_label.replace(" ", "_")
    filename = f"Journal_Reglements_{safe_label}.csv"

    response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


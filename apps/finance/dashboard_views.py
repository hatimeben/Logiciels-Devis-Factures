from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from .analytics_services import get_executive_dashboard_context
from .models import Expense, ExpensePayment, Invoice, Payment, Quote
from .money import round_money

ZERO = Decimal("0.00")


@login_required
def dashboard(request):
    today = timezone.localdate()
    first_day_month = today.replace(day=1)
    first_day_year = today.replace(month=1, day=1)

    # Calculs analytiques & BI exécutifs
    executive = get_executive_dashboard_context(today=today)
    activity_data = executive["activity"]
    conversion_data = executive["conversion"]
    cashflow_data = executive["cashflow"]

    # Invoices stats (historique & filtres mensuels/annuels)
    all_invoices = list(
        Invoice.objects.exclude(status=Invoice.Status.DRAFT)
        .select_related("customer")
        .prefetch_related("lines", "payment_allocations")
    )
    invoiced_global = sum((inv.total_ttc for inv in all_invoices), ZERO)
    invoiced_month = sum(
        (inv.total_ttc for inv in all_invoices if inv.issue_date and inv.issue_date >= first_day_month),
        ZERO,
    )
    invoiced_year = sum(
        (inv.total_ttc for inv in all_invoices if inv.issue_date and inv.issue_date >= first_day_year),
        ZERO,
    )

    # Payments received
    payments_global = Payment.objects.aggregate(total=Sum("amount"))["total"] or ZERO
    payments_month = (
        Payment.objects.filter(payment_date__gte=first_day_month).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    payments_year = (
        Payment.objects.filter(payment_date__gte=first_day_year).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )

    # Expenses & Supplier payments
    expenses_global = Expense.objects.aggregate(total=Sum("amount"))["total"] or ZERO
    expenses_month = (
        Expense.objects.filter(expense_date__gte=first_day_month).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )
    expense_paid_global = ExpensePayment.objects.aggregate(total=Sum("amount"))["total"] or ZERO
    expense_paid_month = (
        ExpensePayment.objects.filter(payment_date__gte=first_day_month).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )

    # Net Cash flow (Collections - Paid expenses)
    net_cashflow_global = round_money(payments_global - expense_paid_global)
    net_cashflow_month = round_money(payments_month - expense_paid_month)

    # Recent activity
    recent_invoices = (
        Invoice.objects.exclude(status=Invoice.Status.DRAFT)
        .select_related("customer")
        .prefetch_related("lines")
        .order_by("-issue_date", "-id")[:5]
    )
    recent_quotes = Quote.objects.select_related("customer").order_by("-created_at")[:5]
    recent_payments = (
        Payment.objects.select_related("customer")
        .prefetch_related("allocations__invoice")
        .order_by("-payment_date", "-id")[:5]
    )
    recent_expenses = Expense.objects.select_related("category", "supplier").order_by("-expense_date", "-id")[:5]

    return render(
        request,
        "finance/dashboard.html",
        {
            # Temporal & Brand
            "today": today,
            "brand_slogan": executive["brand_slogan"],

            # BLOC 1 : CA par Ligne d'Activité
            "activity": activity_data,

            # BLOC 2 : Taux de Conversion Commercial
            "conversion": conversion_data,

            # BLOC 3 : Trésorerie & Encours Clients
            "cashflow": cashflow_data,

            # Données rétro-compatibles pour les vues existantes et tests
            "invoiced_global": round_money(invoiced_global),
            "invoiced_month": round_money(invoiced_month),
            "invoiced_year": round_money(invoiced_year),
            "pending_receivables": cashflow_data["total_remaining_due"],
            "overdue_invoices": cashflow_data["overdue_invoices"],
            "overdue_count": cashflow_data["overdue_count"],
            "overdue_total": cashflow_data["overdue_total"],
            "payments_global": round_money(payments_global),
            "payments_month": round_money(payments_month),
            "payments_year": round_money(payments_year),
            "expenses_global": round_money(expenses_global),
            "expenses_month": round_money(expenses_month),
            "expense_paid_global": round_money(expense_paid_global),
            "expense_paid_month": round_money(expense_paid_month),
            "net_cashflow_global": net_cashflow_global,
            "net_cashflow_month": net_cashflow_month,
            "quotes_count": conversion_data["total_quotes_count"],
            "quotes_draft": conversion_data["draft_count"],
            "quotes_sent": conversion_data["negotiation_count"],
            "quotes_accepted": conversion_data["won_count"],
            "quotes_rejected": conversion_data["rejected_count"],
            "conversion_rate": conversion_data["conversion_rate"],

            # Flux récents
            "recent_invoices": recent_invoices,
            "recent_quotes": recent_quotes,
            "recent_payments": recent_payments,
            "recent_expenses": recent_expenses,
        },
    )

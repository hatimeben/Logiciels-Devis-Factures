from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from .models import Expense, ExpensePayment, Invoice, Payment, Quote
from .money import round_money


@login_required
def dashboard(request):
    today = timezone.localdate()
    first_day_month = today.replace(day=1)
    first_day_year = today.replace(month=1, day=1)

    # Invoices stats
    all_invoices = list(
        Invoice.objects.exclude(status=Invoice.Status.DRAFT)
        .select_related("customer")
        .prefetch_related("lines", "payment_allocations")
    )
    invoiced_global = sum((inv.total_ttc for inv in all_invoices), Decimal("0.00"))
    invoiced_month = sum(
        (inv.total_ttc for inv in all_invoices if inv.issue_date and inv.issue_date >= first_day_month),
        Decimal("0.00"),
    )
    invoiced_year = sum(
        (inv.total_ttc for inv in all_invoices if inv.issue_date and inv.issue_date >= first_day_year),
        Decimal("0.00"),
    )
    pending_receivables = sum((inv.remaining_balance for inv in all_invoices), Decimal("0.00"))

    # Overdue invoices
    overdue_invoices = [
        inv
        for inv in all_invoices
        if inv.remaining_balance > 0
        and inv.due_date
        and inv.due_date < today
    ]
    overdue_total = sum((inv.remaining_balance for inv in overdue_invoices), Decimal("0.00"))

    # Payments received (Client collections)
    payments_global = Payment.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    payments_month = (
        Payment.objects.filter(payment_date__gte=first_day_month).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    payments_year = (
        Payment.objects.filter(payment_date__gte=first_day_year).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    # Expenses & Supplier payments
    expenses_global = Expense.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    expenses_month = (
        Expense.objects.filter(expense_date__gte=first_day_month).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    expense_paid_global = ExpensePayment.objects.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    expense_paid_month = (
        ExpensePayment.objects.filter(payment_date__gte=first_day_month).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    # Net Cash flow (Collections - Paid expenses)
    net_cashflow_global = round_money(payments_global - expense_paid_global)
    net_cashflow_month = round_money(payments_month - expense_paid_month)

    # Quotes pipeline
    all_quotes = list(Quote.objects.all())
    quotes_count = len(all_quotes)
    quotes_sent = sum(1 for q in all_quotes if q.status == Quote.Status.SENT)
    quotes_accepted = sum(1 for q in all_quotes if q.status in {Quote.Status.ACCEPTED, Quote.Status.CONVERTED})
    quotes_rejected = sum(1 for q in all_quotes if q.status == Quote.Status.REJECTED)
    quotes_draft = sum(1 for q in all_quotes if q.status == Quote.Status.DRAFT)

    decided_quotes = quotes_accepted + quotes_rejected + quotes_sent
    conversion_rate = (
        round((quotes_accepted / decided_quotes) * 100, 1)
        if decided_quotes > 0
        else 0
    )

    # Recent activity
    recent_invoices = (
        Invoice.objects.exclude(status=Invoice.Status.DRAFT)
        .select_related("customer")
        .prefetch_related("lines")
        .order_by("-issue_date", "-id")[:5]
    )
    recent_quotes = Quote.objects.select_related("customer").order_by("-created_at")[:5]
    recent_payments = Payment.objects.select_related("customer").prefetch_related("allocations__invoice").order_by("-payment_date", "-id")[:5]
    recent_expenses = Expense.objects.select_related("category", "supplier").order_by("-expense_date", "-id")[:5]

    return render(
        request,
        "finance/dashboard.html",
        {
            "today": today,
            "invoiced_global": round_money(invoiced_global),
            "invoiced_month": round_money(invoiced_month),
            "invoiced_year": round_money(invoiced_year),
            "pending_receivables": round_money(pending_receivables),
            "overdue_invoices": overdue_invoices[:5],
            "overdue_count": len(overdue_invoices),
            "overdue_total": round_money(overdue_total),
            "payments_global": round_money(payments_global),
            "payments_month": round_money(payments_month),
            "payments_year": round_money(payments_year),
            "expenses_global": round_money(expenses_global),
            "expenses_month": round_money(expenses_month),
            "expense_paid_global": round_money(expense_paid_global),
            "expense_paid_month": round_money(expense_paid_month),
            "net_cashflow_global": net_cashflow_global,
            "net_cashflow_month": net_cashflow_month,
            "quotes_count": quotes_count,
            "quotes_draft": quotes_draft,
            "quotes_sent": quotes_sent,
            "quotes_accepted": quotes_accepted,
            "quotes_rejected": quotes_rejected,
            "conversion_rate": conversion_rate,
            "recent_invoices": recent_invoices,
            "recent_quotes": recent_quotes,
            "recent_payments": recent_payments,
            "recent_expenses": recent_expenses,
        },
    )


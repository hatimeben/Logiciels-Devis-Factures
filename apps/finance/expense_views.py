from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.catalog.models import ExpenseCategory
from apps.partners.models import Supplier
from .expense_forms import ExpenseForm, ExpensePaymentForm
from .models import Expense, ExpenseHistory, ExpensePayment


@login_required
@permission_required("finance.view_expense", raise_exception=True)
def expense_list(request):
    expenses = Expense.objects.select_related("category", "supplier", "project").prefetch_related("payments")
    search = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip()
    supplier_id = request.GET.get("supplier", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if search:
        expenses = expenses.filter(
            Q(description__icontains=search)
            | Q(supplier__name__icontains=search)
            | Q(category__name__icontains=search)
        )
    if category_id.isdigit():
        expenses = expenses.filter(category_id=int(category_id))
    if supplier_id.isdigit():
        expenses = expenses.filter(supplier_id=int(supplier_id))
    for value, field, label in [
        (date_from, "expense_date__gte", "Date de début"),
        (date_to, "expense_date__lte", "Date de fin"),
    ]:
        if value:
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                messages.error(request, f"{label} invalide.")
            else:
                expenses = expenses.filter(**{field: parsed})

    expense_items = list(expenses)
    if status == "paid":
        expense_items = [e for e in expense_items if e.remaining_balance == 0]
    elif status == "partially_paid":
        expense_items = [e for e in expense_items if e.amount_paid > 0 and e.remaining_balance > 0]
    elif status == "unpaid":
        expense_items = [e for e in expense_items if e.amount_paid == 0]

    page = Paginator(expense_items, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "finance/expenses/list.html",
        {
            "page": page,
            "categories": ExpenseCategory.objects.filter(is_active=True).order_by("name"),
            "suppliers": Supplier.objects.filter(is_active=True).order_by("name"),
            "filters": request.GET,
            "statuses": [
                ("unpaid", "À payer"),
                ("partially_paid", "Partiellement payée"),
                ("paid", "Payée"),
            ],
        },
    )


@login_required
@permission_required("finance.add_expense", raise_exception=True)
def expense_create(request):
    form = ExpenseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.audit_actor = request.user
        try:
            expense.save()
        except ValidationError as error:
            messages.error(request, "La dépense n'a pas été enregistrée : " + " ".join(error.messages))
        else:
            messages.success(request, f"La dépense « {expense.description} » a été enregistrée.")
            return redirect("expense-detail", expense_id=expense.pk)
    return render(
        request,
        "finance/expenses/form.html",
        {"form": form, "expense": None, "page_title": "Nouvelle dépense"},
    )


@login_required
@permission_required("finance.view_expense", raise_exception=True)
def expense_detail(request, expense_id):
    expense = get_object_or_404(
        Expense.objects.select_related("category", "supplier", "project").prefetch_related("payments"),
        pk=expense_id,
    )
    payments = expense.payments.all().order_by("-payment_date", "-id")
    history = ExpenseHistory.objects.filter(expense=expense).select_related("actor").order_by("-created_at")
    can_change = request.user.has_perm("finance.change_expense")
    can_add_payment = (
        request.user.has_perm("finance.add_expensepayment")
        and expense.remaining_balance > Decimal("0.00")
    )
    return render(
        request,
        "finance/expenses/detail.html",
        {
            "expense": expense,
            "payments": payments,
            "history": history,
            "can_change": can_change,
            "can_add_payment": can_add_payment,
        },
    )


@login_required
@permission_required("finance.change_expense", raise_exception=True)
def expense_edit(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    form = ExpenseForm(request.POST or None, request.FILES or None, instance=expense)
    if request.method == "POST" and form.is_valid():
        expense = form.save(commit=False)
        expense.audit_actor = request.user
        try:
            expense.save()
        except ValidationError as error:
            messages.error(request, "La dépense n'a pas été modifiée : " + " ".join(error.messages))
        else:
            messages.success(request, "La dépense a été mise à jour.")
            return redirect("expense-detail", expense_id=expense.pk)
    return render(
        request,
        "finance/expenses/form.html",
        {"form": form, "expense": expense, "page_title": f"Modifier {expense.description}"},
    )


@login_required
@permission_required("finance.add_expensepayment", raise_exception=True)
def expense_payment(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    remaining = expense.remaining_balance
    if remaining <= Decimal("0.00"):
        messages.info(request, "Cette dépense est déjà entièrement payée.")
        return redirect("expense-detail", expense_id=expense.pk)

    form = ExpensePaymentForm(request.POST or None, max_amount=remaining)
    if request.method == "POST" and form.is_valid():
        payment = ExpensePayment(
            expense=expense,
            payment_date=form.cleaned_data["payment_date"],
            amount=form.cleaned_data["amount"],
            method=form.cleaned_data["method"],
            reference=form.cleaned_data.get("reference", ""),
            notes=form.cleaned_data.get("notes", ""),
            idempotency_key=form.cleaned_data["idempotency_key"],
        )
        payment.audit_actor = request.user
        try:
            payment.save()
        except ValidationError as error:
            messages.error(request, "Paiement impossible : " + " ".join(error.messages))
        else:
            messages.success(request, f"Paiement de {payment.amount} MAD enregistré pour la dépense.")
            if payment.is_cash_over_ceiling:
                messages.warning(
                    request,
                    payment.get_cgi_alert_message()
                )
            return redirect("expense-detail", expense_id=expense.pk)
    return render(
        request,
        "finance/expenses/payment.html",
        {"expense": expense, "form": form, "remaining": remaining},
    )


@login_required
@permission_required("finance.view_expense", raise_exception=True)
def expense_receipt(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    if not expense.receipt:
        raise Http404("Aucun justificatif associé à cette dépense.")
    return FileResponse(expense.receipt.open("rb"))


from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.partners.models import Customer
from .invoice_forms import InvoiceDraftForm, InvoiceForm, InvoiceLineFormSet
from .invoice_pdf import build_invoice_pdf
from .invoice_services import (
    convert_quote_to_invoice,
    issue_invoice,
    save_invoice_draft,
    update_invoice_draft,
)
from .models import DocumentHistory, Invoice, Payment, Quote
from .payment_forms import InvoicePaymentForm
from .payment_services import record_invoice_payment
from .quote_calculations import calculate_quote_totals


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def invoice_list(request):
    invoices = Invoice.objects.select_related("customer", "quote").prefetch_related("lines", "payment_allocations")
    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    customer_id = request.GET.get("customer", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    if search:
        invoices = invoices.filter(
            Q(number__icontains=search)
            | Q(customer__name__icontains=search)
            | Q(quote__number__icontains=search)
        )
    if status in Invoice.Status.values:
        invoices = invoices.filter(status=status)
    if customer_id.isdigit():
        invoices = invoices.filter(customer_id=int(customer_id))
    for value, field, label in (
        (date_from, "issue_date__gte", "Date de début"),
        (date_to, "issue_date__lte", "Date de fin"),
    ):
        if value:
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                messages.error(request, f"{label} invalide.")
            else:
                invoices = invoices.filter(**{field: parsed})
    page = Paginator(invoices, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "finance/invoices/list.html",
        {
            "page": page,
            "statuses": Invoice.Status.choices,
            "customers": Customer.objects.filter(is_active=True).order_by("name"),
            "filters": request.GET,
        },
    )


@login_required
@permission_required("finance.add_invoice", raise_exception=True)
def invoice_create(request):
    invoice = Invoice()
    form = InvoiceForm(request.POST or None)
    formset = InvoiceLineFormSet(request.POST or None, instance=invoice, prefix="lines")

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        invoice = form.save(commit=False)
        try:
            invoice = save_invoice_draft(invoice, formset, request.user)
        except ValidationError as error:
            messages.error(request, "La facture n'a pas été enregistrée : " + " ".join(error.messages))
        else:
            messages.success(request, "La facture brouillon a été enregistrée.")
            return redirect("invoice-detail", invoice_id=invoice.pk)
    return render(
        request,
        "finance/invoices/form.html",
        {
            "form": form,
            "formset": formset,
            "invoice": None,
            "page_title": "Nouvelle facture",
        },
    )


@require_POST
@login_required
@permission_required(
    ("finance.add_invoice", "finance.change_quote"),
    raise_exception=True,
)
def invoice_convert(request, quote_id):
    try:
        invoice = convert_quote_to_invoice(quote_id, request.user)
    except (Quote.DoesNotExist, ValidationError) as error:
        detail = (
            " ".join(error.messages)
            if isinstance(error, ValidationError)
            else "Le devis demandé n'existe pas."
        )
        messages.error(request, "Conversion impossible : " + detail)
        return redirect("quote-list")
    messages.success(request, "Le devis accepté a été copié dans une facture brouillon.")
    return redirect("invoice-detail", invoice_id=invoice.pk)


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer", "quote", "project").prefetch_related(
            "lines__service", "lines__tax_rate", "payment_allocations__payment"
        ),
        pk=invoice_id,
    )
    lines = list(invoice.lines.all())
    totals = calculate_quote_totals(lines)
    has_discount = any(
        line.discount_value and line.discount_value > Decimal("0.00")
        for line in lines
    )
    for line in lines:
        line.amounts = calculate_quote_totals([line])
    customer_snapshot = invoice.customer_snapshot or {
        "name": invoice.customer.name,
        "email": invoice.customer.email,
        "phone": invoice.customer.phone,
        "address": invoice.customer.address,
        "city": invoice.customer.city,
        "country": invoice.customer.country,
        "tax_identifier": invoice.customer.tax_identifier,
    }
    history = DocumentHistory.objects.filter(invoice=invoice).select_related("actor")
    allocations = invoice.payment_allocations.select_related("payment").order_by(
        "-payment__payment_date", "-id"
    )
    can_add_payment = (
        request.user.has_perm("finance.add_payment")
        and invoice.status in {Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE}
        and invoice.remaining_balance > 0
    )
    return render(
        request,
        "finance/invoices/detail.html",
        {
            "invoice": invoice,
            "lines": lines,
            "totals": totals,
            "has_discount": has_discount,
            "customer_snapshot": customer_snapshot,
            "history": history,
            "allocations": allocations,
            "can_change": request.user.has_perm("finance.change_invoice"),
            "can_add_payment": can_add_payment,
        },
    )


@login_required
@permission_required("finance.change_invoice", raise_exception=True)
def invoice_edit(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    if invoice.status != Invoice.Status.DRAFT:
        messages.error(request, "Seules les factures brouillon peuvent être modifiées.")
        return redirect("invoice-detail", invoice_id=invoice.pk)

    has_lines_in_post = request.method == "POST" and "lines-TOTAL_FORMS" in request.POST
    form = InvoiceForm(request.POST or None, instance=invoice)
    formset = InvoiceLineFormSet(
        request.POST if has_lines_in_post else None,
        instance=invoice,
        prefix="lines",
    )

    if request.method == "POST":
        if has_lines_in_post:
            if form.is_valid() and formset.is_valid():
                invoice = form.save(commit=False)
                try:
                    invoice = save_invoice_draft(invoice, formset, request.user)
                except ValidationError as error:
                    messages.error(request, "La facture n'a pas été modifiée : " + " ".join(error.messages))
                else:
                    messages.success(request, "La facture brouillon a été mise à jour.")
                    return redirect("invoice-detail", invoice_id=invoice.pk)
        else:
            if form.is_valid():
                try:
                    invoice = update_invoice_draft(invoice, form, request.user)
                except ValidationError as error:
                    messages.error(request, "La facture n'a pas été modifiée : " + " ".join(error.messages))
                else:
                    messages.success(request, "La facture brouillon a été mise à jour.")
                    return redirect("invoice-detail", invoice_id=invoice.pk)

    return render(
        request,
        "finance/invoices/form.html",
        {
            "form": form,
            "formset": formset,
            "invoice": invoice,
            "page_title": f"Modifier {invoice}",
        },
    )


@require_POST
@login_required
@permission_required("finance.change_invoice", raise_exception=True)
def invoice_issue(request, invoice_id):
    try:
        invoice = issue_invoice(invoice_id, request.user)
    except ValidationError as error:
        messages.error(request, "Émission impossible : " + " ".join(error.messages))
        return redirect("invoice-detail", invoice_id=invoice_id)
    messages.success(request, f"La facture {invoice.number} a été émise.")
    return redirect("invoice-detail", invoice_id=invoice.pk)


@login_required
@permission_required("finance.add_payment", raise_exception=True)
def invoice_payment(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    if invoice.status not in {
        Invoice.Status.ISSUED,
        Invoice.Status.PARTIALLY_PAID,
        Invoice.Status.OVERDUE,
    }:
        messages.error(request, "Seules les factures émises peuvent recevoir un règlement.")
        return redirect("invoice-detail", invoice_id=invoice.pk)

    remaining = invoice.remaining_balance
    if remaining <= 0:
        messages.info(request, "Cette facture est déjà entièrement réglée.")
        return redirect("invoice-detail", invoice_id=invoice.pk)

    form = InvoicePaymentForm(request.POST or None, max_amount=remaining)
    if request.method == "POST" and form.is_valid():
        try:
            payment, allocation = record_invoice_payment(
                invoice_id=invoice.pk,
                amount=form.cleaned_data["amount"],
                payment_date=form.cleaned_data["payment_date"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data.get("reference", ""),
                notes=form.cleaned_data.get("notes", ""),
                actor=request.user,
            )
        except ValidationError as error:
            messages.error(request, "Enregistrement impossible : " + " ".join(error.messages))
        else:
            messages.success(
                request,
                f"Règlement de {allocation.amount} MAD enregistré sur la facture {invoice.number}."
            )
            return redirect("invoice-detail", invoice_id=invoice.pk)

    return render(
        request,
        "finance/invoices/payment.html",
        {
            "invoice": invoice,
            "form": form,
            "remaining": remaining,
        },
    )


@login_required
@permission_required("finance.view_payment", raise_exception=True)
def payment_list(request):
    payments = Payment.objects.select_related("customer").prefetch_related("allocations__invoice")
    search = request.GET.get("q", "").strip()
    customer_id = request.GET.get("customer", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if search:
        payments = payments.filter(
            Q(reference__icontains=search)
            | Q(customer__name__icontains=search)
            | Q(allocations__invoice__number__icontains=search)
        ).distinct()
    if customer_id.isdigit():
        payments = payments.filter(customer_id=int(customer_id))
    for value, field, label in [
        (date_from, "payment_date__gte", "Date de début"),
        (date_to, "payment_date__lte", "Date de fin"),
    ]:
        if value:
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                messages.error(request, f"{label} invalide.")
            else:
                payments = payments.filter(**{field: parsed})

    page = Paginator(payments, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "finance/payments/list.html",
        {
            "page": page,
            "customers": Customer.objects.filter(is_active=True).order_by("name"),
            "filters": request.GET,
        },
    )


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def invoice_pdf(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer").prefetch_related("lines", "payment_allocations"),
        pk=invoice_id,
    )
    content = build_invoice_pdf(invoice)
    filename = invoice.number or f"facture-brouillon-{invoice.pk}"
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response

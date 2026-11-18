import re
from datetime import date
from decimal import Decimal, InvalidOperation

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
from .document_filename import get_document_download_filename
from .invoice_docx import build_invoice_docx
from .invoice_pdf import build_invoice_pdf
from .invoice_services import (
    cancel_invoice,
    clone_invoice,
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
@require_POST
@login_required
@permission_required(
    ("finance.add_invoice", "finance.change_quote"),
    raise_exception=True,
)
def invoice_convert(request, quote_id):
    quote = get_object_or_404(Quote, pk=quote_id)
    line_quantities = None
    invoice_notes = request.POST.get("invoice_notes")

    # Si conversion partielle demandée via formulaire
    if request.POST.get("is_partial") == "1":
        line_quantities = {}
        for line in quote.lines.all():
            val_str = request.POST.get(f"qty_{line.pk}", "").strip().replace(",", ".")
            if val_str:
                try:
                    qty_val = Decimal(val_str)
                    if qty_val > Decimal("0.000"):
                        if qty_val % 1 != 0:
                            messages.error(request, "Les quantités facturées doivent être des nombres entiers.")
                            return redirect("finance:quote_detail", pk=quote_id)
                        line_quantities[line.pk] = qty_val
                except (InvalidOperation, TypeError):
                    pass

    try:
        invoice = convert_quote_to_invoice(
            quote_id,
            request.user,
            line_quantities=line_quantities,
            invoice_notes=invoice_notes,
        )
    except (Quote.DoesNotExist, ValidationError) as error:
        detail = (
            " ".join(error.messages)
            if isinstance(error, ValidationError)
            else "Le devis demandé n'existe pas."
        )
        messages.error(request, "Conversion impossible : " + detail)
        return redirect("quote-detail", quote_id=quote_id)

    quote.refresh_from_db()
    if quote.status == Quote.Status.PARTIALLY_INVOICED:
        messages.success(
            request,
            f"Facture partielle brouillon #{invoice.pk} créée avec succès pour le devis {quote.number}. "
            "Vous pouvez modifier ou regrouper des prestations avant son émission officielle."
        )
    else:
        messages.success(
            request,
            f"Facture brouillon #{invoice.pk} créée avec succès pour le devis {quote.number}. "
            "Toutes les prestations restantes ont été intégrées."
        )
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
    customer_snapshot = dict(invoice.customer_snapshot or {})
    if not customer_snapshot.get("name") and invoice.customer:
        customer_snapshot["name"] = invoice.customer.name
    if not customer_snapshot.get("email") and invoice.customer:
        customer_snapshot["email"] = invoice.customer.email
    if not customer_snapshot.get("phone") and invoice.customer:
        customer_snapshot["phone"] = invoice.customer.phone
    if not customer_snapshot.get("address") and invoice.customer:
        customer_snapshot["address"] = invoice.customer.address
    if not customer_snapshot.get("city") and invoice.customer:
        customer_snapshot["city"] = invoice.customer.city
    if not customer_snapshot.get("country") and invoice.customer:
        customer_snapshot["country"] = invoice.customer.country
    if not customer_snapshot.get("ice") and invoice.customer:
        customer_snapshot["ice"] = getattr(invoice.customer, "ice", "")
    if not customer_snapshot.get("tax_identifier") and invoice.customer:
        customer_snapshot["tax_identifier"] = getattr(invoice.customer, "tax_identifier", "")

    # Auto-synchronisation si l'ICE client est présent sur la fiche client mais absent du snapshot
    if invoice.customer and invoice.customer.ice and not (invoice.customer_snapshot or {}).get("ice"):
        invoice.customer_snapshot = customer_snapshot
        invoice.save(update_fields=["customer_snapshot"])
    history = DocumentHistory.objects.filter(invoice=invoice).select_related("actor")
    allocations = invoice.payment_allocations.select_related("payment").order_by(
        "-payment__payment_date", "-id"
    )
    credit_notes = invoice.credit_notes.all().order_by("-created_at")
    can_create_credit_note = (
        request.user.has_perm("finance.add_creditnote")
        and invoice.status in {Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID, Invoice.Status.OVERDUE}
        and any(line.remaining_creditable_quantity > 0 for line in lines)
    )
    can_add_payment = (
        request.user.has_perm("finance.add_payment")
        and invoice.status in {Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE}
        and invoice.remaining_balance > 0
    )
    can_cancel = (
        request.user.has_perm("finance.change_invoice")
        and invoice.status in {Invoice.Status.ISSUED, Invoice.Status.OVERDUE}
        and not invoice.payment_allocations.exists()
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
            "credit_notes": credit_notes,
            "can_create_credit_note": can_create_credit_note,
            "can_change": request.user.has_perm("finance.change_invoice"),
            "can_add": request.user.has_perm("finance.add_invoice"),
            "can_add_payment": can_add_payment,
            "can_cancel": can_cancel,
            "customers": Customer.objects.filter(is_active=True).order_by("name"),
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


@require_POST
@login_required
@permission_required("finance.change_invoice", raise_exception=True)
def invoice_cancel(request, invoice_id):
    motif = request.POST.get("motif", "").strip()
    try:
        invoice = cancel_invoice(invoice_id, request.user, motif=motif)
    except ValidationError as error:
        messages.error(request, "Annulation impossible : " + " ".join(error.messages))
    else:
        messages.success(
            request,
            f"La facture {invoice.number} a été annulée avec succès. Le stock d'articles associé a été réintégré."
        )
    return redirect("invoice-detail", invoice_id=invoice_id)


@require_POST
@login_required
@permission_required("finance.add_invoice", raise_exception=True)
def invoice_clone(request, invoice_id):
    target_customer_id = request.POST.get("customer_id")
    if not target_customer_id:
        messages.error(request, "Veuillez sélectionner un client pour la duplication.")
        return redirect("invoice-detail", invoice_id=invoice_id)
    try:
        new_invoice = clone_invoice(invoice_id, target_customer_id, request.user)
    except Exception as error:
        messages.error(request, f"Erreur lors de la duplication de la facture : {error}")
        return redirect("invoice-detail", invoice_id=invoice_id)
    messages.success(
        request,
        f"La facture a été dupliquée avec succès sous forme de brouillon pour le client « {new_invoice.customer.name} »."
    )
    return redirect("invoice-detail", invoice_id=new_invoice.pk)


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
            if payment.is_cash_over_ceiling:
                messages.warning(
                    request,
                    payment.get_cgi_alert_message()
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
    filename = get_document_download_filename(invoice, doc_type="facture")
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def invoice_docx(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer").prefetch_related("lines", "payment_allocations"),
        pk=invoice_id,
    )
    content = build_invoice_docx(invoice)
    filename = get_document_download_filename(invoice, doc_type="facture")
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.docx"'
    return response


@login_required
@permission_required("finance.change_invoice", raise_exception=True)
def update_invoice_customer_ice(request, invoice_id):
    if request.method != "POST":
        return redirect("invoice-detail", invoice_id=invoice_id)
    from django.shortcuts import get_object_or_404, redirect
    from django.db import transaction
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=invoice_id)
    raw_ice = request.POST.get("ice", "").strip()
    cleaned_ice = re.sub(r"\s+", "", raw_ice)
    if cleaned_ice and not (cleaned_ice.isdigit() and len(cleaned_ice) == 15):
        messages.error(request, "L'ICE marocain doit comporter exactement 15 chiffres.")
        return redirect("invoice-detail", invoice_id=invoice_id)

    with transaction.atomic():
        if invoice.customer:
            invoice.customer.ice = cleaned_ice
            invoice.customer.save(update_fields=["ice"])
        snapshot = dict(invoice.customer_snapshot or {})
        snapshot["ice"] = cleaned_ice
        invoice.customer_snapshot = snapshot
        invoice.save(update_fields=["customer_snapshot"])
        DocumentHistory.objects.create(
            invoice=invoice,
            event=DocumentHistory.Event.UPDATED,
            actor=request.user,
            snapshot={"ice": cleaned_ice, "action": "update_customer_ice"},
        )
    messages.success(request, f"L'ICE client ({cleaned_ice}) a été enregistré et appliqué avec succès.")
    return redirect("invoice-detail", invoice_id=invoice_id)

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.partners.models import Customer

from .models import DocumentHistory, Invoice, Quote
from .quote_calculations import calculate_quote_totals
from .quote_forms import QuoteForm, QuoteLineFormSet
from .document_filename import get_document_download_filename
from .quote_docx import build_quote_docx
from .quote_pdf import build_quote_pdf
from .quote_services import (
    change_quote_status,
    clone_quote,
    create_quote_revision,
    save_quote_draft,
    send_quote,
)


@login_required
@permission_required("finance.view_quote", raise_exception=True)
def quote_list(request):
    quotes = Quote.objects.select_related("customer", "revision_of").prefetch_related("lines")
    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    customer_id = request.GET.get("customer", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if search:
        quotes = quotes.filter(
            Q(number__icontains=search) | Q(customer__name__icontains=search)
        )
    if status in Quote.Status.values:
        quotes = quotes.filter(status=status)
    if customer_id.isdigit():
        quotes = quotes.filter(customer_id=int(customer_id))
    for value, field, label in [
        (date_from, "issue_date__gte", "Date de début"),
        (date_to, "issue_date__lte", "Date de fin"),
    ]:
        if value:
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                messages.error(request, f"{label} invalide.")
            else:
                quotes = quotes.filter(**{field: parsed})

    page = Paginator(quotes, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "finance/quotes/list.html",
        {
            "page": page,
            "statuses": Quote.Status.choices,
            "customers": Customer.objects.filter(is_active=True).order_by("name"),
            "filters": request.GET,
        },
    )


@login_required
@permission_required("finance.add_quote", raise_exception=True)
def quote_create(request):
    quote = Quote()
    form = QuoteForm(request.POST or None)
    formset = QuoteLineFormSet(request.POST or None, instance=quote, prefix="lines")

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        quote = form.save(commit=False)
        try:
            quote = save_quote_draft(quote, formset, request.user)
        except ValidationError as error:
            messages.error(request, "Le devis n'a pas été enregistré : " + " ".join(error.messages))
        else:
            messages.success(request, "Le devis brouillon a été enregistré.")
            return redirect("quote-detail", quote_id=quote.pk)
    return render(
        request,
        "finance/quotes/form.html",
        {"form": form, "formset": formset, "quote": None, "page_title": "Nouveau devis"},
    )


@login_required
@permission_required("finance.change_quote", raise_exception=True)
def quote_edit(request, quote_id):
    quote = get_object_or_404(Quote, pk=quote_id)
    if quote.status != Quote.Status.DRAFT:
        messages.error(request, "Seuls les devis brouillons peuvent être modifiés.")
        return redirect("quote-detail", quote_id=quote.pk)
    form = QuoteForm(request.POST or None, instance=quote)
    formset = QuoteLineFormSet(request.POST or None, instance=quote, prefix="lines")

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        quote = form.save(commit=False)
        try:
            quote = save_quote_draft(quote, formset, request.user)
        except ValidationError as error:
            messages.error(request, "Le devis n'a pas été enregistré : " + " ".join(error.messages))
        else:
            messages.success(request, "Le brouillon a été mis à jour.")
            return redirect("quote-detail", quote_id=quote.pk)
    return render(
        request,
        "finance/quotes/form.html",
        {
            "form": form,
            "formset": formset,
            "quote": quote,
            "page_title": f"Modifier {quote}",
        },
    )


@login_required
@permission_required("finance.view_quote", raise_exception=True)
def quote_detail(request, quote_id):
    quote = get_object_or_404(
        Quote.objects.select_related("customer", "project", "revision_of").prefetch_related(
            "lines__service", "lines__tax_rate", "lines__invoice_lines", "revisions", "invoices"
        ),
        pk=quote_id,
    )
    lines = list(quote.lines.all())
    totals = calculate_quote_totals(lines)
    has_discount = any(
        line.discount_value and line.discount_value > Decimal("0.00")
        for line in lines
    )
    customer_snapshot = dict(quote.customer_snapshot or {})
    if not customer_snapshot.get("name") and quote.customer:
        customer_snapshot["name"] = quote.customer.name
    if not customer_snapshot.get("email") and quote.customer:
        customer_snapshot["email"] = quote.customer.email
    if not customer_snapshot.get("phone") and quote.customer:
        customer_snapshot["phone"] = quote.customer.phone
    if not customer_snapshot.get("address") and quote.customer:
        customer_snapshot["address"] = quote.customer.address
    if not customer_snapshot.get("city") and quote.customer:
        customer_snapshot["city"] = quote.customer.city
    if not customer_snapshot.get("country") and quote.customer:
        customer_snapshot["country"] = quote.customer.country
    if not customer_snapshot.get("ice") and quote.customer:
        customer_snapshot["ice"] = getattr(quote.customer, "ice", "")
    if not customer_snapshot.get("tax_identifier") and quote.customer:
        customer_snapshot["tax_identifier"] = getattr(quote.customer, "tax_identifier", "")

    if quote.customer and quote.customer.ice and not (quote.customer_snapshot or {}).get("ice"):
        quote.customer_snapshot = customer_snapshot
        quote.save(update_fields=["customer_snapshot"])
    for line in lines:
        line.amounts = calculate_quote_totals([line])
    history = DocumentHistory.objects.filter(quote=quote).select_related("actor")
    linked_invoices = list(quote.invoices.order_by("-created_at"))
    return render(
        request,
        "finance/quotes/detail.html",
        {
            "quote": quote,
            "customer_snapshot": customer_snapshot,
            "lines": lines,
            "totals": totals,
            "has_discount": has_discount,
            "history": history,
            "linked_invoices": linked_invoices,
            "customers": Customer.objects.filter(is_active=True).order_by("name"),
            "today": timezone.localdate(),
            "can_edit": request.user.has_perm("finance.change_quote"),
            "can_add": request.user.has_perm("finance.add_quote"),
            "can_delete": request.user.has_perm("finance.delete_quote"),
            "can_convert": (
                quote.status in (Quote.Status.ACCEPTED, Quote.Status.PARTIALLY_INVOICED)
                and request.user.has_perm("finance.add_invoice")
                and request.user.has_perm("finance.change_quote")
                and not quote.is_fully_invoiced
            ),
        },
    )


@require_POST
@login_required
@permission_required("finance.change_quote", raise_exception=True)
def quote_send(request, quote_id):
    try:
        quote = send_quote(quote_id, request.user)
    except ValidationError as error:
        messages.error(request, "Envoi impossible : " + " ".join(error.messages))
        return redirect("quote-detail", quote_id=quote_id)
    messages.success(request, f"Le devis {quote.number} a été envoyé.")
    return redirect("quote-detail", quote_id=quote.pk)


@require_POST
@login_required
@permission_required("finance.change_quote", raise_exception=True)
def quote_change_status(request, quote_id):
    new_status = request.POST.get("status", "")
    try:
        quote = change_quote_status(quote_id, new_status, request.user)
    except (ValidationError, ValueError) as error:
        detail = " ".join(error.messages) if isinstance(error, ValidationError) else str(error)
        messages.error(request, "Changement de statut impossible : " + detail)
        return redirect("quote-detail", quote_id=quote_id)
    messages.success(request, f"Le statut du devis est maintenant « {quote.get_status_display()} ».")
    return redirect("quote-detail", quote_id=quote.pk)


@login_required
@permission_required("finance.add_quote", raise_exception=True)
def quote_revision(request, quote_id):
    try:
        quote = create_quote_revision(quote_id, request.user)
    except (Quote.DoesNotExist, ValidationError) as error:
        if isinstance(error, ValidationError):
            detail = " ".join(error.messages)
        else:
            detail = "Le devis demandé n'existe pas."
        messages.error(request, "Création de la nouvelle version impossible : " + detail)
        return redirect("quote-list")
    messages.success(request, "Une nouvelle version brouillon liée au devis a été créée.")
    return redirect("quote-edit", quote_id=quote.pk)


@require_POST
@login_required
@permission_required("finance.add_quote", raise_exception=True)
def quote_clone(request, quote_id):
    target_customer_id = request.POST.get("customer_id")
    if not target_customer_id:
        messages.error(request, "Veuillez sélectionner un client pour la duplication.")
        return redirect("quote-detail", quote_id=quote_id)
    try:
        new_quote = clone_quote(quote_id, target_customer_id, request.user)
    except Exception as error:
        messages.error(request, f"Erreur lors de la duplication du devis : {error}")
        return redirect("quote-detail", quote_id=quote_id)
    messages.success(
        request,
        f"Le devis a été dupliqué avec succès sous forme de brouillon pour le client « {new_quote.customer.name} »."
    )
    return redirect("quote-detail", quote_id=new_quote.pk)


@login_required
@permission_required("finance.view_quote", raise_exception=True)
def quote_pdf(request, quote_id):
    quote = get_object_or_404(
        Quote.objects.select_related("customer", "project", "revision_of").prefetch_related(
            "lines"
        ),
        pk=quote_id,
    )
    content = build_quote_pdf(quote)
    response = HttpResponse(content, content_type="application/pdf")
    filename = get_document_download_filename(quote, doc_type="devis")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response


@login_required
@permission_required("finance.view_quote", raise_exception=True)
def quote_docx(request, quote_id):
    quote = get_object_or_404(
        Quote.objects.select_related("customer", "project", "revision_of").prefetch_related(
            "lines"
        ),
        pk=quote_id,
    )
    content = build_quote_docx(quote)
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    filename = get_document_download_filename(quote, doc_type="devis")
    response["Content-Disposition"] = f'attachment; filename="{filename}.docx"'
    return response


@require_POST
@login_required
@permission_required("finance.delete_quote", raise_exception=True)
def quote_delete(request, quote_id):
    quote = get_object_or_404(Quote, pk=quote_id)
    if quote.status != Quote.Status.DRAFT:
        messages.error(
            request,
            "Un devis envoyé ou émis ne peut pas être supprimé. "
            "Vous pouvez le marquer comme Refusé ou Annulé pour préserver la traçabilité."
        )
        return redirect("quote-detail", quote_id=quote.pk)
    pk_deleted = quote.pk
    quote.delete()
    messages.success(request, f"Le devis brouillon #{pk_deleted} a été supprimé avec succès.")
    return redirect("quote-list")

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .credit_note_docx import build_credit_note_docx
from .credit_note_pdf import build_credit_note_pdf
from .credit_note_services import create_credit_note_draft, issue_credit_note
from .document_filename import clean_designation_for_filename
from .models import CompanySettings, CreditNote, DocumentHistory, Invoice
from .quote_calculations import calculate_quote_totals


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def credit_note_list(request):
    """Liste de toutes les factures d'avoir (notes de crédit)."""
    credit_notes = CreditNote.objects.select_related("customer", "invoice").prefetch_related("lines").order_by("-created_at")

    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    if search:
        credit_notes = credit_notes.filter(
            models.Q(number__icontains=search)
            | models.Q(customer__name__icontains=search)
            | models.Q(invoice__number__icontains=search)
        )
    if status in CreditNote.Status.values:
        credit_notes = credit_notes.filter(status=status)

    page = Paginator(credit_notes, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "finance/credit_notes/list.html",
        {
            "page": page,
            "statuses": CreditNote.Status.choices,
            "filters": request.GET,
        },
    )


@login_required
@permission_required("finance.add_invoice", raise_exception=True)
def credit_note_create(request, invoice_id):
    """
    Formulaire de création d'un avoir partiel ou total pour une facture émise.
    Permet de sélectionner les prestations / articles retournés et la quantité à déduire.
    """
    invoice = get_object_or_404(
        Invoice.objects.select_related("customer").prefetch_related("lines__article", "lines__service"),
        pk=invoice_id,
    )

    if invoice.status not in {
        Invoice.Status.ISSUED,
        Invoice.Status.PARTIALLY_PAID,
        Invoice.Status.PAID,
        Invoice.Status.OVERDUE,
    }:
        messages.error(request, "Un avoir ne peut être créé que sur une facture émise ou réglée.")
        return redirect("invoice-detail", invoice_id=invoice.pk)

    invoice_lines = list(invoice.lines.all())
    # Filtrer les lignes qui peuvent encore être créditées
    creditable_lines = [l for l in invoice_lines if l.remaining_creditable_quantity > Decimal("0.000")]

    if not creditable_lines:
        messages.error(request, "Toutes les lignes de cette facture ont déjà été intégralement créditées.")
        return redirect("invoice-detail", invoice_id=invoice.pk)

    if request.method == "POST":
        reason = request.POST.get("reason", CreditNote.Reason.RETURN)
        reason_details = request.POST.get("reason_details", "").strip()
        notes = request.POST.get("notes", "").strip()

        line_data = {}
        for line in creditable_lines:
            raw_qty = request.POST.get(f"qty_{line.pk}", "").strip().replace(",", ".")
            if raw_qty:
                try:
                    qty = Decimal(raw_qty)
                    if qty > Decimal("0.000"):
                        line_data[line.pk] = qty
                except Exception:
                    pass

        try:
            credit_note = create_credit_note_draft(
                invoice_id=invoice.pk,
                line_data=line_data,
                reason=reason,
                reason_details=reason_details,
                actor=request.user,
                notes=notes,
            )
        except ValidationError as error:
            messages.error(request, "Impossible de créer l'avoir : " + " ".join(error.messages))
        else:
            messages.success(
                request,
                f"L'avoir brouillon #{credit_note.pk} a été préparé avec succès pour la facture {invoice.number}."
            )
            return redirect("credit-note-detail", credit_note_id=credit_note.pk)

    return render(
        request,
        "finance/credit_notes/form.html",
        {
            "invoice": invoice,
            "creditable_lines": creditable_lines,
            "reasons": CreditNote.Reason.choices,
        },
    )


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def credit_note_detail(request, credit_note_id):
    """Fiche de consultation détaillée d'un avoir."""
    credit_note = get_object_or_404(
        CreditNote.objects.select_related("customer", "invoice").prefetch_related("lines__article", "lines__service"),
        pk=credit_note_id,
    )
    lines = list(credit_note.lines.all())
    for line in lines:
        line.amounts = calculate_quote_totals([line])
    totals = calculate_quote_totals(lines)

    company = CompanySettings.get_settings()
    history = DocumentHistory.objects.filter(credit_note=credit_note).select_related("actor")

    return render(
        request,
        "finance/credit_notes/detail.html",
        {
            "credit_note": credit_note,
            "lines": lines,
            "totals": totals,
            "company": company,
            "history": history,
            "can_issue": request.user.has_perm("finance.add_invoice") and credit_note.status == CreditNote.Status.DRAFT,
        },
    )


@require_POST
@login_required
@permission_required("finance.add_invoice", raise_exception=True)
def credit_note_issue(request, credit_note_id):
    """Émission officielle de l'avoir : attribution du numéro AVO-... et réintégration de stock."""
    try:
        credit_note = issue_credit_note(credit_note_id, request.user)
    except ValidationError as error:
        messages.error(request, "Émission impossible : " + " ".join(error.messages))
        return redirect("credit-note-detail", credit_note_id=credit_note_id)

    messages.success(
        request,
        f"L'avoir {credit_note.number} a été émis avec succès. "
        "Le stock des articles retournés a été réintégré et le compte client mis à jour."
    )
    return redirect("credit-note-detail", credit_note_id=credit_note.pk)


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def credit_note_pdf(request, credit_note_id):
    """Téléchargement de la facture d'avoir au format PDF."""
    credit_note = get_object_or_404(
        CreditNote.objects.select_related("customer", "invoice").prefetch_related("lines"),
        pk=credit_note_id,
    )
    content = build_credit_note_pdf(credit_note)

    num_str = credit_note.number.replace("AVO-", "Avoir-") if (credit_note.number and credit_note.number.startswith("AVO-")) else f"avoir-brouillon-{credit_note.pk}"
    desc = ""
    first_line = credit_note.lines.first()
    if first_line and first_line.description:
        desc = " " + clean_designation_for_filename(first_line.description)

    filename = f"{num_str}{desc}.pdf"

    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@permission_required("finance.view_invoice", raise_exception=True)
def credit_note_docx(request, credit_note_id):
    """Téléchargement de la facture d'avoir au format Word (.docx)."""
    credit_note = get_object_or_404(
        CreditNote.objects.select_related("customer", "invoice").prefetch_related("lines"),
        pk=credit_note_id,
    )
    content = build_credit_note_docx(credit_note)

    num_str = credit_note.number.replace("AVO-", "Avoir-") if (credit_note.number and credit_note.number.startswith("AVO-")) else f"avoir-brouillon-{credit_note.pk}"
    desc = ""
    first_line = credit_note.lines.first()
    if first_line and first_line.description:
        desc = " " + clean_designation_for_filename(first_line.description)

    filename = f"{num_str}{desc}.docx"

    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

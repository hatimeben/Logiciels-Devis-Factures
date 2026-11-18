from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .document_numbering import format_document_number
from .models import (
    CreditNote,
    CreditNoteLine,
    CreditNoteNumberCounter,
    CreditNoteNumberingSettings,
    DocumentHistory,
    Invoice,
    InvoiceLine,
)


def create_credit_note_draft(invoice_id, line_data, reason, reason_details="", actor=None, notes=""):
    """
    Crée un avoir brouillon rattaché à une facture émise.
    `line_data` est un dict: {invoice_line_id: quantity_to_credit}.
    Valide que :
    - La facture est émise (ou partiellement payée, payée, en retard).
    - Au moins une ligne est créditée avec une quantité > 0.
    - La quantité créditée pour chaque ligne ne dépasse pas sa quantité restante créditable.
    """
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
        if invoice.status not in {
            Invoice.Status.ISSUED,
            Invoice.Status.PARTIALLY_PAID,
            Invoice.Status.PAID,
            Invoice.Status.OVERDUE,
        }:
            raise ValidationError("Un avoir ne peut être créé que pour une facture émise ou réglée.")

        # Filtrer les lignes avec quantité demandée > 0
        credited_lines = []
        for line_id, qty in line_data.items():
            if qty and qty > Decimal("0.000"):
                inv_line = InvoiceLine.objects.get(pk=line_id, invoice=invoice)
                remaining = inv_line.remaining_creditable_quantity
                if qty > remaining:
                    raise ValidationError(
                        f"La quantité à créditer pour '{inv_line.description}' ({qty}) dépasse "
                        f"la quantité restant créditable ({remaining})."
                    )
                credited_lines.append((inv_line, qty))

        if not credited_lines:
            raise ValidationError("Veuillez sélectionner au moins une prestation ou un article avec une quantité positive à créditer.")

        credit_note = CreditNote.objects.create(
            invoice=invoice,
            customer=invoice.customer,
            customer_snapshot=invoice.customer_snapshot,
            status=CreditNote.Status.DRAFT,
            reason=reason,
            reason_details=reason_details,
            notes=notes,
        )

        for inv_line, qty in credited_lines:
            CreditNoteLine.objects.create(
                credit_note=credit_note,
                invoice_line=inv_line,
                service=inv_line.service,
                article=inv_line.article,
                description=inv_line.description,
                quantity=qty,
                unit_price=inv_line.unit_price,
                discount_type=inv_line.discount_type,
                discount_value=inv_line.discount_value,
                tax_rate=inv_line.tax_rate,
                tax_percentage=inv_line.tax_percentage,
                tax_name=inv_line.tax_name,
                position=inv_line.position,
            )

        DocumentHistory.objects.create(
            credit_note=credit_note,
            event=DocumentHistory.Event.CREATED,
            actor=actor,
            snapshot={
                "invoice_number": invoice.number,
                "reason": credit_note.get_reason_display(),
                "actor_username": actor.get_username() if actor else "",
            },
        )
        return credit_note


def issue_credit_note(credit_note_id, actor=None):
    """
    Émet officiellement une facture d'avoir :
    - Attribue un numéro séquentiel unique officiel (ex: AVO-2026-0001).
    - Passe le statut à ISSUED.
    - Réintègre automatiquement les articles en stock.
    - Enregistre l'événement dans DocumentHistory.
    """
    with transaction.atomic():
        credit_note = CreditNote.objects.select_for_update().get(pk=credit_note_id)
        if credit_note.status != CreditNote.Status.DRAFT:
            raise ValidationError("Seul un avoir brouillon peut être émis.")

        year = timezone.localdate().year
        counter, _ = CreditNoteNumberCounter.objects.select_for_update().get_or_create(
            year=year,
            defaults={"last_number": 0},
        )
        counter.last_number += 1
        counter.save()

        settings = CreditNoteNumberingSettings.get_settings()
        number = format_document_number(settings, year, counter.last_number)

        credit_note.number = number
        credit_note.status = CreditNote.Status.ISSUED
        credit_note.issue_date = timezone.localdate()
        credit_note.save()

        # Réintégration du stock pour les articles retournés
        from apps.catalog.stock_services import reintegrer_stock_depuis_avoir
        reintegrer_stock_depuis_avoir(credit_note, actor=actor)

        DocumentHistory.objects.create(
            credit_note=credit_note,
            event=DocumentHistory.Event.ISSUED,
            actor=actor,
            snapshot={
                "number": number,
                "invoice_number": credit_note.invoice.number,
                "actor_username": actor.get_username() if actor else "",
            },
        )
        return credit_note


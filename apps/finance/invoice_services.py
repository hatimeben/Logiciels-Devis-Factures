from decimal import Decimal
import time

from django.core.exceptions import ValidationError
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from .models import (
    DocumentHistory,
    Invoice,
    InvoiceNumberingSettings,
    Quote,
)
from .quote_calculations import calculate_quote_line, calculate_quote_totals


def invoice_snapshot(invoice):
    lines = list(invoice.lines.select_related("tax_rate").all())
    line_snapshots = []
    for line in lines:
        amounts = calculate_quote_line(
            line.quantity,
            line.unit_price,
            line.discount_type,
            line.discount_value,
            line.tax_percentage,
        )
        line_snapshots.append(
            {
                "service_id": line.service_id,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit_price": str(line.unit_price),
                "discount_type": line.discount_type,
                "discount_value": str(line.discount_value),
                "tax_name": line.tax_name,
                "tax_percentage": str(line.tax_percentage),
                **{key: str(value) for key, value in amounts.items()},
            }
        )
    return {
        "number": invoice.number or "",
        "status": invoice.status,
        "customer_id": invoice.customer_id,
        "customer": invoice.customer_snapshot,
        "quote_id": invoice.quote_id,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else "",
        "due_date": invoice.due_date.isoformat() if invoice.due_date else "",
        "notes": invoice.notes,
        "lines": line_snapshots,
        "totals": {
            key: str(value)
            for key, value in calculate_quote_totals(lines).items()
        },
    }


def _customer_snapshot(customer):
    return {
        "name": customer.name,
        "email": customer.email,
        "phone": customer.phone,
        "address": customer.address,
        "city": customer.city,
        "country": customer.country,
        "ice": getattr(customer, "ice", ""),
        "tax_identifier": customer.tax_identifier,
    }


def _convert_quote_once(quote_id, actor, line_quantities=None, invoice_notes=None):
    with transaction.atomic():
        quote = (
            Quote.objects.select_for_update()
            .select_related("customer", "project")
            .prefetch_related("lines__invoice_lines__invoice")
            .get(pk=quote_id)
        )
        if quote.status not in (Quote.Status.ACCEPTED, Quote.Status.PARTIALLY_INVOICED):
            if quote.status == Quote.Status.CONVERTED:
                raise ValidationError("Toutes les prestations de ce devis ont déjà été intégralement facturées.")
            raise ValidationError("Seul un devis accepté ou partiellement facturé peut être converti en facture.")

        source_lines = list(quote.lines.all())
        if not source_lines:
            raise ValidationError("Le devis ne contient aucune ligne.")

        # Calcul et validation des quantités à facturer
        lines_to_bill = []
        if line_quantities is not None:
            # Facturation partielle ou sélective explicite
            for line in source_lines:
                qty_req = line_quantities.get(line.pk)
                if qty_req is not None:
                    qty_req = Decimal(str(qty_req))
                    if qty_req > Decimal("0.000"):
                        remaining = line.remaining_quantity
                        if qty_req > remaining:
                            raise ValidationError(
                                f"La quantité demandée ({qty_req}) pour « {line.description} » "
                                f"dépasse la quantité restante devisée ({remaining})."
                            )
                        lines_to_bill.append((line, qty_req))
        else:
            # Conversion complète des quantités restantes par défaut
            for line in source_lines:
                remaining = line.remaining_quantity
                if remaining > Decimal("0.000"):
                    lines_to_bill.append((line, remaining))

        if not lines_to_bill:
            raise ValidationError("Toutes les prestations de ce devis ont déjà été intégralement facturées.")

        initial_status = quote.status

        invoice = Invoice.objects.create(
            quote=quote,
            customer=quote.customer,
            customer_snapshot=quote.customer_snapshot or _customer_snapshot(quote.customer),
            project=quote.project,
            notes=invoice_notes if invoice_notes is not None else quote.notes,
        )
        for line, qty in lines_to_bill:
            invoice.lines.create(
                quote_line=line,
                service=line.service,
                description=line.description,
                quantity=qty,
                unit_price=line.unit_price,
                discount_type=line.discount_type,
                discount_value=line.discount_value,
                tax_rate=line.tax_rate,
                tax_percentage=line.tax_percentage,
                tax_name=line.tax_name,
                position=line.position,
            )

        # Synchronisation automatique du statut du devis (Partiellement ou Totalement facturé)
        quote.sync_invoicing_status()
        quote.refresh_from_db()

        DocumentHistory.objects.create(
            quote=quote,
            event=DocumentHistory.Event.STATUS_CHANGED,
            actor=actor,
            snapshot={
                "from_status": initial_status,
                "to_status": quote.status,
                "invoice_id": invoice.pk,
                "lines_billed": [
                    {"quote_line_id": l.pk, "billed_qty": str(q)} for l, q in lines_to_bill
                ],
                "actor_username": actor.get_username(),
                "document": {
                    "number": quote.number,
                    "customer": quote.customer_snapshot,
                },
            },
        )
        DocumentHistory.objects.create(
            invoice=invoice,
            event=DocumentHistory.Event.CREATED,
            actor=actor,
            snapshot={
                "source_quote_id": quote.pk,
                "is_partial": quote.status == Quote.Status.PARTIALLY_INVOICED,
                "after": invoice_snapshot(invoice),
                "actor_username": actor.get_username(),
            },
        )
        return invoice


def convert_quote_to_invoice(quote_id, actor, line_quantities=None, invoice_notes=None):
    for attempt in range(5):
        try:
            return _convert_quote_once(
                quote_id,
                actor,
                line_quantities=line_quantities,
                invoice_notes=invoice_notes,
            )
        except OperationalError as error:
            if "locked" not in str(error).lower() or attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))


def _issue_invoice_once(invoice_id, actor):
    numbering = InvoiceNumberingSettings.get_settings()
    year = timezone.localdate().year
    counter_year = year if numbering.include_year else 0
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO finance_invoicenumbercounter (year, last_number) "
                "VALUES (%s, 1) "
                "ON CONFLICT(year) DO UPDATE SET last_number = last_number + 1 "
                "RETURNING last_number",
                [counter_year],
            )
            sequence = cursor.fetchone()[0]
        invoice = Invoice.objects.get(pk=invoice_id)
        if invoice.status != Invoice.Status.DRAFT:
            raise ValidationError("Seule une facture brouillon peut être émise.")
        if not invoice.lines.exists():
            raise ValidationError("Ajoutez au moins une prestation avant d'émettre la facture.")
        parts = [numbering.prefix]
        if numbering.include_year:
            parts.append(str(year))
        parts.append(str(sequence).zfill(numbering.sequence_width))
        number = numbering.separator.join(parts)
        changed = Invoice.objects.filter(
            pk=invoice.pk,
            status=Invoice.Status.DRAFT,
            number__isnull=True,
        ).update(
            number=number,
            status=Invoice.Status.ISSUED,
            issue_date=timezone.localdate(),
            customer_snapshot=invoice.customer_snapshot or _customer_snapshot(invoice.customer),
            updated_at=timezone.now(),
        )
        if changed != 1:
            raise ValidationError("La facture a déjà été émise ou modifiée.")

        invoice.refresh_from_db()
        DocumentHistory.objects.create(
            invoice=invoice,
            event=DocumentHistory.Event.ISSUED,
            actor=actor,
            snapshot={
                "from_status": Invoice.Status.DRAFT,
                "to_status": Invoice.Status.ISSUED,
                "actor_username": actor.get_username(),
                "document": invoice_snapshot(invoice),
            },
        )
        return invoice


def issue_invoice(invoice_id, actor):
    for attempt in range(5):
        try:
            return _issue_invoice_once(invoice_id, actor)
        except OperationalError as error:
            if "locked" not in str(error).lower() or attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))


def save_invoice_draft(invoice, line_formset, actor):
    with transaction.atomic():
        is_new = invoice._state.adding
        before = None
        if not is_new:
            persisted = Invoice.objects.get(pk=invoice.pk)
            if persisted.status != Invoice.Status.DRAFT:
                raise ValidationError("Seules les factures brouillon peuvent être modifiées.")
            before = invoice_snapshot(persisted)
        if not invoice.customer_snapshot and invoice.customer_id:
            invoice.customer_snapshot = _customer_snapshot(invoice.customer)
        invoice.save()
        line_formset.instance = invoice
        line_formset.save()
        invoice.refresh_from_db()
        snapshot = invoice_snapshot(invoice)
        DocumentHistory.objects.create(
            invoice=invoice,
            event=(
                DocumentHistory.Event.CREATED if is_new else DocumentHistory.Event.UPDATED
            ),
            actor=actor,
            snapshot={
                "before": before,
                "after": snapshot,
                "actor_username": actor.get_username(),
            },
        )
        return invoice


def update_invoice_draft(invoice, form, actor):
    with transaction.atomic():
        persisted = Invoice.objects.get(pk=invoice.pk)
        if persisted.status != Invoice.Status.DRAFT:
            raise ValidationError("Seules les factures brouillon peuvent être modifiées.")
        before = invoice_snapshot(persisted)
        invoice = form.save()
        invoice.refresh_from_db()
        DocumentHistory.objects.create(
            invoice=invoice,
            event=DocumentHistory.Event.UPDATED,
            actor=actor,
            snapshot={
                "before": before,
                "after": invoice_snapshot(invoice),
                "actor_username": actor.get_username(),
            },
        )
        return invoice

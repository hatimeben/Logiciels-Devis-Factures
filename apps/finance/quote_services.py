import time

from django.core.exceptions import ValidationError
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from .models import DocumentHistory, Quote, QuoteNumberingSettings
from .quote_calculations import calculate_quote_line, calculate_quote_totals


ALLOWED_TRANSITIONS = {
    Quote.Status.SENT: {
        Quote.Status.ACCEPTED,
        Quote.Status.REJECTED,
        Quote.Status.EXPIRED,
        Quote.Status.CANCELLED,
    },
}


def quote_snapshot(quote):
    lines = list(quote.lines.select_related("service", "tax_rate").all())
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
        "number": quote.number or "",
        "status": quote.status,
        "customer_id": quote.customer_id,
        "customer_name": (
            quote.customer_snapshot.get("name")
            if quote.customer_snapshot
            else quote.customer.name
        ),
        "customer": quote.customer_snapshot,
        "project_id": quote.project_id,
        "issue_date": quote.issue_date.isoformat() if quote.issue_date else "",
        "valid_until": quote.valid_until.isoformat() if quote.valid_until else "",
        "notes": quote.notes,
        "revision_of_id": quote.revision_of_id,
        "lines": line_snapshots,
        "totals": {
            key: str(value)
            for key, value in calculate_quote_totals(lines).items()
        },
    }


def save_quote_draft(quote, line_formset, actor):
    with transaction.atomic():
        is_new = quote._state.adding
        before = None
        if not is_new:
            persisted = Quote.objects.get(pk=quote.pk)
            before = quote_snapshot(persisted)
        quote.save()
        line_formset.instance = quote
        line_formset.save()
        quote.refresh_from_db()
        snapshot = quote_snapshot(quote)
        DocumentHistory.objects.create(
            quote=quote,
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
        return quote


def _format_quote_number(settings, year, sequence):
    from .document_numbering import format_document_number

    return format_document_number(settings, year, sequence)


def _send_quote_once(quote_id, actor):
    numbering = QuoteNumberingSettings.get_settings()
    year = timezone.localdate().year
    counter_year = year if numbering.include_year else 0
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO finance_quotenumbercounter (year, last_number) "
                "VALUES (%s, 1) "
                "ON CONFLICT(year) DO UPDATE SET last_number = last_number + 1 "
                "RETURNING last_number",
                [counter_year],
            )
            sequence = cursor.fetchone()[0]
        quote = Quote.objects.get(pk=quote_id)
        if quote.status != Quote.Status.DRAFT:
            raise ValidationError("Seul un devis brouillon peut être envoyé.")
        if not quote.lines.exists():
            raise ValidationError("Ajoutez au moins une prestation avant d'envoyer le devis.")
        if not quote.customer.is_active:
            raise ValidationError("Le client du devis est inactif ; choisissez un client actif.")

        number = _format_quote_number(numbering, year, sequence)
        customer_snapshot = quote.customer_snapshot or {
            "name": quote.customer.name,
            "email": quote.customer.email,
            "phone": quote.customer.phone,
            "address": quote.customer.address,
            "city": quote.customer.city,
            "country": quote.customer.country,
            "tax_identifier": quote.customer.tax_identifier,
        }
        changed = Quote.objects.filter(
            pk=quote.pk,
            status=Quote.Status.DRAFT,
            number__isnull=True,
        ).update(
            number=number,
            status=Quote.Status.SENT,
            issue_date=timezone.localdate(),
            customer_snapshot=customer_snapshot,
            updated_at=timezone.now(),
        )
        if changed != 1:
            raise ValidationError("Le devis a déjà été envoyé ou modifié.")

        quote.refresh_from_db()
        DocumentHistory.objects.create(
            quote=quote,
            event=DocumentHistory.Event.STATUS_CHANGED,
            actor=actor,
            snapshot={
                "from_status": Quote.Status.DRAFT,
                "to_status": Quote.Status.SENT,
                "actor_username": actor.get_username(),
                "document": quote_snapshot(quote),
            },
        )
        return quote


def send_quote(quote_id, actor):
    for attempt in range(5):
        try:
            return _send_quote_once(quote_id, actor)
        except OperationalError as error:
            if "locked" not in str(error).lower() or attempt == 4:
                raise
            time.sleep(0.05 * (2**attempt))


def change_quote_status(quote_id, new_status, actor):
    with transaction.atomic():
        quote = Quote.objects.get(pk=quote_id)
        if new_status not in Quote.Status.values:
            raise ValidationError("Le statut demandé n'est pas reconnu.")
        if new_status not in ALLOWED_TRANSITIONS.get(quote.status, set()):
            raise ValidationError(
                f"Transition de statut non autorisée : {quote.get_status_display()} "
                f"vers {Quote.Status(new_status).label}."
            )
        old_status = quote.status
        changed = Quote.objects.filter(pk=quote.pk, status=old_status).update(
            status=new_status,
            updated_at=timezone.now(),
        )
        if changed != 1:
            raise ValidationError("Le statut du devis a été modifié par une autre opération.")
        quote.refresh_from_db()
        DocumentHistory.objects.create(
            quote=quote,
            event=DocumentHistory.Event.STATUS_CHANGED,
            actor=actor,
            snapshot={
                "from_status": old_status,
                "to_status": new_status,
                "actor_username": actor.get_username(),
                "document": quote_snapshot(quote),
            },
        )
        return quote


def create_quote_revision(source_quote_id, actor):
    with transaction.atomic():
        source = Quote.objects.get(pk=source_quote_id)
        if source.status not in {Quote.Status.SENT, Quote.Status.ACCEPTED}:
            raise ValidationError("Seuls les devis envoyés ou acceptés peuvent être révisés.")
        revision = Quote.objects.create(
            revision_of=source,
            customer=source.customer,
            project=source.project,
            status=Quote.Status.DRAFT,
            customer_snapshot=source.customer_snapshot,
            valid_until=source.valid_until,
            notes=source.notes,
        )
        for line in source.lines.all():
            revision.lines.create(
                service=line.service,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount_type=line.discount_type,
                discount_value=line.discount_value,
                tax_rate=line.tax_rate,
                tax_percentage=line.tax_percentage,
                tax_name=line.tax_name,
                position=line.position,
            )
        DocumentHistory.objects.create(
            quote=source,
            event=DocumentHistory.Event.REVISION_CREATED,
            actor=actor,
            snapshot={
                "revision_id": revision.pk,
                "actor_username": actor.get_username(),
            },
        )
        DocumentHistory.objects.create(
            quote=revision,
            event=DocumentHistory.Event.CREATED,
            actor=actor,
            snapshot={
                "source_quote_id": source.pk,
                "after": quote_snapshot(revision),
                "actor_username": actor.get_username(),
            },
        )
        return revision

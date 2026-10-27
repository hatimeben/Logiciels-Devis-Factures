from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import DocumentHistory, Invoice, Payment, PaymentAllocation
from .money import round_money


def record_invoice_payment(
    invoice_id,
    amount,
    payment_date,
    method,
    reference="",
    notes="",
    actor=None,
):
    amount = Decimal(amount)
    if amount <= Decimal("0.00"):
        raise ValidationError("Le montant du paiement doit être supérieur à zéro.")

    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
        if invoice.status not in {
            Invoice.Status.ISSUED,
            Invoice.Status.PARTIALLY_PAID,
            Invoice.Status.OVERDUE,
        }:
            raise ValidationError("Seule une facture émise peut recevoir un paiement.")

        remaining = invoice.remaining_balance
        if amount > remaining:
            raise ValidationError(
                f"Le montant ({amount} MAD) dépasse le reste à payer de la facture ({remaining} MAD)."
            )

        payment = Payment.objects.create(
            customer=invoice.customer,
            payment_date=payment_date,
            amount=amount,
            method=method,
            reference=reference,
            notes=notes,
        )

        allocation = PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=amount,
        )

        new_remaining = round_money(remaining - amount)
        if new_remaining == Decimal("0.00"):
            new_status = Invoice.Status.PAID
        else:
            new_status = Invoice.Status.PARTIALLY_PAID

        Invoice.objects.filter(pk=invoice.pk).update(
            status=new_status,
            updated_at=timezone.now(),
        )
        invoice.refresh_from_db()

        actor_username = actor.get_username() if actor else ""
        DocumentHistory.objects.create(
            invoice=invoice,
            event=DocumentHistory.Event.PAYMENT_RECORDED,
            actor=actor,
            snapshot={
                "payment_id": payment.pk,
                "allocation_id": allocation.pk,
                "amount": str(amount),
                "payment_date": payment_date.isoformat(),
                "method": method,
                "reference": reference,
                "notes": notes,
                "remaining_balance": str(new_remaining),
                "new_status": new_status,
                "actor_username": actor_username,
            },
        )

        return payment, allocation


def allocate_payment_to_invoice(payment_id, invoice_id, amount, actor=None):
    amount = Decimal(amount)
    if amount <= Decimal("0.00"):
        raise ValidationError("Le montant à imputer doit être supérieur à zéro.")

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)

        if payment.customer_id != invoice.customer_id:
            raise ValidationError("Le paiement et la facture doivent appartenir au même client.")

        if invoice.status not in {
            Invoice.Status.ISSUED,
            Invoice.Status.PARTIALLY_PAID,
            Invoice.Status.OVERDUE,
        }:
            raise ValidationError("Seule une facture émise peut recevoir une imputation.")

        if amount > payment.unallocated_amount:
            raise ValidationError("Le montant dépasse le solde disponible du paiement.")

        if amount > invoice.remaining_balance:
            raise ValidationError("Le montant dépasse le solde restant de la facture.")

        allocation = PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=amount,
        )

        new_remaining = invoice.remaining_balance
        if new_remaining == Decimal("0.00"):
            new_status = Invoice.Status.PAID
        else:
            new_status = Invoice.Status.PARTIALLY_PAID

        Invoice.objects.filter(pk=invoice.pk).update(
            status=new_status,
            updated_at=timezone.now(),
        )
        invoice.refresh_from_db()

        actor_username = actor.get_username() if actor else ""
        DocumentHistory.objects.create(
            invoice=invoice,
            event=DocumentHistory.Event.PAYMENT_RECORDED,
            actor=actor,
            snapshot={
                "payment_id": payment.pk,
                "allocation_id": allocation.pk,
                "amount": str(amount),
                "remaining_balance": str(new_remaining),
                "new_status": new_status,
                "actor_username": actor_username,
            },
        )

        return allocation


def update_overdue_invoices():
    today = timezone.localdate()
    overdue_invoices = Invoice.objects.filter(
        status__in=[Invoice.Status.ISSUED],
        due_date__lt=today,
    )
    count = 0
    for invoice in overdue_invoices:
        if invoice.remaining_balance > Decimal("0.00"):
            Invoice.objects.filter(pk=invoice.pk).update(
                status=Invoice.Status.OVERDUE,
                updated_at=timezone.now(),
            )
            count += 1
    return count


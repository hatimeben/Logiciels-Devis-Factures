from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings

from apps.catalog.models import ExpenseCategory, Service, ServiceCategory
from apps.partners.models import Customer
from apps.partners.models import Supplier

from .models import (
    Expense,
    ExpenseHistory,
    ExpensePayment,
    Invoice,
    Payment,
    PaymentAllocation,
    Quote,
)
from .money import round_money


class FinanceModelTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Client test")
        self.category = ExpenseCategory.objects.create(name="Frais généraux")
        self.expense = Expense.objects.create(
            category=self.category,
            description="Achat de matériel",
            expense_date=date(2026, 10, 1),
            amount=Decimal("100.00"),
        )

    def test_a_quote_can_be_linked_to_only_one_invoice(self):
        quote = Quote.objects.create(customer=self.customer)
        Invoice.objects.bulk_create(
            [Invoice(customer=self.customer, quote=quote)]
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.bulk_create(
                [Invoice(customer=self.customer, quote=quote)]
            )

    def test_document_numbers_are_unique(self):
        Quote.objects.bulk_create(
            [Quote(customer=self.customer, number="DEV-2026-0001")]
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Quote.objects.bulk_create(
                [Quote(customer=self.customer, number="DEV-2026-0001")]
            )

        Invoice.objects.bulk_create(
            [Invoice(customer=self.customer, number="FAC-2026-0001")]
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.bulk_create(
                [Invoice(customer=self.customer, number="FAC-2026-0001")]
            )

    def test_payment_allocations_keep_decimal_precision(self):
        invoice = Invoice.objects.create(customer=self.customer)
        payment = Payment.objects.create(
            customer=self.customer,
            payment_date=date(2026, 10, 25),
            amount=Decimal("1234.56"),
            method=Payment.Method.BANK_TRANSFER,
        )

        allocation = PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("1234.56"),
        )

        self.assertEqual(allocation.amount, Decimal("1234.56"))

    def test_payment_allocation_must_be_positive(self):
        invoice = Invoice.objects.create(customer=self.customer)
        payment = Payment.objects.create(
            customer=self.customer,
            payment_date=date(2026, 10, 25),
            amount=Decimal("1.00"),
            method=Payment.Method.OTHER,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentAllocation.objects.create(
                payment=payment,
                invoice=invoice,
                amount=Decimal("0.00"),
            )

    def test_document_history_requires_exactly_one_document(self):
        from .models import DocumentHistory

        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentHistory.objects.create(event=DocumentHistory.Event.CREATED)

    def test_expense_supports_multiple_partial_payments_and_calculates_balance(self):
        ExpensePayment.objects.create(
            expense=self.expense,
            payment_date=date(2026, 10, 2),
            amount=Decimal("30.25"),
            method=ExpensePayment.Method.BANK_TRANSFER,
        )
        ExpensePayment.objects.create(
            expense=self.expense,
            payment_date=date(2026, 10, 3),
            amount=Decimal("19.75"),
            method=ExpensePayment.Method.CASH,
        )

        self.assertEqual(self.expense.amount_paid, Decimal("50.00"))
        self.assertEqual(self.expense.remaining_balance, Decimal("50.00"))
        self.assertEqual(self.expense.payment_status, "Partiellement payée")

    def test_expense_category_can_be_created_from_admin(self):
        user = get_user_model().objects.create_superuser(
            username="reference-admin",
            email="reference-admin@example.test",
            password="safe-test-password",
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            "/admin/catalog/expensecategory/add/",
            {
                "name": "Déplacements professionnels",
                "description": "",
                "is_active": "on",
                "_save": "Enregistrer",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ExpenseCategory.objects.filter(name="Déplacements professionnels").exists()
        )

    def test_expense_cannot_be_overpaid_or_reduced_below_payments(self):
        ExpensePayment.objects.create(
            expense=self.expense,
            payment_date=date(2026, 10, 2),
            amount=Decimal("80.00"),
            method=ExpensePayment.Method.CASH,
        )

        with self.assertRaises(ValidationError):
            ExpensePayment(
                expense=self.expense,
                payment_date=date(2026, 10, 3),
                amount=Decimal("20.01"),
                method=ExpensePayment.Method.CASH,
            ).save()

        self.expense.amount = Decimal("79.99")
        with self.assertRaises(ValidationError):
            self.expense.save()

    def test_database_rejects_overpayment_bypassing_model_validation(self):
        ExpensePayment.objects.create(
            expense=self.expense,
            payment_date=date(2026, 10, 2),
            amount=Decimal("80.00"),
            method=ExpensePayment.Method.CASH,
        )

        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO finance_expensepayment "
                    "(expense_id, idempotency_key, payment_date, amount, method, reference, notes, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        self.expense.pk,
                        "a7c9bb2c-c40d-44e4-bc30-aa9207f230fa",
                        "2026-10-03",
                        "20.01",
                        ExpensePayment.Method.CASH,
                        "",
                        "",
                        "2026-10-03 12:00:00",
                    ],
                )

        self.assertEqual(self.expense.amount_paid, Decimal("80.00"))

    def test_expense_payment_rejects_zero_and_negative_amounts(self):
        for amount in [Decimal("0.00"), Decimal("-0.01")]:
            with self.subTest(amount=amount):
                with self.assertRaises(ValidationError):
                    ExpensePayment.objects.create(
                        expense=self.expense,
                        payment_date=date(2026, 10, 2),
                        amount=amount,
                        method=ExpensePayment.Method.CASH,
                    )

        self.assertFalse(ExpensePayment.objects.filter(expense=self.expense).exists())

    def test_database_rejects_reducing_expense_below_payments(self):
        ExpensePayment.objects.create(
            expense=self.expense,
            payment_date=date(2026, 10, 2),
            amount=Decimal("80.00"),
            method=ExpensePayment.Method.CASH,
        )

        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE finance_expense SET amount = %s WHERE id = %s",
                    ["79.99", self.expense.pk],
                )

        self.expense.refresh_from_db()
        self.assertEqual(self.expense.amount, Decimal("100.00"))

    def test_fully_paid_expense_has_zero_balance(self):
        ExpensePayment.objects.create(
            expense=self.expense,
            payment_date=date(2026, 10, 2),
            amount=Decimal("100.00"),
            method=ExpensePayment.Method.CHEQUE,
        )
        self.assertEqual(self.expense.remaining_balance, Decimal("0.00"))
        self.assertEqual(self.expense.payment_status, "Payée")

    def test_money_rounding_uses_half_up_to_two_decimals(self):
        self.assertEqual(round_money(Decimal("1.004")), Decimal("1.00"))
        self.assertEqual(round_money(Decimal("1.005")), Decimal("1.01"))
        self.assertEqual(round_money(Decimal("1.015")), Decimal("1.02"))

    def test_recorded_expense_payment_cannot_be_modified_or_deleted(self):
        payment = ExpensePayment.objects.create(
            expense=self.expense,
            payment_date=date(2026, 10, 2),
            amount=Decimal("10.00"),
            method=ExpensePayment.Method.CASH,
        )

        payment.amount = Decimal("9.00")
        with self.assertRaises(ValidationError):
            payment.save()
        with self.assertRaises(ValidationError):
            payment.delete()
        self.assertTrue(ExpensePayment.objects.filter(pk=payment.pk).exists())

    def test_database_rejects_payment_update_and_delete(self):
        payment = ExpensePayment.objects.create(
            expense=self.expense,
            payment_date=date(2026, 10, 2),
            amount=Decimal("10.00"),
            method=ExpensePayment.Method.CASH,
        )

        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE finance_expensepayment SET amount = %s WHERE id = %s",
                    ["9.00", payment.pk],
                )
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM finance_expensepayment WHERE id = %s",
                    [payment.pk],
                )

        self.assertTrue(ExpensePayment.objects.filter(pk=payment.pk).exists())

    def test_model_saves_and_audit_entries_roll_back_together(self):
        initial_history_count = ExpenseHistory.objects.count()

        with patch.object(
            ExpenseHistory.objects,
            "create",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                ExpensePayment.objects.create(
                    expense=self.expense,
                    payment_date=date(2026, 10, 2),
                    amount=Decimal("10.00"),
                    method=ExpensePayment.Method.CASH,
                )

        self.assertFalse(ExpensePayment.objects.filter(expense=self.expense).exists())
        self.assertEqual(ExpenseHistory.objects.count(), initial_history_count)

    def test_direct_expense_save_is_audited_and_failure_rolls_back(self):
        old_description = self.expense.description
        history_count = ExpenseHistory.objects.count()

        with patch.object(
            ExpenseHistory.objects,
            "create",
            side_effect=RuntimeError("audit unavailable"),
        ):
            self.expense.description = "Modification sans trace"
            with self.assertRaises(RuntimeError):
                self.expense.save()

        self.expense.refresh_from_db()
        self.assertEqual(self.expense.description, old_description)
        self.assertEqual(ExpenseHistory.objects.count(), history_count)

        self.expense.description = "Modification tracée"
        self.expense.save()
        history = ExpenseHistory.objects.filter(
            expense=self.expense,
            event=ExpenseHistory.Event.UPDATED,
        ).latest("created_at")
        self.assertEqual(history.snapshot["before"]["description"], old_description)
        self.assertEqual(history.snapshot["after"]["description"], "Modification tracée")

    def test_expense_admin_records_audit_history_and_protects_deletion(self):
        user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.test",
            password="safe-test-password",
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            f"/admin/finance/expense/{self.expense.pk}/change/",
            {
                "category": self.category.pk,
                "description": "Achat modifié",
                "expense_date": "02/10/2026",
                "amount": "100.00",
                "supplier": "",
                "project": "",
                "receipt-clear": "",
                "receipt": "",
                "_save": "Enregistrer",
            },
        )

        self.assertEqual(response.status_code, 302)
        history = ExpenseHistory.objects.get(
            expense=self.expense,
            event=ExpenseHistory.Event.UPDATED,
        )
        self.assertEqual(history.event, ExpenseHistory.Event.UPDATED)
        self.assertEqual(history.actor, user)
        self.assertEqual(history.snapshot["before"]["description"], "Achat de matériel")
        self.assertEqual(history.snapshot["after"]["description"], "Achat modifié")
        delete_response = client.get(
            f"/admin/finance/expense/{self.expense.pk}/delete/"
        )
        self.assertEqual(delete_response.status_code, 403)

    def test_history_retains_actor_name_if_user_is_deleted(self):
        user = get_user_model().objects.create_superuser(
            username="historical-actor",
            email="historical-actor@example.test",
            password="safe-test-password",
        )
        self.expense.audit_actor = user
        self.expense.description = "Modification par utilisateur"
        self.expense.save()
        history = ExpenseHistory.objects.get(
            expense=self.expense,
            event=ExpenseHistory.Event.UPDATED,
        )

        user.is_active = False
        user.save()
        history.refresh_from_db()
        self.assertEqual(history.actor, user)
        self.assertEqual(history.snapshot["actor_username"], "historical-actor")

        user.delete()
        history.refresh_from_db()
        self.assertIsNone(history.actor)
        self.assertEqual(history.snapshot["actor_username"], "historical-actor")

    def test_history_is_read_only_in_admin_even_for_staff_with_permissions(self):
        user = get_user_model().objects.create_user(
            username="history-staff",
            email="history-staff@example.test",
            password="safe-test-password",
            is_staff=True,
        )
        all_history_permissions = Permission.objects.filter(
            content_type__app_label="finance",
            content_type__model="expensehistory",
        )
        user.user_permissions.add(*all_history_permissions)
        client = Client()
        client.force_login(user)
        history = ExpenseHistory.objects.filter(expense=self.expense).first()
        self.assertIsNotNone(history)

        change_url = f"/admin/finance/expensehistory/{history.pk}/change/"
        self.assertEqual(client.get(change_url).status_code, 200)
        self.assertEqual(
            client.post(
                change_url,
                {"snapshot": '{"tampered": true}', "_save": "Enregistrer"},
            ).status_code,
            403,
        )
        self.assertEqual(
            client.get(f"/admin/finance/expensehistory/{history.pk}/delete/").status_code,
            403,
        )
        self.assertEqual(client.get("/admin/finance/expensehistory/add/").status_code, 403)
        history.refresh_from_db()
        self.assertNotIn("tampered", history.snapshot)

    @override_settings(DATE_INPUT_FORMATS=["%d/%m/%Y"])
    def test_french_date_input_is_accepted_in_admin(self):
        user = get_user_model().objects.create_superuser(
            username="date-admin",
            email="date-admin@example.test",
            password="safe-test-password",
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            f"/admin/finance/expense/{self.expense.pk}/change/",
            {
                "category": self.category.pk,
                "description": self.expense.description,
                "expense_date": "05/10/2026",
                "amount": "100.00",
                "supplier": "",
                "project": "",
                "receipt-clear": "",
                "receipt": "",
                "_save": "Enregistrer",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.expense_date, date(2026, 10, 5))

    def test_service_default_price_is_required_in_admin(self):
        category = ServiceCategory.objects.create(name="Formation")
        user = get_user_model().objects.create_superuser(
            username="catalog-admin",
            email="catalog-admin@example.test",
            password="safe-test-password",
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            "/admin/catalog/service/add/",
            {
                "category": category.pk,
                "name": "Formation Excel",
                "description": "",
                "default_unit_price": "",
                "is_active": "on",
                "_save": "Enregistrer",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Service.objects.count(), 0)

    def test_supplier_can_be_attached_to_expense(self):
        supplier = Supplier.objects.create(name="Fournisseur test")
        self.expense.supplier = supplier
        self.expense.save()

        self.assertEqual(self.expense.supplier, supplier)

    def test_expense_payment_admin_records_payment_and_audit(self):
        user = get_user_model().objects.create_superuser(
            username="payment-admin",
            email="payment-admin@example.test",
            password="safe-test-password",
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            "/admin/finance/expensepayment/add/",
            {
                "expense": self.expense.pk,
                "idempotency_key": "4e5bc947-168c-431d-ae7e-37c6f4d96a21",
                "payment_date": "03/10/2026",
                "amount": "25.50",
                "method": ExpensePayment.Method.BANK_TRANSFER,
                "reference": "VIR-001",
                "notes": "",
                "_save": "Enregistrer",
            },
        )

        self.assertEqual(response.status_code, 302)
        payment = ExpensePayment.objects.get(expense=self.expense)
        self.assertEqual(payment.amount, Decimal("25.50"))
        history = ExpenseHistory.objects.get(
            expense=self.expense,
            event=ExpenseHistory.Event.PAYMENT_RECORDED,
        )
        self.assertEqual(history.actor, user)
        self.assertEqual(history.snapshot["amount"], "25.50")

    def test_duplicate_admin_submission_is_rejected_by_idempotency_key(self):
        user = get_user_model().objects.create_superuser(
            username="dedup-admin",
            email="dedup-admin@example.test",
            password="safe-test-password",
        )
        client = Client()
        client.force_login(user)
        payload = {
            "expense": self.expense.pk,
            "idempotency_key": "6c0e33a3-d404-4484-a1e5-12a83752c43f",
            "payment_date": "03/10/2026",
            "amount": "25.50",
            "method": ExpensePayment.Method.BANK_TRANSFER,
            "reference": "VIR-001",
            "notes": "",
            "_save": "Enregistrer",
        }

        first_response = client.post("/admin/finance/expensepayment/add/", payload)
        duplicate_response = client.post("/admin/finance/expensepayment/add/", payload)

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(ExpensePayment.objects.filter(expense=self.expense).count(), 1)
        self.assertEqual(
            ExpenseHistory.objects.filter(
                expense=self.expense,
                event=ExpenseHistory.Event.PAYMENT_RECORDED,
            ).count(),
            1,
        )

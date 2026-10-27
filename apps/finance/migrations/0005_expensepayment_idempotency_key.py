import uuid

from django.db import migrations, models


def populate_idempotency_keys(apps, schema_editor):
    ExpensePayment = apps.get_model("finance", "ExpensePayment")
    database = schema_editor.connection.alias
    for payment in ExpensePayment.objects.using(database).filter(
        idempotency_key__isnull=True
    ).iterator():
        payment.idempotency_key = uuid.uuid4()
        payment.save(update_fields=["idempotency_key"])


CREATE_GUARDS = [
    (
        "CREATE TRIGGER finance_expensepayment_limit_insert "
        "BEFORE INSERT ON finance_expensepayment "
        "FOR EACH ROW WHEN "
        "(SELECT COALESCE(SUM(amount), 0) FROM finance_expensepayment "
        "WHERE expense_id = NEW.expense_id) + NEW.amount > "
        "(SELECT amount FROM finance_expense WHERE id = NEW.expense_id) "
        "BEGIN SELECT RAISE(ABORT, 'expense payment exceeds remaining balance'); END;"
    ),
    (
        "CREATE TRIGGER finance_expense_amount_guard "
        "BEFORE UPDATE OF amount ON finance_expense "
        "FOR EACH ROW WHEN "
        "(SELECT COALESCE(SUM(amount), 0) FROM finance_expensepayment "
        "WHERE expense_id = OLD.id) > NEW.amount "
        "BEGIN SELECT RAISE(ABORT, 'expense amount is below recorded payments'); END;"
    ),
    (
        "CREATE TRIGGER finance_expensepayment_immutable_update "
        "BEFORE UPDATE ON finance_expensepayment "
        "BEGIN SELECT RAISE(ABORT, 'expense payments are immutable'); END;"
    ),
    (
        "CREATE TRIGGER finance_expensepayment_immutable_delete "
        "BEFORE DELETE ON finance_expensepayment "
        "BEGIN SELECT RAISE(ABORT, 'expense payments cannot be deleted'); END;"
    ),
]


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0004_expense_payment_database_guards"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "DROP TRIGGER IF EXISTS finance_expensepayment_limit_insert;",
                "DROP TRIGGER IF EXISTS finance_expense_amount_guard;",
                "DROP TRIGGER IF EXISTS finance_expensepayment_immutable_update;",
                "DROP TRIGGER IF EXISTS finance_expensepayment_immutable_delete;",
            ],
            reverse_sql=CREATE_GUARDS,
        ),
        migrations.AddField(
            model_name="expensepayment",
            name="idempotency_key",
            field=models.UUIDField(
                null=True,
                verbose_name="clé anti-doublon",
            ),
        ),
        migrations.RunPython(populate_idempotency_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="expensepayment",
            name="idempotency_key",
            field=models.UUIDField(
                default=uuid.uuid4,
                unique=True,
                verbose_name="clé anti-doublon",
            ),
        ),
        *[
            migrations.RunSQL(sql=sql, reverse_sql=migrations.RunSQL.noop)
            for sql in CREATE_GUARDS
        ],
    ]

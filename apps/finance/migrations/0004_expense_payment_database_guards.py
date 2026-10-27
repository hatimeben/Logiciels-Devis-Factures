from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0003_alter_expense_amount_expensehistory_expensepayment"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE TRIGGER finance_expensepayment_limit_insert "
                "BEFORE INSERT ON finance_expensepayment "
                "FOR EACH ROW WHEN "
                "(SELECT COALESCE(SUM(amount), 0) FROM finance_expensepayment "
                "WHERE expense_id = NEW.expense_id) + NEW.amount > "
                "(SELECT amount FROM finance_expense WHERE id = NEW.expense_id) "
                "BEGIN SELECT RAISE(ABORT, 'expense payment exceeds remaining balance'); END;"
            ),
            reverse_sql="DROP TRIGGER finance_expensepayment_limit_insert;",
        ),
        migrations.RunSQL(
            sql=(
                "CREATE TRIGGER finance_expense_amount_guard "
                "BEFORE UPDATE OF amount ON finance_expense "
                "FOR EACH ROW WHEN "
                "(SELECT COALESCE(SUM(amount), 0) FROM finance_expensepayment "
                "WHERE expense_id = OLD.id) > NEW.amount "
                "BEGIN SELECT RAISE(ABORT, 'expense amount is below recorded payments'); END;"
            ),
            reverse_sql="DROP TRIGGER finance_expense_amount_guard;",
        ),
        migrations.RunSQL(
            sql=(
                "CREATE TRIGGER finance_expensepayment_immutable_update "
                "BEFORE UPDATE ON finance_expensepayment "
                "BEGIN SELECT RAISE(ABORT, 'expense payments are immutable'); END;"
            ),
            reverse_sql="DROP TRIGGER finance_expensepayment_immutable_update;",
        ),
        migrations.RunSQL(
            sql=(
                "CREATE TRIGGER finance_expensepayment_immutable_delete "
                "BEFORE DELETE ON finance_expensepayment "
                "BEGIN SELECT RAISE(ABORT, 'expense payments cannot be deleted'); END;"
            ),
            reverse_sql="DROP TRIGGER finance_expensepayment_immutable_delete;",
        ),
    ]

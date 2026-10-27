import re

from django.db import migrations


def seed_existing_invoice_counters(apps, schema_editor):
    database = schema_editor.connection.alias
    settings_model = apps.get_model("finance", "InvoiceNumberingSettings")
    counter_model = apps.get_model("finance", "InvoiceNumberCounter")
    invoice_model = apps.get_model("finance", "Invoice")
    numbering = settings_model.objects.using(database).get(pk=1)
    separator = re.escape(numbering.separator)
    if numbering.include_year:
        pattern = re.compile(
            rf"^{re.escape(numbering.prefix)}{separator}(\d{{4}}){separator}(\d+)$"
        )
    else:
        pattern = re.compile(rf"^{re.escape(numbering.prefix)}{separator}(\d+)$")

    counters = {}
    numbers = invoice_model.objects.using(database).exclude(number__isnull=True).values_list(
        "number", flat=True
    )
    for number in numbers.iterator():
        match = pattern.fullmatch(number)
        if not match:
            continue
        year, sequence = (
            (int(match.group(1)), int(match.group(2)))
            if numbering.include_year
            else (0, int(match.group(1)))
        )
        counters[year] = max(counters.get(year, 0), sequence)
    for year, last_number in counters.items():
        counter_model.objects.using(database).update_or_create(
            year=year,
            defaults={"last_number": last_number},
        )


class Migration(migrations.Migration):
    dependencies = [("finance", "0009_invoicenumbercounter_invoicenumberingsettings_and_more")]

    operations = [migrations.RunPython(seed_existing_invoice_counters, migrations.RunPython.noop)]

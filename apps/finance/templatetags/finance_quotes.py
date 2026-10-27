from decimal import Decimal

from django import template

from apps.finance.models import QuoteLine
from apps.finance.money import round_money
from apps.finance.quote_calculations import calculate_quote_totals


register = template.Library()


@register.filter
def money(value):
    amount = round_money(Decimal(value or 0))
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


@register.filter
def quote_total(quote, key):
    if key not in {"gross", "discount", "net", "tax", "total"}:
        return Decimal("0.00")
    return calculate_quote_totals(quote.lines.all())[key]

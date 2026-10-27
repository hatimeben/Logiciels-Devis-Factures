from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")
MONEY_ROUNDING = ROUND_HALF_UP


def round_money(amount):
    if not isinstance(amount, Decimal):
        amount = Decimal(amount)
    return amount.quantize(MONEY_QUANTUM, rounding=MONEY_ROUNDING)

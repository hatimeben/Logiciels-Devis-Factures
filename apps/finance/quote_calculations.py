from decimal import Decimal

from .money import round_money


ZERO = Decimal("0.00")


def calculate_quote_line(quantity, unit_price, discount_type, discount_value, tax_percentage):
    quantity = Decimal(quantity)
    unit_price = Decimal(unit_price)
    discount_value = Decimal(discount_value)
    tax_percentage = Decimal(tax_percentage or ZERO)

    gross = round_money(quantity * unit_price)
    if discount_type == "percentage":
        if discount_value < 0 or discount_value > 100:
            raise ValueError("La remise en pourcentage doit être comprise entre 0 et 100.")
        discount = round_money(gross * discount_value / Decimal("100"))
    elif discount_type == "fixed":
        if discount_value < 0 or discount_value > gross:
            raise ValueError("La remise fixe doit être comprise entre zéro et le montant HT.")
        discount = round_money(discount_value)
    else:
        raise ValueError("Le type de remise est invalide.")

    if tax_percentage < 0 or tax_percentage > 100:
        raise ValueError("Le taux de taxe doit être compris entre 0 et 100.")

    net = round_money(gross - discount)
    tax = round_money(net * tax_percentage / Decimal("100"))
    return {
        "gross": gross,
        "discount": discount,
        "net": net,
        "tax": tax,
        "total": round_money(net + tax),
    }


def calculate_quote_totals(lines):
    totals = {
        "gross": ZERO,
        "discount": ZERO,
        "net": ZERO,
        "tax": ZERO,
        "total": ZERO,
    }
    for line in lines:
        amounts = calculate_quote_line(
            line.quantity,
            line.unit_price,
            line.discount_type,
            line.discount_value,
            line.tax_percentage,
        )
        for key, value in amounts.items():
            totals[key] += value
    return {key: round_money(value) for key, value in totals.items()}

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import ExpenseCategory, Service, ServiceCategory, TaxRate


class CatalogModelTests(TestCase):
    def test_custom_categories_services_and_tax_rates_are_persisted(self):
        expense_category = ExpenseCategory.objects.create(name="Déplacements")
        service_category = ServiceCategory.objects.create(name="Formation")
        service = Service.objects.create(
            category=service_category,
            name="Formation Excel",
            default_unit_price=Decimal("1250.50"),
        )
        tax_rate = TaxRate.objects.create(name="Taux paramétrable", percentage=Decimal("7.25"))

        self.assertEqual(expense_category.name, "Déplacements")
        self.assertEqual(service.category.name, "Formation")
        self.assertEqual(service.default_unit_price, Decimal("1250.50"))
        self.assertEqual(tax_rate.percentage, Decimal("7.25"))

    def test_service_name_is_unique_within_a_category(self):
        category = ServiceCategory.objects.create(name="Conseil")
        Service.objects.create(
            category=category,
            name="Audit",
            default_unit_price=Decimal("500.00"),
        )

        with self.assertRaises(ValidationError):
            Service.objects.create(
                category=category,
                name="Audit",
                default_unit_price=Decimal("500.00"),
            )

    def test_service_requires_a_default_sale_price(self):
        category = ServiceCategory.objects.create(name="Développement")

        with self.assertRaises(ValidationError):
            Service.objects.create(category=category, name="Application métier")

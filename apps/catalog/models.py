from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class ExpenseCategory(models.Model):
    name = models.CharField("nom", max_length=100, unique=True)
    description = models.TextField("description", blank=True)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "catégorie de dépense"
        verbose_name_plural = "catégories de dépenses"

    def __str__(self):
        return self.name


class ServiceCategory(models.Model):
    name = models.CharField("nom", max_length=100, unique=True)
    description = models.TextField("description", blank=True)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "catégorie de service"
        verbose_name_plural = "catégories de services"

    def __str__(self):
        return self.name


class TaxRate(models.Model):
    name = models.CharField("libellé", max_length=100, unique=True)
    percentage = models.DecimalField(
        "pourcentage",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "taux de taxe"
        verbose_name_plural = "taux de taxe"
        constraints = [
            models.CheckConstraint(
                condition=Q(percentage__gte=0, percentage__lte=100),
                name="tax_rate_percentage_between_zero_and_hundred",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.percentage} %)"


class Service(models.Model):
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.PROTECT,
        related_name="services",
        verbose_name="catégorie",
    )
    name = models.CharField("nom", max_length=200)
    description = models.TextField("description", blank=True)
    default_unit_price = models.DecimalField(
        "prix de vente HT par défaut (MAD)",
        max_digits=12,
        decimal_places=2,
        null=True,
    )
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "service"
        verbose_name_plural = "services"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "name"],
                name="unique_service_name_per_category",
            ),
        ]

    def clean(self):
        super().clean()
        if self.default_unit_price is None:
            raise ValidationError(
                {"default_unit_price": "Un prix de vente HT par défaut doit être renseigné."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name

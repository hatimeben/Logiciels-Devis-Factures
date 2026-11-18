from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory

from apps.catalog.models import Article, Service, TaxRate
from apps.partners.models import Customer
from apps.projects.models import Project

from .models import Invoice, InvoiceLine, QuoteLine
from .quote_forms import ArticleSelect, ServiceSelect, TaxRateSelect


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["customer", "project", "due_date", "notes"]
        widgets = {
            "due_date": forms.DateInput(attrs={"placeholder": "JJ/MM/AAAA"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)
        self.fields["project"].queryset = Project.objects.all()
        self.fields["project"].required = False
        if self.instance and self.instance.quote_id:
            self.fields["customer"].disabled = True


class InvoiceDraftForm(InvoiceForm):
    """Pour la compatibilité ascendante avec les vues et tests existants."""
    pass


class InvoiceLineForm(forms.ModelForm):
    class Meta:
        model = InvoiceLine
        fields = [
            "article",
            "service",
            "description",
            "quantity",
            "unit_price",
            "discount_type",
            "discount_value",
            "tax_rate",
        ]
        widgets = {
            "description": forms.TextInput(attrs={"maxlength": 250}),
            "quantity": forms.NumberInput(attrs={"step": "1", "min": "1"}),
            "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "discount_value": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        articles = list(Article.objects.filter(is_active=True).order_by("reference"))
        self.fields["article"].queryset = Article.objects.filter(
            pk__in=[art.pk for art in articles]
        )
        self.fields["article"].required = False
        self.fields["article"].widget = ArticleSelect(
            articles,
            choices=self.fields["article"].choices,
        )
        services = list(Service.objects.filter(is_active=True).order_by("name"))
        self.fields["service"].queryset = Service.objects.filter(
            pk__in=[service.pk for service in services]
        )
        self.fields["service"].required = False
        self.fields["service"].widget = ServiceSelect(
            services,
            choices=self.fields["service"].choices,
        )
        rates = list(TaxRate.objects.filter(is_active=True).order_by("name"))
        self.fields["tax_rate"].queryset = TaxRate.objects.filter(
            pk__in=[rate.pk for rate in rates]
        )
        if not self.is_bound and not self.instance.pk and len(rates) == 1:
            self.initial["tax_rate"] = rates[0].pk
        self.fields["tax_rate"].required = False
        self.fields["tax_rate"].widget = TaxRateSelect(
            rates,
            choices=self.fields["tax_rate"].choices,
        )
        self.fields["description"].required = False
        self.fields["unit_price"].required = False

    def clean(self):
        cleaned = super().clean()
        article = cleaned.get("article")
        if article and not cleaned.get("description"):
            cleaned["description"] = f"{article.reference} - {article.designation}"
        if article and cleaned.get("unit_price") is None:
            cleaned["unit_price"] = article.prix_vente_ht

        service = cleaned.get("service")
        if service and not cleaned.get("description"):
            cleaned["description"] = service.name
        if service and cleaned.get("unit_price") is None:
            cleaned["unit_price"] = service.default_unit_price
        if not cleaned.get("description"):
            self.add_error("description", "Saisissez une désignation pour cette ligne libre.")
        if cleaned.get("unit_price") is None:
            self.add_error("unit_price", "Saisissez un prix unitaire HT.")
        tax_rate = cleaned.get("tax_rate")
        if tax_rate:
            self.instance.tax_percentage = None
            self.instance.tax_name = ""
        return cleaned

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is not None:
            if quantity < 1:
                raise ValidationError("La quantité doit être supérieure ou égale à 1.")
            if quantity % 1 != 0:
                raise ValidationError("La quantité doit être un nombre entier (ex: 1, 2, 5).")
        return quantity

    def clean_discount_value(self):
        value = self.cleaned_data["discount_value"]
        discount_type = self.cleaned_data.get("discount_type")
        if discount_type == QuoteLine.DiscountType.PERCENTAGE and value > 100:
            raise ValidationError("La remise en pourcentage ne peut pas dépasser 100 %.")
        quantity = self.cleaned_data.get("quantity")
        unit_price = self.cleaned_data.get("unit_price")
        if (
            discount_type == QuoteLine.DiscountType.FIXED
            and quantity is not None
            and unit_price is not None
            and value > quantity * unit_price
        ):
            raise ValidationError("La remise ne peut pas dépasser le montant HT de la ligne.")
        return value


InvoiceLineFormSet = inlineformset_factory(
    Invoice,
    InvoiceLine,
    form=InvoiceLineForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)

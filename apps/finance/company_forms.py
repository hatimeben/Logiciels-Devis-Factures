from django import forms

from .models import CompanySettings


class CompanySettingsForm(forms.ModelForm):
    class Meta:
        model = CompanySettings
        fields = [
            "name",
            "tagline",
            "legal_form",
            "capital",
            "logo",
            "ice",
            "tax_identifier",
            "trade_registry",
            "patente",
            "cnss",
            "address",
            "city",
            "postal_code",
            "country",
            "phone",
            "email",
            "website",
            "bank_name",
            "rib",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Ex: BENCHTECH SARL"}),
            "tagline": forms.TextInput(attrs={"placeholder": "Ex: Solutions informatiques, conseil & formation"}),
            "legal_form": forms.TextInput(attrs={"placeholder": "Ex: SARL, SARL-AU, SA..."}),
            "capital": forms.NumberInput(attrs={"step": "0.01", "placeholder": "Ex: 100000.00"}),
            "ice": forms.TextInput(attrs={"placeholder": "Ex: 001234567890001 (15 chiffres)", "maxlength": "20"}),
            "tax_identifier": forms.TextInput(attrs={"placeholder": "Ex: 12345678"}),
            "trade_registry": forms.TextInput(attrs={"placeholder": "Ex: 123456 Casablanca"}),
            "patente": forms.TextInput(attrs={"placeholder": "Ex: 12345678"}),
            "cnss": forms.TextInput(attrs={"placeholder": "Ex: 1234567"}),
            "address": forms.Textarea(attrs={"rows": 2, "placeholder": "Adresse du siège ou établissement"}),
            "city": forms.TextInput(attrs={"placeholder": "Ex: Casablanca"}),
            "postal_code": forms.TextInput(attrs={"placeholder": "Ex: 20000"}),
            "country": forms.TextInput(attrs={"placeholder": "Ex: Maroc"}),
            "phone": forms.TextInput(attrs={"placeholder": "Ex: +212 5 22 00 00 00"}),
            "email": forms.EmailInput(attrs={"placeholder": "contact@benchtech.ma"}),
            "website": forms.TextInput(attrs={"placeholder": "www.benchtech.ma"}),
            "bank_name": forms.TextInput(attrs={"placeholder": "Ex: Attijariwafa bank, BCP, BMCE..."}),
            "rib": forms.TextInput(attrs={"placeholder": "Ex: 007780000123456789012345 (24 chiffres)", "maxlength": "35"}),
        }


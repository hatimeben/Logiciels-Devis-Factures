from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.partners.models import Customer
from .models import Payment


class InvoicePaymentForm(forms.Form):
    payment_date = forms.DateField(
        label="Date du règlement",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"placeholder": "JJ/MM/AAAA"}),
    )
    amount = forms.DecimalField(
        label="Montant (MAD)",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    method = forms.ChoiceField(
        label="Mode de règlement",
        choices=Payment.Method.choices,
        initial=Payment.Method.BANK_TRANSFER,
    )
    reference = forms.CharField(
        label="Référence (N° virement, chèque, etc.)",
        max_length=100,
        required=False,
    )
    notes = forms.CharField(
        label="Notes",
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
    )

    def __init__(self, *args, max_amount=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_amount = max_amount
        if max_amount is not None and not self.is_bound:
            self.fields["amount"].initial = max_amount

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.max_amount is not None and amount > self.max_amount:
            raise forms.ValidationError(
                f"Le montant ne peut pas dépasser le reste à payer ({self.max_amount} MAD)."
            )
        return amount


class StandalonePaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["customer", "payment_date", "amount", "method", "reference", "notes"]
        widgets = {
            "payment_date": forms.DateInput(attrs={"placeholder": "JJ/MM/AAAA"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True)


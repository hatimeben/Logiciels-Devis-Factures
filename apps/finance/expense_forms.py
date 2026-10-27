from decimal import Decimal
import uuid

from django import forms
from django.utils import timezone

from apps.catalog.models import ExpenseCategory
from apps.partners.models import Supplier
from apps.projects.models import Project
from .models import Expense, ExpensePayment


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = [
            "category",
            "supplier",
            "project",
            "description",
            "expense_date",
            "amount",
            "receipt",
        ]
        widgets = {
            "description": forms.TextInput(attrs={"maxlength": 250, "placeholder": "Ex: Abonnement internet, Achat fournitures..."}),
            "expense_date": forms.DateInput(attrs={"placeholder": "JJ/MM/AAAA"}),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01", "placeholder": "0,00"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True).order_by("name")
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True).order_by("name")
        self.fields["supplier"].required = False
        self.fields["project"].queryset = Project.objects.all().order_by("name")
        self.fields["project"].required = False
        if not self.is_bound and not self.instance.pk:
            self.fields["expense_date"].initial = timezone.localdate


class ExpensePaymentForm(forms.Form):
    payment_date = forms.DateField(
        label="Date du paiement",
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
        label="Mode de paiement",
        choices=ExpensePayment.Method.choices,
        initial=ExpensePayment.Method.BANK_TRANSFER,
    )
    reference = forms.CharField(
        label="Référence (N° chèque, virement, etc.)",
        max_length=100,
        required=False,
    )
    notes = forms.CharField(
        label="Notes",
        widget=forms.Textarea(attrs={"rows": 2}),
        required=False,
    )
    idempotency_key = forms.UUIDField(
        widget=forms.HiddenInput,
        required=False,
    )

    def __init__(self, *args, max_amount=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_amount = max_amount
        if max_amount is not None and not self.is_bound:
            self.fields["amount"].initial = max_amount
        if not self.is_bound:
            self.fields["idempotency_key"].initial = uuid.uuid4()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.max_amount is not None and amount > self.max_amount:
            raise forms.ValidationError(
                f"Le paiement dépasse le solde restant de la dépense ({self.max_amount} MAD)."
            )
        return amount

    def clean_idempotency_key(self):
        key = self.cleaned_data.get("idempotency_key")
        if not key:
            key = uuid.uuid4()
        if ExpensePayment.objects.filter(idempotency_key=key).exists():
            raise forms.ValidationError("Ce paiement a déjà été enregistré.")
        return key


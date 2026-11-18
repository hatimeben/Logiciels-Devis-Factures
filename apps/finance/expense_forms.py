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
            "is_tax_deductible",
            "non_deductible_reason",
            "non_deductible_amount",
            "fiscal_notes",
        ]
        widgets = {
            "description": forms.TextInput(attrs={"maxlength": 250, "placeholder": "Ex: Abonnement internet, Achat fournitures..."}),
            "expense_date": forms.DateInput(attrs={"placeholder": "JJ/MM/AAAA"}),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01", "placeholder": "0,00"}),
            "is_tax_deductible": forms.CheckboxInput(attrs={"class": "fiscal-deductible-toggle"}),
            "non_deductible_reason": forms.Select(attrs={"class": "fiscal-reason-select"}),
            "non_deductible_amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.00", "placeholder": "0,00 (si réintégration partielle)"}),
            "fiscal_notes": forms.Textarea(attrs={"rows": 2, "placeholder": "Précisions ou justification pour l'expert-comptable / fiduciaire..."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = ExpenseCategory.objects.filter(is_active=True).order_by("name")
        self.fields["supplier"].queryset = Supplier.objects.filter(is_active=True).order_by("name")
        self.fields["supplier"].required = False
        self.fields["project"].queryset = Project.objects.all().order_by("name")
        self.fields["project"].required = False
        self.fields["non_deductible_reason"].required = False
        self.fields["non_deductible_amount"].required = False
        self.fields["fiscal_notes"].required = False
        if not self.is_bound and not self.instance.pk:
            self.fields["expense_date"].initial = timezone.localdate
            self.fields["is_tax_deductible"].initial = True

    def clean(self):
        cleaned_data = super().clean()
        is_tax_deductible = cleaned_data.get("is_tax_deductible")
        reason = cleaned_data.get("non_deductible_reason") or Expense.NonDeductibleReason.NONE
        amount = cleaned_data.get("amount") or Decimal("0.00")
        non_deductible_amt = cleaned_data.get("non_deductible_amount") or Decimal("0.00")

        if not cleaned_data.get("non_deductible_reason"):
            cleaned_data["non_deductible_reason"] = Expense.NonDeductibleReason.NONE

        # Rétrocompatibilité : si le formulaire a été soumis sans le volet fiscal (tests existants ou API)
        if "fiscal_submitted" not in self.data and "is_tax_deductible" not in self.data:
            cleaned_data["is_tax_deductible"] = True
            is_tax_deductible = True

        # Automatisation 1 : Profil fiscal de la catégorie de charge (PCGE)
        desc = (cleaned_data.get("description") or "").lower()
        category = cleaned_data.get("category")
        if category and not category.is_tax_deductible_default:
            cleaned_data["is_tax_deductible"] = False
            is_tax_deductible = False
            if not cleaned_data.get("non_deductible_reason") or cleaned_data.get("non_deductible_reason") == Expense.NonDeductibleReason.NONE:
                cleaned_data["non_deductible_reason"] = category.default_non_deductible_reason or Expense.NonDeductibleReason.FINES_PENALTIES
            reason = cleaned_data["non_deductible_reason"]

        # Automatisation 2 : Détection intelligente par mots-clés dans la description
        fines_keywords = ["amende", "contravention", "radar", "pénalité", "penalite", "majoration", "infraction", "fourriere", "fourrière"]
        if any(kw in desc for kw in fines_keywords):
            cleaned_data["is_tax_deductible"] = False
            is_tax_deductible = False
            if not cleaned_data.get("non_deductible_reason") or cleaned_data.get("non_deductible_reason") == Expense.NonDeductibleReason.NONE:
                cleaned_data["non_deductible_reason"] = Expense.NonDeductibleReason.FINES_PENALTIES
            if not cleaned_data.get("fiscal_notes"):
                cleaned_data["fiscal_notes"] = "Détecté automatiquement (amende / pénalité légale - Art. 11-I CGI)"
            reason = cleaned_data["non_deductible_reason"]

        if not is_tax_deductible and reason == Expense.NonDeductibleReason.NONE:
            self.add_error(
                "non_deductible_reason",
                "Veuillez sélectionner un motif de non-déductibilité (CGI marocain).",
            )

        if non_deductible_amt > amount:
            self.add_error(
                "non_deductible_amount",
                "Le montant non déductible ne peut pas dépasser le montant total de la dépense.",
            )

        return cleaned_data


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


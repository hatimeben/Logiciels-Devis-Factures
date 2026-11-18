from decimal import Decimal

from django import forms
from apps.partners.models import Supplier

from .models import Article


class StockReceptionForm(forms.Form):
    article = forms.ModelChoiceField(
        queryset=Article.objects.filter(is_active=True).order_by("reference"),
        label="Article à réceptionner",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.filter(is_active=True).order_by("name"),
        required=False,
        label="Fournisseur référencé",
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Sélectionnez un fournisseur existant dans l'annuaire.",
    )
    supplier_name = forms.CharField(
        max_length=200,
        required=False,
        label="Ou Nom du Fournisseur externe",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: Grossiste Informatique SARL"}),
    )
    quantity = forms.DecimalField(
        min_value=Decimal("1"),
        decimal_places=2,
        label="Quantité reçue",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "1", "min": "1", "placeholder": "10"}),
    )
    unit_price_ht = forms.DecimalField(
        min_value=Decimal("0.00"),
        decimal_places=2,
        required=False,
        label="Prix d'achat unitaire HT constaté (MAD)",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Laissez vide pour conserver le prix actuel"}),
    )
    reference_document = forms.CharField(
        max_length=100,
        label="N° BL / Facture Achat Fournisseur",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: BL-2026-90412"}),
        help_text="Référence justificative obligatoire pour la traçabilité DGI et l'audit.",
    )
    update_cost_price = forms.BooleanField(
        initial=True,
        required=False,
        label="Mettre à jour le coût d'achat moyen (PUMP) de la fiche article",
    )
    notes = forms.CharField(
        max_length=255,
        required=False,
        label="Motif / Observations",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: Réassort de stock régulier"}),
    )

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is not None:
            if quantity < 1:
                raise forms.ValidationError("La quantité doit être supérieure ou égale à 1.")
            if quantity % 1 != 0:
                raise forms.ValidationError("La quantité doit être un nombre entier (ex: 1, 2, 5).")
        return quantity


class ArticleForm(forms.ModelForm):
    initial_stock = forms.DecimalField(
        label="Stock physique initial",
        min_value=Decimal("0.00"),
        decimal_places=2,
        initial=Decimal("0.00"),
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "1", "min": "0"}),
        help_text="Quantité actuellement disponible en rayon/entrepôt (nombre entier).",
    )

    class Meta:
        model = Article
        fields = [
            "reference",
            "designation",
            "nature_produit",
            "compte_stock",
            "compte_variation",
            "prix_achat_ht",
            "prix_vente_ht",
            "stock_alerte",
            "description",
        ]
        widgets = {
            "reference": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: MAT-SSD-01"}),
            "designation": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex: Disque SSD NVMe 1 To"}),
            "nature_produit": forms.Select(attrs={"class": "form-control"}),
            "compte_stock": forms.TextInput(attrs={"class": "form-control"}),
            "compte_variation": forms.TextInput(attrs={"class": "form-control"}),
            "prix_achat_ht": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "prix_vente_ht": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "stock_alerte": forms.NumberInput(attrs={"class": "form-control", "step": "1", "min": "0"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def clean_initial_stock(self):
        val = self.cleaned_data.get("initial_stock")
        if val is not None and val % 1 != 0:
            raise forms.ValidationError("Le stock initial doit être un nombre entier.")
        return val

    def clean_stock_alerte(self):
        val = self.cleaned_data.get("stock_alerte")
        if val is not None and val % 1 != 0:
            raise forms.ValidationError("Le seuil d'alerte de stock doit être un nombre entier.")
        return val


from django.contrib import admin
from django.utils.html import format_html

from .models import Article, ExpenseCategory, MouvementStock, Service, ServiceCategory, TaxRate


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "pcge_account", "vat_rate", "is_vat_recoverable", "is_tax_deductible_default", "is_active", "updated_at"]
    list_filter = ["is_active", "is_vat_recoverable", "is_tax_deductible_default"]
    search_fields = ["name", "pcge_account", "description"]


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "default_unit_price", "is_active"]
    list_filter = ["category", "is_active"]
    search_fields = ["name", "description"]


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ["name", "percentage", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]


class MouvementStockInline(admin.TabularInline):
    model = MouvementStock
    extra = 0
    fields = [
        "date_mouvement",
        "type_mouvement",
        "quantite",
        "prix_unitaire_ht",
        "stock_avant",
        "stock_apres",
        "reference_document",
        "fournisseur",
        "motif",
        "auteur",
    ]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = [
        "reference",
        "designation",
        "nature_produit",
        "compte_stock",
        "prix_achat_ht",
        "prix_vente_ht",
        "stock_actuel_badge",
        "valeur_stock_display",
        "stock_alerte",
        "is_active",
    ]
    list_filter = ["nature_produit", "is_active"]
    search_fields = ["reference", "designation", "description", "compte_stock"]
    readonly_fields = ["stock_actuel", "created_at", "updated_at"]
    inlines = [MouvementStockInline]

    fieldsets = [
        (
            "Identification",
            {
                "fields": ["reference", "designation", "description", "nature_produit", "is_active"],
            },
        ),
        (
            "Comptabilité PCGE Maroc",
            {
                "fields": ["compte_stock", "compte_variation", "compte_achat", "compte_vente"],
                "classes": ["collapse"],
            },
        ),
        (
            "Tarification & Valorisation (MAD)",
            {
                "fields": ["prix_achat_ht", "prix_vente_ht", "stock_alerte", "stock_actuel"],
            },
        ),
        (
            "Métadonnées",
            {
                "fields": ["created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]

    @admin.display(description="Stock actuel")
    def stock_actuel_badge(self, obj):
        if obj.en_rupture:
            color = "#dc2626"
            label = f"{obj.stock_actuel} (Rupture)"
        elif obj.sous_alerte:
            color = "#d97706"
            label = f"{obj.stock_actuel} (Alerte)"
        else:
            color = "#16a34a"
            label = f"{obj.stock_actuel}"

        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 4px; font-weight: bold; font-size: 0.85rem;">{}</span>',
            color,
            label,
        )

    @admin.display(description="Valeur Stock (MAD)")
    def valeur_stock_display(self, obj):
        return f"{obj.valeur_stock_ht:,.2f} MAD"


@admin.register(MouvementStock)
class MouvementStockAdmin(admin.ModelAdmin):
    list_display = [
        "date_mouvement",
        "article",
        "type_badge",
        "quantite",
        "prix_unitaire_ht",
        "stock_avant",
        "stock_apres",
        "reference_document",
        "fournisseur",
        "auteur",
    ]
    list_filter = ["type_mouvement", "date_mouvement"]
    search_fields = ["article__reference", "article__designation", "reference_document", "motif"]
    date_hierarchy = "date_mouvement"

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Type")
    def type_badge(self, obj):
        colors = {
            MouvementStock.TypeMouvement.ENTREE: "#2563eb",
            MouvementStock.TypeMouvement.SORTIE: "#e11d48",
            MouvementStock.TypeMouvement.AJUSTEMENT: "#4b5563",
        }
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; '
            'border-radius: 3px; font-size: 0.8rem; font-weight: 500;">{}</span>',
            colors.get(obj.type_mouvement, "#000"),
            obj.get_type_mouvement_display(),
        )

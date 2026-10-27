import uuid

from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError

from .models import (
    CompanySettings,
    Expense,
    ExpenseHistory,
    ExpensePayment,
    InvoiceNumberingSettings,
    Payment,
    PaymentAllocation,
    QuoteNumberingSettings,
)


class ExpensePaymentAdminForm(forms.ModelForm):
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    class Meta:
        model = ExpensePayment
        fields = [
            "expense",
            "payment_date",
            "amount",
            "method",
            "reference",
            "notes",
            "idempotency_key",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("idempotency_key", uuid.uuid4())

    def clean_idempotency_key(self):
        key = self.cleaned_data["idempotency_key"]
        if ExpensePayment.objects.filter(idempotency_key=key).exists():
            raise ValidationError("Ce paiement a déjà été enregistré.")
        return key


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = [
        "description",
        "category",
        "supplier",
        "expense_date",
        "amount",
        "amount_paid",
        "remaining_balance",
        "payment_status",
    ]
    list_filter = ["category", "expense_date"]
    search_fields = ["description", "supplier__name"]
    date_hierarchy = "expense_date"
    readonly_fields = ["amount_paid", "remaining_balance", "payment_status"]

    def save_model(self, request, obj, form, change):
        obj.audit_actor = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExpensePayment)
class ExpensePaymentAdmin(admin.ModelAdmin):
    form = ExpensePaymentAdminForm
    list_display = ["expense", "payment_date", "amount", "method", "reference"]
    list_filter = ["method", "payment_date"]
    search_fields = ["expense__description", "reference", "expense__supplier__name"]
    date_hierarchy = "payment_date"
    readonly_fields = ["created_at"]

    def save_model(self, request, obj, form, change):
        obj.audit_actor = request.user
        obj.idempotency_key = form.cleaned_data["idempotency_key"]
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExpenseHistory)
class ExpenseHistoryAdmin(admin.ModelAdmin):
    list_display = ["expense", "event", "actor", "created_at"]
    list_filter = ["event", "created_at"]
    search_fields = ["expense__description", "actor__username"]
    readonly_fields = ["expense", "event", "actor", "snapshot", "created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuoteNumberingSettings)
class QuoteNumberingSettingsAdmin(admin.ModelAdmin):
    list_display = ["prefix", "separator", "include_year", "sequence_width"]
    fields = ["prefix", "separator", "include_year", "sequence_width"]

    def has_add_permission(self, request):
        return not QuoteNumberingSettings.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InvoiceNumberingSettings)
class InvoiceNumberingSettingsAdmin(admin.ModelAdmin):
    list_display = ["prefix", "separator", "include_year", "sequence_width"]
    fields = ["prefix", "separator", "include_year", "sequence_width"]

    def has_add_permission(self, request):
        return not InvoiceNumberingSettings.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False


class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    fields = ["invoice", "amount", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        "customer",
        "payment_date",
        "amount",
        "allocated_amount",
        "unallocated_amount",
        "method",
        "reference",
    ]
    list_filter = ["method", "payment_date"]
    search_fields = ["customer__name", "reference", "notes"]
    date_hierarchy = "payment_date"
    readonly_fields = ["created_at", "updated_at", "allocated_amount", "unallocated_amount"]
    inlines = [PaymentAllocationInline]


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(admin.ModelAdmin):
    list_display = ["payment", "invoice", "amount", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["payment__customer__name", "invoice__number", "payment__reference"]
    readonly_fields = ["created_at"]


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ["name", "legal_form", "city", "ice", "tax_identifier", "phone", "email"]
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

    def has_add_permission(self, request):
        return not CompanySettings.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False

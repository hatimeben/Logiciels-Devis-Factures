from django.contrib import admin

from .models import ExpenseCategory, Service, ServiceCategory, TaxRate


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active", "updated_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]


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

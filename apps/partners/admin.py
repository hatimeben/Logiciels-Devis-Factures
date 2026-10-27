from django.contrib import admin

from .models import Customer, Supplier


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "phone", "is_active"]
    list_filter = ["is_active", "country"]
    search_fields = ["name", "email", "tax_identifier"]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "phone", "is_active"]
    list_filter = ["is_active", "country"]
    search_fields = ["name", "email", "tax_identifier"]

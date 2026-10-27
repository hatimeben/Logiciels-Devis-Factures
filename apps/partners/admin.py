from django.contrib import admin

from .models import Customer, Supplier


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "ice", "tax_identifier", "city", "phone", "is_active"]
    list_filter = ["is_active", "country", "city"]
    search_fields = ["name", "ice", "tax_identifier", "email", "phone"]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "ice", "tax_identifier", "city", "phone", "is_active"]
    list_filter = ["is_active", "country", "city"]
    search_fields = ["name", "ice", "tax_identifier", "email", "phone"]

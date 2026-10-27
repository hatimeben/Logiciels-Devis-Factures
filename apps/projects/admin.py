from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "customer", "status", "starts_on", "ends_on"]
    list_filter = ["status"]
    search_fields = ["name", "customer__name"]

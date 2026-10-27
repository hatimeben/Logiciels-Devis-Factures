from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Initialise les groupes et rôles standards de l'application (Commercial / Utilisateur Normal, Comptable)"

    def handle(self, *args, **options):
        # 1. Groupe : Commercial / Utilisateur Normal
        commercial_group, created = Group.objects.get_or_create(name="Commercial / Utilisateur Normal")
        commercial_permissions = [
            # Devis
            "view_quote", "add_quote", "change_quote",
            "view_quoteline", "add_quoteline", "change_quoteline",
            # Factures
            "view_invoice", "add_invoice", "change_invoice",
            "view_invoiceline", "add_invoiceline", "change_invoiceline",
            # Encaissements & règlements clients
            "view_payment", "add_payment",
            "view_paymentallocation", "add_paymentallocation",
            # Clients
            "view_customer", "add_customer", "change_customer",
            # Catalogue & Taxes
            "view_service", "view_servicecategory", "view_taxrate",
        ]
        perms_to_add = Permission.objects.filter(codename__in=commercial_permissions)
        commercial_group.permissions.set(perms_to_add)
        self.stdout.write(
            self.style.SUCCESS(
                f"Groupe '{commercial_group.name}' configuré avec {perms_to_add.count()} permissions."
            )
        )

        # 2. Groupe : Comptable / Gestionnaire Financier
        comptable_group, created = Group.objects.get_or_create(name="Comptable / Gestionnaire Financier")
        comptable_permissions = [
            # Consultation devis
            "view_quote", "view_quoteline",
            # Factures
            "view_invoice", "view_invoiceline",
            # Encaissements
            "view_payment", "add_payment", "view_paymentallocation", "add_paymentallocation",
            # Dépenses fournisseurs
            "view_expense", "add_expense", "change_expense",
            "view_expensepayment", "add_expensepayment",
            # Clients & Fournisseurs
            "view_customer", "view_supplier", "add_supplier", "change_supplier",
            # Catalogue
            "view_service", "view_taxrate", "view_expensecategory",
        ]
        comptable_perms = Permission.objects.filter(codename__in=comptable_permissions)
        comptable_group.permissions.set(comptable_perms)
        self.stdout.write(
            self.style.SUCCESS(
                f"Groupe '{comptable_group.name}' configuré avec {comptable_perms.count()} permissions."
            )
        )


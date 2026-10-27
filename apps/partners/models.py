from django.db import models


class Contact(models.Model):
    name = models.CharField("nom ou raison sociale", max_length=200)
    email = models.EmailField("adresse e-mail", blank=True)
    phone = models.CharField("téléphone", max_length=40, blank=True)
    address = models.TextField("adresse", blank=True)
    city = models.CharField("ville", max_length=100, blank=True)
    country = models.CharField("pays", max_length=100, default="Maroc")
    tax_identifier = models.CharField("identifiant fiscal", max_length=100, blank=True)
    notes = models.TextField("notes", blank=True)
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name


class Customer(Contact):
    class Meta(Contact.Meta):
        verbose_name = "client"
        verbose_name_plural = "clients"


class Supplier(Contact):
    class Meta(Contact.Meta):
        verbose_name = "fournisseur"
        verbose_name_plural = "fournisseurs"

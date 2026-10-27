from django.db import models


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planifié"
        IN_PROGRESS = "in_progress", "En cours"
        COMPLETED = "completed", "Terminé"
        CANCELLED = "cancelled", "Annulé"

    customer = models.ForeignKey(
        "partners.Customer",
        on_delete=models.PROTECT,
        related_name="projects",
        verbose_name="client",
        null=True,
        blank=True,
    )
    name = models.CharField("nom", max_length=200)
    description = models.TextField("description", blank=True)
    status = models.CharField(
        "statut",
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    starts_on = models.DateField("date de début", null=True, blank=True)
    ends_on = models.DateField("date de fin", null=True, blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "projet"
        verbose_name_plural = "projets"

    def __str__(self):
        return self.name

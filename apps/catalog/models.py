from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class ExpenseCategory(models.Model):
    name = models.CharField("nom", max_length=100, unique=True)
    description = models.TextField("description", blank=True)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)
    pcge_account = models.CharField(
        "compte PCGE",
        max_length=10,
        default="6111",
        blank=True,
        help_text="Compte de charges du Plan Comptable Général des Entreprises (ex: 6111, 61251, 6144, 6583...).",
    )
    is_tax_deductible_default = models.BooleanField(
        "déductible par défaut à l'IS",
        default=True,
        blank=True,
        help_text="Décocher si les charges de cette catégorie sont systématiquement non déductibles fiscalement.",
    )
    default_non_deductible_reason = models.CharField(
        "motif de réintégration par défaut",
        max_length=30,
        blank=True,
        default="",
        help_text="Motif légal CGI attribué par défaut (ex: fines_penalties, client_gifts...).",
    )
    vat_rate = models.DecimalField(
        "taux de TVA (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("20.00"),
        blank=True,
        help_text="Taux de TVA standard (ex: 20%, 10%, 14%, 0%).",
    )
    is_vat_recoverable = models.BooleanField(
        "TVA récupérable",
        default=True,
        blank=True,
        help_text="Décocher si la TVA est fiscalement exclue du droit à déduction (ex: carburant Art. 106-I-2° CGI, amendes).",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "catégorie de dépense"
        verbose_name_plural = "catégories de dépenses"

    def __str__(self):
        return self.name


class ServiceCategory(models.Model):
    name = models.CharField("nom", max_length=100, unique=True)
    description = models.TextField("description", blank=True)
    is_active = models.BooleanField("active", default=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "catégorie de service"
        verbose_name_plural = "catégories de services"

    def __str__(self):
        return self.name


class TaxRate(models.Model):
    name = models.CharField("libellé", max_length=100, unique=True)
    percentage = models.DecimalField(
        "pourcentage",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "taux de taxe"
        verbose_name_plural = "taux de taxe"
        constraints = [
            models.CheckConstraint(
                condition=Q(percentage__gte=0, percentage__lte=100),
                name="tax_rate_percentage_between_zero_and_hundred",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.percentage} %)"


class Service(models.Model):
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.PROTECT,
        related_name="services",
        verbose_name="catégorie",
    )
    name = models.CharField("nom", max_length=200)
    description = models.TextField("description", blank=True)
    default_unit_price = models.DecimalField(
        "prix de vente HT par défaut (MAD)",
        max_digits=12,
        decimal_places=2,
        null=True,
    )
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "service"
        verbose_name_plural = "services"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "name"],
                name="unique_service_name_per_category",
            ),
        ]

    def clean(self):
        super().clean()
        if self.default_unit_price is None:
            raise ValidationError(
                {"default_unit_price": "Un prix de vente HT par défaut doit être renseigné."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Article(models.Model):
    class NatureProduit(models.TextChoices):
        MARCHANDISE = "MARCHANDISE", "Marchandise physique stockable (Compte 3111)"
        FOURNITURE = "FOURNITURE", "Matière ou Fourniture consommable (Compte 3121)"

    reference = models.CharField("référence", max_length=50, unique=True)
    designation = models.CharField("désignation", max_length=255)
    description = models.TextField("description détaillée", blank=True)
    nature_produit = models.CharField(
        "nature du produit",
        max_length=20,
        choices=NatureProduit.choices,
        default=NatureProduit.MARCHANDISE,
    )
    
    # Comptes comptables (Plan Comptable Général des Entreprises - PCGE Maroc)
    compte_stock = models.CharField(
        "compte de stock (PCGE)",
        max_length=10,
        default="3111",
        help_text="Classe 3 - Actif circulant (3111 Marchandises)",
    )
    compte_variation = models.CharField(
        "compte variation stock (PCGE)",
        max_length=10,
        default="6114",
        help_text="Classe 6 - Charges d'exploitation (6114 Variation des stocks)",
    )
    compte_achat = models.CharField(
        "compte d'achat (PCGE)",
        max_length=10,
        default="6111",
        help_text="Classe 6 - Achats revendus de marchandises (6111)",
    )
    compte_vente = models.CharField(
        "compte de vente (PCGE)",
        max_length=10,
        default="7111",
        help_text="Classe 7 - Ventes de marchandises au Maroc (7111)",
    )

    prix_vente_ht = models.DecimalField(
        "prix de vente HT (MAD)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    prix_achat_ht = models.DecimalField(
        "prix d'achat HT (MAD)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Coût d'achat de référence (PUMP) pour la valorisation du stock.",
    )
    stock_actuel = models.DecimalField(
        "stock actuel",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    stock_alerte = models.DecimalField(
        "seuil d'alerte",
        max_digits=12,
        decimal_places=2,
        default=Decimal("5.00"),
        help_text="Déclenche une alerte si le stock atteint ou passe sous ce niveau.",
    )
    is_active = models.BooleanField("actif", default=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["reference"]
        verbose_name = "article"
        verbose_name_plural = "articles"

    def __str__(self):
        return f"{self.reference} - {self.designation}"

    @property
    def en_rupture(self) -> bool:
        return self.stock_actuel <= Decimal("0.00")

    @property
    def sous_alerte(self) -> bool:
        return self.stock_actuel <= self.stock_alerte and not self.en_rupture

    @property
    def valeur_stock_ht(self) -> Decimal:
        """Valorisation du stock physique au coût d'achat HT (MAD)."""
        if self.stock_actuel > Decimal("0.00") and self.prix_achat_ht > Decimal("0.00"):
            return (self.stock_actuel * self.prix_achat_ht).quantize(Decimal("0.01"))
        return Decimal("0.00")

    @property
    def marge_unitaire_ht(self) -> Decimal:
        """Marge brute unitaire HT en MAD."""
        return (self.prix_vente_ht - self.prix_achat_ht).quantize(Decimal("0.01"))

    @property
    def taux_marge(self) -> Decimal:
        """Taux de marge en pourcentage."""
        if self.prix_achat_ht and self.prix_achat_ht > Decimal("0.00"):
            return ((self.prix_vente_ht - self.prix_achat_ht) / self.prix_achat_ht * 100).quantize(Decimal("0.1"))
        return Decimal("0.0")


class MouvementStock(models.Model):
    class TypeMouvement(models.TextChoices):
        ENTREE = "ENTREE", "Entrée (Achat / Réception / Retour)"
        SORTIE = "SORTIE", "Sortie (Vente / Facture)"
        AJUSTEMENT = "AJUSTEMENT", "Ajustement (Inventaire)"

    article = models.ForeignKey(
        Article,
        on_delete=models.PROTECT,
        related_name="mouvements",
        verbose_name="article",
    )
    type_mouvement = models.CharField(
        "type de mouvement",
        max_length=15,
        choices=TypeMouvement.choices,
    )
    quantite = models.DecimalField(
        "quantité",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    stock_avant = models.DecimalField(
        "stock avant",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    stock_apres = models.DecimalField(
        "stock après",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    reference_document = models.CharField(
        "document source",
        max_length=100,
        blank=True,
        null=True,
        help_text="Ex: N° Facture (FAC-2026-0001), Bon de réception fournisseur, ou Inventaire",
    )
    fournisseur = models.ForeignKey(
        "partners.Supplier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_stock",
        verbose_name="fournisseur lié",
    )
    prix_unitaire_ht = models.DecimalField(
        "prix unitaire constaté HT (MAD)",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Prix unitaire d'achat ou de revient constaté lors du mouvement.",
    )
    motif = models.CharField("motif / commentaire", max_length=255, blank=True)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="initié par",
    )
    date_mouvement = models.DateTimeField("date du mouvement", auto_now_add=True)

    class Meta:
        ordering = ["-date_mouvement", "-pk"]
        verbose_name = "mouvement de stock"
        verbose_name_plural = "mouvements de stock"

    def __str__(self):
        return f"{self.get_type_mouvement_display()} - {self.article.reference} ({self.quantite})"

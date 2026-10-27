from decimal import Decimal
import uuid
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db import transaction
from django.db.models import Q, Sum

from .money import round_money
from .quote_calculations import calculate_quote_totals


class Expense(models.Model):
    category = models.ForeignKey(
        "catalog.ExpenseCategory",
        on_delete=models.PROTECT,
        related_name="expenses",
        verbose_name="catégorie",
    )
    supplier = models.ForeignKey(
        "partners.Supplier",
        on_delete=models.PROTECT,
        related_name="expenses",
        verbose_name="fournisseur",
        null=True,
        blank=True,
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        related_name="expenses",
        verbose_name="projet",
        null=True,
        blank=True,
    )
    description = models.CharField("description", max_length=250)
    expense_date = models.DateField("date de dépense")
    amount = models.DecimalField(
        "montant (MAD)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    receipt = models.FileField(
        "justificatif",
        upload_to="justificatifs/depenses/%Y/%m/",
        blank=True,
    )
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        ordering = ["-expense_date", "-id"]
        verbose_name = "dépense"
        verbose_name_plural = "dépenses"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive"),
        ]

    def clean(self):
        super().clean()
        if self.pk and self.amount is not None:
            paid = self.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            if paid > self.amount:
                raise ValidationError(
                    {"amount": "Le montant ne peut pas être inférieur aux paiements déjà enregistrés."}
                )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            previous = (
                type(self).objects.filter(pk=self.pk).first()
                if self.pk
                else None
            )
            self.full_clean()
            result = super().save(*args, **kwargs)
            ExpenseHistory.objects.create(
                expense=self,
                event=(
                    ExpenseHistory.Event.UPDATED
                    if previous
                    else ExpenseHistory.Event.CREATED
                ),
                actor=getattr(self, "audit_actor", None),
                snapshot={
                    "before": self._audit_snapshot(previous) if previous else None,
                    "after": self._audit_snapshot(self),
                    "actor_username": self._audit_actor_name(),
                },
            )
            return result

    @staticmethod
    def _audit_snapshot(expense):
        return {
            "category_id": expense.category_id,
            "supplier_id": expense.supplier_id,
            "project_id": expense.project_id,
            "description": expense.description,
            "expense_date": expense.expense_date.isoformat(),
            "amount": str(expense.amount),
            "receipt": expense.receipt.name if expense.receipt else "",
        }

    def _audit_actor_name(self):
        actor = getattr(self, "audit_actor", None)
        return actor.get_username() if actor else ""

    @property
    def amount_paid(self):
        return round_money(
            self.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        )

    @property
    def remaining_balance(self):
        return round_money(self.amount - self.amount_paid)

    @property
    def payment_status(self):
        if self.remaining_balance == 0:
            return "Payée"
        if self.amount_paid > 0:
            return "Partiellement payée"
        return "À payer"

    def __str__(self):
        return self.description


class ExpensePayment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Espèces"
        BANK_TRANSFER = "bank_transfer", "Virement bancaire"
        CARD = "card", "Carte"
        CHEQUE = "cheque", "Chèque"
        OTHER = "other", "Autre"

    expense = models.ForeignKey(
        Expense,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="dépense",
    )
    idempotency_key = models.UUIDField(
        "clé anti-doublon",
        default=uuid.uuid4,
        unique=True,
        editable=True,
    )
    payment_date = models.DateField("date du paiement")
    amount = models.DecimalField(
        "montant (MAD)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    method = models.CharField("mode de paiement", max_length=20, choices=Method.choices)
    reference = models.CharField("référence", max_length=100, blank=True)
    notes = models.TextField("notes", blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        verbose_name = "paiement de dépense"
        verbose_name_plural = "paiements de dépenses"
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="expense_payment_amount_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.expense_id or self.amount is None:
            return
        total = self.expense.payments.exclude(pk=self.pk).aggregate(total=Sum("amount"))["total"]
        total = total or Decimal("0.00")
        if total + self.amount > self.expense.amount:
            raise ValidationError(
                {"amount": "Le paiement dépasse le solde restant de cette dépense."}
            )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                raise ValidationError(
                    "Un paiement enregistré est immuable. Les corrections et remboursements "
                    "ne sont pas encore disponibles."
                )
            Expense.objects.select_for_update().get(pk=self.expense_id)
            self.full_clean()
            result = super().save(*args, **kwargs)
            actor = getattr(self, "audit_actor", None)
            ExpenseHistory.objects.create(
                expense=self.expense,
                event=ExpenseHistory.Event.PAYMENT_RECORDED,
                actor=actor,
                snapshot={
                    "payment_id": self.pk,
                    "amount": str(self.amount),
                    "payment_date": self.payment_date.isoformat(),
                    "method": self.method,
                    "reference": self.reference,
                    "notes": self.notes,
                    "actor_username": actor.get_username() if actor else "",
                },
            )
            return result

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Un paiement enregistré ne peut pas être supprimé. "
            "Les corrections et remboursements ne sont pas encore disponibles."
        )

    def __str__(self):
        return f"{self.amount} MAD - {self.expense}"


class ExpenseHistory(models.Model):
    class Event(models.TextChoices):
        CREATED = "created", "Création"
        UPDATED = "updated", "Modification"
        PAYMENT_RECORDED = "payment_recorded", "Paiement enregistré"

    expense = models.ForeignKey(
        Expense,
        on_delete=models.PROTECT,
        related_name="history",
        verbose_name="dépense",
    )
    event = models.CharField("événement", max_length=30, choices=Event.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="expense_history",
        verbose_name="utilisateur",
        null=True,
        blank=True,
    )
    snapshot = models.JSONField("instantané", default=dict)
    created_at = models.DateTimeField("date de l'événement", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "historique de dépense"
        verbose_name_plural = "historiques de dépenses"

    def __str__(self):
        return f"{self.get_event_display()} - {self.expense}"


class Quote(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SENT = "sent", "Envoyé"
        ACCEPTED = "accepted", "Accepté"
        REJECTED = "rejected", "Refusé"
        EXPIRED = "expired", "Expiré"
        CONVERTED = "converted", "Converti en facture"
        CANCELLED = "cancelled", "Annulé"

    number = models.CharField("numéro", max_length=50, unique=True, null=True, blank=True)
    revision_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="revisions",
        verbose_name="version précédente",
        null=True,
        blank=True,
    )
    customer = models.ForeignKey(
        "partners.Customer",
        on_delete=models.PROTECT,
        related_name="quotes",
        verbose_name="client",
    )
    customer_snapshot = models.JSONField("coordonnées client historisées", default=dict, blank=True)
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        related_name="quotes",
        verbose_name="projet",
        null=True,
        blank=True,
    )
    status = models.CharField(
        "statut",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    issue_date = models.DateField("date d'émission", null=True, blank=True)
    valid_until = models.DateField("valable jusqu'au", null=True, blank=True)
    notes = models.TextField("notes", blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "devis"
        verbose_name_plural = "devis"

    def save(self, *args, **kwargs):
        if self.pk:
            current_status = type(self).objects.filter(pk=self.pk).values_list(
                "status", flat=True
            ).first()
            if current_status != self.Status.DRAFT:
                raise ValidationError(
                    "Un devis envoyé ne peut pas être modifié. Créez une nouvelle version."
                )
            if self.status != current_status:
                raise ValidationError(
                    "Le statut d'un devis doit être modifié par une transition autorisée."
                )
        elif self.status != self.Status.DRAFT or self.number:
            raise ValidationError("Un nouveau devis doit commencer en brouillon sans numéro.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and self.status != self.Status.DRAFT:
            raise ValidationError("Un devis envoyé ne peut pas être supprimé.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.number or f"Devis brouillon #{self.pk or 'nouveau'}"


class QuoteLine(models.Model):
    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage", "Pourcentage"
        FIXED = "fixed", "Montant fixe (MAD HT)"

    quote = models.ForeignKey(
        Quote,
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="devis",
    )
    service = models.ForeignKey(
        "catalog.Service",
        on_delete=models.SET_NULL,
        related_name="quote_lines",
        verbose_name="service du catalogue",
        null=True,
        blank=True,
    )
    description = models.CharField("description", max_length=250)
    quantity = models.DecimalField(
        "quantité",
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price = models.DecimalField(
        "prix unitaire (MAD)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    discount_type = models.CharField(
        "type de remise",
        max_length=12,
        choices=DiscountType.choices,
        default=DiscountType.PERCENTAGE,
    )
    discount_value = models.DecimalField(
        "remise",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    tax_rate = models.ForeignKey(
        "catalog.TaxRate",
        on_delete=models.PROTECT,
        related_name="quote_lines",
        verbose_name="taux appliqué",
        null=True,
        blank=True,
    )
    tax_percentage = models.DecimalField(
        "pourcentage de taxe historisé",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    tax_name = models.CharField("libellé fiscal historisé", max_length=100, blank=True)
    position = models.PositiveIntegerField("ordre", default=0)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "ligne de devis"
        verbose_name_plural = "lignes de devis"
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="quote_line_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="quote_line_unit_price_nonnegative"),
            models.CheckConstraint(
                condition=Q(discount_value__gte=0),
                name="quote_line_discount_nonnegative",
            ),
            models.CheckConstraint(
                condition=~Q(discount_type="percentage") | Q(discount_value__lte=100),
                name="quote_line_percentage_discount_valid",
            ),
            models.CheckConstraint(
                condition=Q(tax_percentage__isnull=True)
                | Q(tax_percentage__gte=0, tax_percentage__lte=100),
                name="quote_line_tax_percentage_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.quote_id and self.quote.status != Quote.Status.DRAFT:
            raise ValidationError("Les lignes d'un devis envoyé sont immuables.")
        if self.discount_type == self.DiscountType.PERCENTAGE and self.discount_value > 100:
            raise ValidationError({"discount_value": "La remise ne peut pas dépasser 100 %."})
        if (
            self.discount_type == self.DiscountType.FIXED
            and self.quantity is not None
            and self.unit_price is not None
        ):
            gross = round_money(self.quantity * self.unit_price)
            if self.discount_value > gross:
                raise ValidationError(
                    {"discount_value": "La remise ne peut pas dépasser le montant HT de la ligne."}
                )

    def save(self, *args, **kwargs):
        if self.pk:
            current = type(self).objects.select_related("quote").filter(pk=self.pk).first()
            if current and current.quote.status != Quote.Status.DRAFT:
                raise ValidationError("Les lignes d'un devis envoyé sont immuables.")
        self.full_clean()
        if self.tax_rate_id:
            if self.tax_percentage is None:
                self.tax_percentage = self.tax_rate.percentage
                self.tax_name = self.tax_rate.name
        else:
            self.tax_percentage = Decimal("0.00")
            self.tax_name = "Sans taxe"
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.quote.status != Quote.Status.DRAFT:
            raise ValidationError("Les lignes d'un devis envoyé sont immuables.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.description


class QuoteNumberingSettings(models.Model):
    prefix = models.CharField("préfixe", max_length=12, default="DEV")
    separator = models.CharField("séparateur", max_length=3, default="-")
    include_year = models.BooleanField("inclure l'année", default=True)
    sequence_width = models.PositiveSmallIntegerField("chiffres de séquence", default=4)

    class Meta:
        verbose_name = "paramètres de numérotation des devis"
        verbose_name_plural = "paramètres de numérotation des devis"

    def clean(self):
        super().clean()
        if self.pk not in (None, 1):
            raise ValidationError("Un seul paramètre de numérotation des devis est autorisé.")
        if not self.prefix.strip():
            raise ValidationError({"prefix": "Le préfixe ne peut pas être vide."})
        if not 1 <= self.sequence_width <= 12:
            raise ValidationError({"sequence_width": "Choisissez entre 1 et 12 chiffres."})
        if not re.fullmatch(r"[A-Z0-9]{1,12}", self.prefix.strip().upper()):
            raise ValidationError({"prefix": "Utilisez de 1 à 12 lettres ou chiffres."})
        if not re.fullmatch(r"[-_/]{1,3}", self.separator):
            raise ValidationError({"separator": "Le séparateur doit être -, _ ou /."})

    def save(self, *args, **kwargs):
        self.pk = 1
        self.prefix = self.prefix.strip().upper()
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        try:
            return cls.objects.get(pk=1)
        except cls.DoesNotExist:
            try:
                obj, _ = cls.objects.get_or_create(
                    pk=1,
                    defaults={
                        "prefix": "DEV",
                        "separator": "-",
                        "include_year": True,
                        "sequence_width": 4,
                    },
                )
                return obj
            except ValidationError:
                return cls.objects.get(pk=1)

    def __str__(self):
        year = self.separator + "AAAA" if self.include_year else ""
        return f"{self.prefix}{year}{self.separator}{{séquence}}"


class QuoteNumberCounter(models.Model):
    year = models.PositiveSmallIntegerField("année", primary_key=True)
    last_number = models.PositiveIntegerField("dernier numéro attribué", default=0)

    class Meta:
        ordering = ["year"]
        verbose_name = "compteur annuel de devis"
        verbose_name_plural = "compteurs annuels de devis"
        constraints = [
            models.CheckConstraint(
                condition=Q(last_number__gte=0),
                name="quote_counter_last_number_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.year} : {self.last_number}"


class InvoiceNumberingSettings(models.Model):
    prefix = models.CharField("préfixe", max_length=12, default="FAC")
    separator = models.CharField("séparateur", max_length=3, default="-")
    include_year = models.BooleanField("inclure l'année", default=True)
    sequence_width = models.PositiveSmallIntegerField("chiffres de séquence", default=4)

    class Meta:
        verbose_name = "paramètres de numérotation des factures"
        verbose_name_plural = "paramètres de numérotation des factures"

    def clean(self):
        super().clean()
        if self.pk not in (None, 1):
            raise ValidationError("Un seul paramètre de numérotation des factures est autorisé.")
        if not self.prefix.strip():
            raise ValidationError({"prefix": "Le préfixe ne peut pas être vide."})
        if not 1 <= self.sequence_width <= 12:
            raise ValidationError({"sequence_width": "Choisissez entre 1 et 12 chiffres."})
        if not re.fullmatch(r"[A-Z0-9]{1,12}", self.prefix.strip().upper()):
            raise ValidationError({"prefix": "Utilisez de 1 à 12 lettres ou chiffres."})
        if not re.fullmatch(r"[-_/]{1,3}", self.separator):
            raise ValidationError({"separator": "Le séparateur doit être -, _ ou /."})

    def save(self, *args, **kwargs):
        self.pk = 1
        self.prefix = self.prefix.strip().upper()
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        try:
            return cls.objects.get(pk=1)
        except cls.DoesNotExist:
            try:
                obj, _ = cls.objects.get_or_create(
                    pk=1,
                    defaults={
                        "prefix": "FAC",
                        "separator": "-",
                        "include_year": True,
                        "sequence_width": 4,
                    },
                )
                return obj
            except ValidationError:
                return cls.objects.get(pk=1)

    def __str__(self):
        year = self.separator + "AAAA" if self.include_year else ""
        return f"{self.prefix}{year}{self.separator}{{séquence}}"


class InvoiceNumberCounter(models.Model):
    year = models.PositiveSmallIntegerField("année", primary_key=True)
    last_number = models.PositiveIntegerField("dernier numéro attribué", default=0)

    class Meta:
        ordering = ["year"]
        verbose_name = "compteur annuel de factures"
        verbose_name_plural = "compteurs annuels de factures"
        constraints = [
            models.CheckConstraint(
                condition=Q(last_number__gte=0),
                name="invoice_counter_last_number_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.year} : {self.last_number}"


class CompanySettings(models.Model):
    name = models.CharField("raison sociale", max_length=200, default="BENCHTECH")
    tagline = models.CharField(
        "activité ou slogan",
        max_length=250,
        default="Gestion de prestations informatiques et de formation",
        blank=True,
    )
    legal_form = models.CharField("forme juridique", max_length=50, default="SARL", blank=True)
    capital = models.DecimalField(
        "capital social (MAD)",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    address = models.TextField("adresse", blank=True)
    city = models.CharField("ville", max_length=100, default="Casablanca", blank=True)
    postal_code = models.CharField("code postal", max_length=20, blank=True)
    country = models.CharField("pays", max_length=100, default="Maroc", blank=True)
    phone = models.CharField("téléphone", max_length=50, blank=True)
    email = models.EmailField("adresse e-mail", blank=True)
    website = models.CharField("site web", max_length=150, blank=True)

    # Identifiants fiscaux & légaux marocains
    ice = models.CharField("ICE (15 chiffres)", max_length=20, blank=True)
    tax_identifier = models.CharField("identifiant fiscal (IF)", max_length=50, blank=True)
    trade_registry = models.CharField("registre du commerce (RC)", max_length=100, blank=True)
    patente = models.CharField("patente / taxe professionnelle", max_length=50, blank=True)
    cnss = models.CharField("n° CNSS", max_length=50, blank=True)

    # Coordonnées bancaires
    bank_name = models.CharField("banque", max_length=100, blank=True)
    rib = models.CharField("RIB (24 chiffres)", max_length=35, blank=True)

    # Logo
    logo = models.ImageField("logo de l'entreprise", upload_to="company/", blank=True, null=True)

    class Meta:
        verbose_name = "paramètres de l'entreprise"
        verbose_name_plural = "paramètres de l'entreprise"

    def clean(self):
        super().clean()
        if self.pk not in (None, 1):
            raise ValidationError("Une seule fiche d'entreprise est autorisée.")
        if self.ice:
            cleaned_ice = re.sub(r"\s+", "", self.ice)
            if not (cleaned_ice.isdigit() and len(cleaned_ice) == 15):
                raise ValidationError({"ice": "L'ICE marocain doit comporter exactement 15 chiffres."})
            self.ice = cleaned_ice
        if self.rib:
            cleaned_rib = re.sub(r"\s+", "", self.rib)
            if not (cleaned_rib.isdigit() and len(cleaned_rib) == 24):
                raise ValidationError({"rib": "Le RIB marocain doit comporter exactement 24 chiffres."})
            self.rib = cleaned_rib

    def save(self, *args, **kwargs):
        self.pk = 1
        self.full_clean()
        return super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        try:
            return cls.objects.get(pk=1)
        except cls.DoesNotExist:
            try:
                obj, _ = cls.objects.get_or_create(
                    pk=1,
                    defaults={
                        "name": "BENCHTECH",
                        "tagline": "Gestion de prestations informatiques et de formation",
                        "legal_form": "SARL",
                        "city": "Casablanca",
                    },
                )
                return obj
            except ValidationError:
                return cls.objects.get(pk=1)

    def legal_notice(self):
        parts = [self.name]
        if self.legal_form:
            form = self.legal_form
            if self.capital:
                cap = f"{self.capital:,.2f}".replace(",", " ").replace(".", ",")
                form += f" au capital de {cap} MAD"
            parts.append(form)
        if self.ice:
            parts.append(f"ICE : {self.ice}")
        if self.tax_identifier:
            parts.append(f"IF : {self.tax_identifier}")
        if self.trade_registry:
            parts.append(f"RC : {self.trade_registry}")
        if self.patente:
            parts.append(f"Patente : {self.patente}")
        if self.cnss:
            parts.append(f"CNSS : {self.cnss}")
        return " · ".join(parts)

    def __str__(self):
        return self.name


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        ISSUED = "issued", "Émise"
        PARTIALLY_PAID = "partially_paid", "Partiellement payée"
        PAID = "paid", "Payée"
        OVERDUE = "overdue", "En retard"
        CANCELLED = "cancelled", "Annulée"

    number = models.CharField("numéro", max_length=50, unique=True, null=True, blank=True)
    quote = models.OneToOneField(
        Quote,
        on_delete=models.PROTECT,
        related_name="invoice",
        verbose_name="devis d'origine",
        null=True,
        blank=True,
    )
    customer = models.ForeignKey(
        "partners.Customer",
        on_delete=models.PROTECT,
        related_name="invoices",
        verbose_name="client",
    )
    customer_snapshot = models.JSONField(
        "coordonnées client historisées",
        default=dict,
        blank=True,
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        related_name="invoices",
        verbose_name="projet",
        null=True,
        blank=True,
    )
    status = models.CharField(
        "statut",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    issue_date = models.DateField("date d'émission", null=True, blank=True)
    due_date = models.DateField("date d'échéance", null=True, blank=True)
    notes = models.TextField("notes", blank=True)
    created_at = models.DateTimeField("créée le", auto_now_add=True)
    updated_at = models.DateTimeField("modifiée le", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "facture"
        verbose_name_plural = "factures"

    def save(self, *args, **kwargs):
        if self.pk:
            current_status = type(self).objects.filter(pk=self.pk).values_list(
                "status", flat=True
            ).first()
            if current_status != self.Status.DRAFT:
                raise ValidationError("Une facture émise ne peut pas être modifiée.")
            if self.status != current_status:
                raise ValidationError(
                    "Le statut d'une facture doit être modifié par une transition autorisée."
                )
        elif self.status != self.Status.DRAFT or self.number:
            raise ValidationError("Une nouvelle facture doit commencer en brouillon sans numéro.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and self.status != self.Status.DRAFT:
            raise ValidationError("Une facture émise ne peut pas être supprimée.")
        return super().delete(*args, **kwargs)

    @property
    def total_ttc(self):
        return calculate_quote_totals(self.lines.all())["total"]

    @property
    def total_paid(self):
        total = self.payment_allocations.aggregate(total=Sum("amount"))["total"]
        return round_money(total or Decimal("0.00"))

    @property
    def remaining_balance(self):
        return round_money(self.total_ttc - self.total_paid)

    def __str__(self):
        return self.number or f"Facture brouillon #{self.pk or 'nouvelle'}"


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="facture",
    )
    service = models.ForeignKey(
        "catalog.Service",
        on_delete=models.SET_NULL,
        related_name="invoice_lines",
        verbose_name="service du catalogue",
        null=True,
        blank=True,
    )
    description = models.CharField("description", max_length=250)
    quantity = models.DecimalField(
        "quantité",
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price = models.DecimalField(
        "prix unitaire (MAD)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    discount_type = models.CharField(
        "type de remise",
        max_length=12,
        choices=QuoteLine.DiscountType.choices,
        default=QuoteLine.DiscountType.PERCENTAGE,
    )
    discount_value = models.DecimalField(
        "remise",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    tax_rate = models.ForeignKey(
        "catalog.TaxRate",
        on_delete=models.PROTECT,
        related_name="invoice_lines",
        verbose_name="taux appliqué",
        null=True,
        blank=True,
    )
    tax_percentage = models.DecimalField(
        "pourcentage de taxe historisé",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    tax_name = models.CharField("libellé fiscal historisé", max_length=100, blank=True)
    position = models.PositiveIntegerField("ordre", default=0)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "ligne de facture"
        verbose_name_plural = "lignes de facture"
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="invoice_line_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="invoice_line_unit_price_nonnegative"),
            models.CheckConstraint(
                condition=Q(discount_value__gte=0),
                name="invoice_line_discount_nonnegative",
            ),
            models.CheckConstraint(
                condition=~Q(discount_type="percentage") | Q(discount_value__lte=100),
                name="invoice_line_percentage_discount_valid",
            ),
            models.CheckConstraint(
                condition=Q(tax_percentage__isnull=True)
                | Q(tax_percentage__gte=0, tax_percentage__lte=100),
                name="invoice_line_tax_percentage_valid",
            ),
        ]

    def clean(self):
        super().clean()
        if self.invoice_id and self.invoice.status != Invoice.Status.DRAFT:
            raise ValidationError("Les lignes d'une facture émise sont immuables.")
        if self.discount_type == QuoteLine.DiscountType.PERCENTAGE and self.discount_value > 100:
            raise ValidationError({"discount_value": "La remise ne peut pas dépasser 100 %."})
        if (
            self.discount_type == QuoteLine.DiscountType.FIXED
            and self.quantity is not None
            and self.unit_price is not None
        ):
            gross = round_money(self.quantity * self.unit_price)
            if self.discount_value > gross:
                raise ValidationError(
                    {"discount_value": "La remise ne peut pas dépasser le montant HT de la ligne."}
                )

    def save(self, *args, **kwargs):
        if self.pk:
            current = type(self).objects.select_related("invoice").filter(pk=self.pk).first()
            if current and current.invoice.status != Invoice.Status.DRAFT:
                raise ValidationError("Les lignes d'une facture émise sont immuables.")
        self.full_clean()
        if self.tax_rate_id:
            if self.tax_percentage is None:
                self.tax_percentage = self.tax_rate.percentage
            if not self.tax_name:
                self.tax_name = self.tax_rate.name
        elif self.tax_percentage is None:
            self.tax_percentage = Decimal("0.00")
            self.tax_name = "Sans taxe"
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.status != Invoice.Status.DRAFT:
            raise ValidationError("Les lignes d'une facture émise sont immuables.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.description


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Espèces"
        BANK_TRANSFER = "bank_transfer", "Virement bancaire"
        CARD = "card", "Carte"
        CHEQUE = "cheque", "Chèque"
        OTHER = "other", "Autre"

    customer = models.ForeignKey(
        "partners.Customer",
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="client",
    )
    payment_date = models.DateField("date du paiement")
    amount = models.DecimalField(
        "montant (MAD)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    method = models.CharField("mode de paiement", max_length=20, choices=Method.choices)
    reference = models.CharField("référence", max_length=100, blank=True)
    notes = models.TextField("notes", blank=True)
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        verbose_name = "paiement"
        verbose_name_plural = "paiements"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
        ]

    @property
    def allocated_amount(self):
        total = self.allocations.aggregate(total=Sum("amount"))["total"]
        return round_money(total or Decimal("0.00"))

    @property
    def unallocated_amount(self):
        return round_money(self.amount - self.allocated_amount)

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": "Le montant du paiement doit être supérieur à zéro."})
        if self.pk:
            allocated = self.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            if self.amount is not None and self.amount < allocated:
                raise ValidationError(
                    {"amount": "Le montant ne peut pas être inférieur aux imputations déjà enregistrées."}
                )

    def __str__(self):
        return f"Paiement de {self.amount} MAD du {self.payment_date} ({self.customer})"


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="paiement",
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="payment_allocations",
        verbose_name="facture",
    )
    amount = models.DecimalField(
        "montant affecté (MAD)",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    created_at = models.DateTimeField("créée le", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "affectation de paiement"
        verbose_name_plural = "affectations de paiements"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_allocation_amount_positive"),
        ]

    def clean(self):
        super().clean()
        if self.amount is not None and self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": "Le montant affecté doit être supérieur à zéro."})
        if self.payment_id and self.invoice_id:
            if self.payment.customer_id != self.invoice.customer_id:
                raise ValidationError("Le paiement et la facture doivent appartenir au même client.")
            if self.invoice.status not in {
                Invoice.Status.ISSUED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.OVERDUE,
            }:
                raise ValidationError("Une imputation ne peut être enregistrée que sur une facture émise.")
            other_allocations = (
                self.payment.allocations.exclude(pk=self.pk).aggregate(total=Sum("amount"))["total"]
                or Decimal("0.00")
            )
            if other_allocations + self.amount > self.payment.amount:
                raise ValidationError({"amount": "L'imputation dépasse le montant disponible du paiement."})
            other_invoice_allocations = (
                self.invoice.payment_allocations.exclude(pk=self.pk).aggregate(total=Sum("amount"))["total"]
                or Decimal("0.00")
            )
            invoice_total = self.invoice.total_ttc
            if other_invoice_allocations + self.amount > invoice_total:
                raise ValidationError({"amount": "L'imputation dépasse le solde restant de la facture."})

    def __str__(self):
        return f"{self.amount} MAD vers {self.invoice}"


class DocumentHistory(models.Model):
    class Event(models.TextChoices):
        CREATED = "created", "Création"
        UPDATED = "updated", "Modification"
        ISSUED = "issued", "Émission"
        CORRECTED = "corrected", "Correction"
        CANCELLED = "cancelled", "Annulation"
        STATUS_CHANGED = "status_changed", "Changement de statut"
        REVISION_CREATED = "revision_created", "Nouvelle version créée"
        PAYMENT_RECORDED = "payment_recorded", "Paiement enregistré"

    quote = models.ForeignKey(
        Quote,
        on_delete=models.PROTECT,
        related_name="history",
        verbose_name="devis",
        null=True,
        blank=True,
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="history",
        verbose_name="facture",
        null=True,
        blank=True,
    )
    event = models.CharField("événement", max_length=20, choices=Event.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="document_history",
        verbose_name="utilisateur",
        null=True,
        blank=True,
    )
    snapshot = models.JSONField("instantané", default=dict)
    created_at = models.DateTimeField("date de l'événement", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "historique de document"
        verbose_name_plural = "historiques de documents"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(quote__isnull=False, invoice__isnull=True)
                    | Q(quote__isnull=True, invoice__isnull=False)
                ),
                name="document_history_exactly_one_document",
            ),
        ]

    def __str__(self):
        document = self.quote or self.invoice
        return f"{self.get_event_display()} - {document}"

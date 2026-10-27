from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone


# ==============================================================================
# MODULE DE SAISIE COMPTABLE - PCGE MAROC (Plan Comptable Général des Entreprises)
# Conçu avec pédagogie pour les utilisateurs débutants & conforme à la Loi 9-88
# ==============================================================================


class CompteComptable(models.Model):
    """
    MODÈLE : CompteComptable
    ------------------------
    Représente un compte du Plan Comptable Général des Entreprises (PCGE) marocain.

    Pédagogie pour le débutant :
    En comptabilité marocaine, chaque flux économique doit être imputé dans un compte
    numéroté. Le PCGE marocain structure les comptes en 7 CLASSES fondamentales :
    - Classe 1 : Financement Permanent (Ressources durables : capital des associés, emprunts bancaires > 1 an)
    - Classe 2 : Actif Immobilisé (Biens durables : serveurs informatiques, ordinateurs, licences, locaux)
    - Classe 3 : Actif Circulant hors trésorerie (Créances sur les clients, stocks, TVA récupérable)
    - Classe 4 : Passif Circulant hors trésorerie (Dettes fournisseurs à court terme, TVA due à l'État, CNSS)
    - Classe 5 : Trésorerie (Disponibilités liquides : comptes bancaires BMCE/BOA, caisse en espèces)
    - Classe 6 : Charges (Dépenses consommées : achats de matériel, loyers, salaires, électricité)
    - Classe 7 : Produits (Revenus et chiffre d'affaires : conseil IA, formations, vente de matériel)

    Règle mnémotechnique du premier chiffre :
    Le premier chiffre d'un compte correspond TOUJOURS au numéro de sa classe (ex: 3421 commence par 3 -> Classe 3).
    """

    class Classe(models.IntegerChoices):
        CLASSE_1 = 1, "Classe 1 - Financement Permanent (Capitaux propres & emprunts > 1 an : ressources durables)"
        CLASSE_2 = 2, "Classe 2 - Actif Immobilisé (Biens durables : ordinateurs, serveurs, licences, locaux)"
        CLASSE_3 = 3, "Classe 3 - Actif Circulant hors trésorerie (Créances clients, stocks, TVA récupérable)"
        CLASSE_4 = 4, "Classe 4 - Passif Circulant hors trésorerie (Dettes fournisseurs court terme, TVA facturée)"
        CLASSE_5 = 5, "Classe 5 - Trésorerie (Disponibilités immédiates : banques, caisse en espèces, chèques)"
        CLASSE_6 = 6, "Classe 6 - Charges (Consommations et dépenses d'exploitation : achats, loyers, salaires)"
        CLASSE_7 = 7, "Classe 7 - Produits (Revenus et Chiffre d'Affaires : prestations IA, formations, ventes)"

    class SensNormal(models.TextChoices):
        DEBIT = "DEBIT", "Débiteur (Augmente par le DÉBIT - Actif et Charges)"
        CREDIT = "CREDIT", "Créditeur (Augmente par le CRÉDIT - Passif, Financement et Produits)"

    numero = models.CharField(
        "Numéro de compte",
        max_length=10,
        unique=True,
        db_index=True,
        help_text="Exemple : 3421 (Clients), 4411 (Fournisseurs), 5141 (Banque). Le 1er chiffre indique la classe.",
    )
    intitule = models.CharField(
        "Intitulé du compte",
        max_length=150,
        help_text="Nom clair et explicite du compte (ex: Clients - BenchTech, Vente matériel informatique)",
    )
    classe = models.PositiveSmallIntegerField(
        "Classe comptable (PCGE)",
        choices=Classe.choices,
        help_text="Classe officielle marocaine (1 à 7) déterminant la nature économique du compte",
    )
    sens_normal = models.CharField(
        "Sens normal du solde",
        max_length=6,
        choices=SensNormal.choices,
        default=SensNormal.DEBIT,
        help_text="Règle d'or : Actif (2, 3, 5) et Charges (6) augmentent au Débit. Passif (1, 4) et Produits (7) augmentent au Crédit.",
    )
    description_pedagogique = models.TextField(
        "Guide d'utilisation pédagogique",
        blank=True,
        help_text="Explication simple et pratique pour aider le débutant à savoir quand débiter ou créditer ce compte.",
    )
    is_active = models.BooleanField(
        "Compte actif",
        default=True,
        help_text="Permet de désactiver un compte obsolète sans altérer l'historique comptable.",
    )

    class Meta:
        app_label = "finance"
        ordering = ["numero"]
        verbose_name = "Compte comptable (PCGE)"
        verbose_name_plural = "Comptes comptables (PCGE)"

    def clean(self):
        super().clean()
        # Contrôle pédagogique : le numéro de compte doit impérativement débuter par le chiffre de sa classe
        if self.numero and self.classe:
            chiffre_attendu = str(self.classe)
            if not self.numero.startswith(chiffre_attendu):
                raise ValidationError({
                    "numero": (
                        f"⚠️ Règle PCGE Maroc : Le compte '{self.numero}' est rattaché à la Classe {self.classe}. "
                        f"Son numéro doit obligatoirement commencer par le chiffre '{chiffre_attendu}' "
                        f"(ex: {chiffre_attendu}xxx)."
                    )
                })

    def save(self, *args, **kwargs):
        # Déduction automatique intelligente du sens normal si non défini
        if not self.sens_normal:
            if self.classe in (self.Classe.CLASSE_2, self.Classe.CLASSE_3, self.Classe.CLASSE_5, self.Classe.CLASSE_6):
                self.sens_normal = self.SensNormal.DEBIT
            else:
                self.sens_normal = self.SensNormal.CREDIT
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.numero} - {self.intitule}"


class JournalComptable(models.Model):
    """
    MODÈLE : JournalComptable
    -------------------------
    Représente un journal auxiliaire obligatoire (Loi marocaine n° 9-88).

    Pédagogie pour le débutant :
    Un journal est un registre chronologique spécialisé regroupant les opérations de même nature :
    - VE (Ventes) : Factures émises aux clients (Conseil IA, Formations, Matériel).
    - AC (Achats) : Factures reçues des fournisseurs de biens et prestations.
    - TR / BQ / CA (Trésorerie) : Mouvements financiers réels (Banque BOA, Caisse espèces).
    - OD (Opérations Diverses) : Paie, déclarations de TVA, amortissements, écritures d'inventaire.
    """

    class TypeJournal(models.TextChoices):
        VENTES = "VENTES", "Journal des Ventes (Facturation clients - Conseil, Formation, Matériel)"
        ACHATS = "ACHATS", "Journal des Achats (Dépenses fournisseurs et achats de matériel)"
        TRESORERIE = "TRESORERIE", "Journal de Trésorerie (Banque BMCE / Caisse espèces)"
        OPERATIONS_DIVERSES = "OD", "Opérations Diverses (Salaires CNSS, déclarations TVA, clôtures)"

    code = models.CharField(
        "Code journal",
        max_length=6,
        unique=True,
        help_text="Code court usuel : VE (Ventes), AC (Achats), BQ (Banque), CA (Caisse), OD (Opérations Diverses)",
    )
    intitule = models.CharField(
        "Intitulé du journal",
        max_length=100,
        help_text="Nom complet (ex: Journal des Ventes de Prestations et Matériel)",
    )
    type_journal = models.CharField(
        "Type de journal",
        max_length=20,
        choices=TypeJournal.choices,
        default=TypeJournal.OPERATIONS_DIVERSES,
        help_text="Catégorisation officielle du journal",
    )
    compte_contrepartie = models.ForeignKey(
        CompteComptable,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journaux_contrepartie",
        verbose_name="Compte de contrepartie habituel",
        help_text="Optionnel : Compte suggéré automatiquement (ex: 5141 Banque pour le journal BQ)",
    )
    description_pedagogique = models.TextField(
        "Explication pédagogique pour débutant",
        blank=True,
        help_text="Guide pratique expliquant quelles opérations enregistrer dans ce journal.",
    )
    is_active = models.BooleanField(
        "Journal actif",
        default=True,
    )

    class Meta:
        app_label = "finance"
        ordering = ["code"]
        verbose_name = "Journal comptable"
        verbose_name_plural = "Journaux comptables"

    def __str__(self):
        return f"{self.code} - {self.intitule}"


class EcritureComptable(models.Model):
    """
    MODÈLE : EcritureComptable
    --------------------------
    Représente une pièce comptable en partie double (Loi n° 9-88).

    Principes fondamentaux et sécurité légale :
    1. PARTIE DOUBLE : Le Total des DÉBITS doit être STRICTEMENT égal au Total des CRÉDITS.
    2. IMMUABILITÉ : Dès qu'une écriture est au statut 'COMPTABILISE', elle est VERROUILLÉE.
       Toute modification ou suppression directe est interdite par la loi marocaine (art. 11 & 16).
    3. CONTRE-PASSATION (Extourne) : La seule manière légale de corriger une écriture validée est
       de générer une écriture inverse qui neutralise mathématiquement l'écriture initiale.
    4. PÉDAGOGIE FISCALE : Contrôle bienveillant non bloquant sur le seuil des espèces à 5 000 MAD (CGI).
    """

    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon (En cours de saisie - modifiable et supprimable)"
        COMPTABILISE = "COMPTABILISE", "Comptabilisé (Validé définitivement - Strictement immuable Loi 9-88)"
        CONTREPASSE = "CONTREPASSE", "Contre-passé (Neutralisé légalement par une écriture d'extourne)"

    journal = models.ForeignKey(
        JournalComptable,
        on_delete=models.PROTECT,
        related_name="ecritures",
        verbose_name="Journal comptable",
        help_text="Journal dans lequel cette écriture est enregistrée",
    )
    numero_piece = models.CharField(
        "Numéro de pièce interne",
        max_length=50,
        help_text="Numéro séquentiel unique de la pièce comptable (ex: PC-2026-0001)",
    )
    date_comptable = models.DateField(
        "Date comptable",
        help_text="Date à laquelle l'opération impacte officiellement la comptabilité de l'entreprise",
    )
    reference = models.CharField(
        "Référence justificative",
        max_length=100,
        help_text="Référence externe du justificatif (ex: N° Facture FAC-2026-0012, N° Chèque 874512)",
    )
    libelle = models.CharField(
        "Libellé de l'écriture",
        max_length=255,
        help_text="Description explicite et détaillée de l'opération économique",
    )
    statut = models.CharField(
        "Statut comptable",
        max_length=20,
        choices=Statut.choices,
        default=Statut.BROUILLON,
        db_index=True,
        help_text="État légal de la pièce comptable",
    )
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ecritures_saisies",
        verbose_name="Auteur de la saisie",
        help_text="Traçabilité légale : utilisateur qui a saisi ou validé cette écriture",
    )
    date_comptabilisation = models.DateTimeField(
        "Date de validation définitive",
        null=True,
        blank=True,
        editable=False,
        help_text="Horodatage officiel du verrouillage légal",
    )
    contrepassation_de = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contrepassations",
        verbose_name="Écriture d'origine extournée",
        help_text="Si cette écriture est une contre-passation, pointe vers la pièce initiale annulée",
    )
    notes_pedagogiques = models.TextField(
        "Notes d'apprentissage & Mémo",
        blank=True,
        help_text="Explication libre de la logique comptable appliquée pour aider un collègue débutant",
    )
    created_at = models.DateTimeField("Date de création", auto_now_add=True)
    updated_at = models.DateTimeField("Dernière mise à jour", auto_now=True)

    class Meta:
        app_label = "finance"
        ordering = ["-date_comptable", "-id"]
        verbose_name = "Écriture comptable"
        verbose_name_plural = "Écritures comptables"

    def __str__(self):
        return f"{self.numero_piece} ({self.date_comptable}) - {self.libelle} [{self.get_statut_display()}]"

    # --------------------------------------------------------------------------
    # PROPRIÉTÉS ET MÉTHODES DE CALCUL DE L'ÉQUILIBRE (PARTIE DOUBLE)
    # --------------------------------------------------------------------------

    @property
    def total_debit(self) -> Decimal:
        """
        Somme de tous les débits des lignes de cette écriture.
        """
        agg = self.lignes.aggregate(total=Sum("debit"))["total"] or Decimal("0.00")
        return Decimal(str(agg)).quantize(Decimal("0.01"))

    @property
    def total_credit(self) -> Decimal:
        """
        Somme de tous les crédits des lignes de cette écriture.
        """
        agg = self.lignes.aggregate(total=Sum("credit"))["total"] or Decimal("0.00")
        return Decimal(str(agg)).quantize(Decimal("0.01"))

    @property
    def ecart(self) -> Decimal:
        """
        Écart absolu entre Total Débit et Total Crédit.
        Doit être exactement égal à 0,00 MAD pour qu'une écriture soit valide.
        """
        return abs(self.total_debit - self.total_credit).quantize(Decimal("0.01"))

    @property
    def is_equilibre(self) -> bool:
        """
        Vérifie si la règle d'or de la partie double est respectée :
        Total Débit == Total Crédit et montant total > 0.
        """
        return self.total_debit == self.total_credit and self.total_debit > Decimal("0.00")

    def verifier_equilibre(self):
        """
        Vérification automatique de l'équilibre Débit/Crédit.
        Lève une ValidationError pédagogique détaillée avec calcul précis de l'écart.
        """
        lignes_qs = self.lignes.all()
        nb_lignes = lignes_qs.count()

        if nb_lignes < 2:
            raise ValidationError(
                f"⚠️ Une écriture comptable en partie double doit comporter au minimum 2 lignes "
                f"(votre écriture en compte actuellement {nb_lignes}).\n\n"
                f"💡 Conseil pédagogique pour débutant :\n"
                f"Toute opération économique affecte toujours au moins deux comptes : un compte débité (Emploi) "
                f"et un compte crédité (Ressource)."
            )

        tot_deb = self.total_debit
        tot_crd = self.total_credit
        diff = abs(tot_deb - tot_crd)

        if diff != Decimal("0.00"):
            sens_manquant = "au CRÉDIT" if tot_deb > tot_crd else "au DÉBIT"
            raise ValidationError(
                f"⚠️ Déséquilibre comptable détecté !\n"
                f"Conformément au principe fondamental de la partie double (PCGE Maroc) :\n"
                f"• Total des DÉBITS  : {tot_deb:,.2f} MAD\n"
                f"• Total des CRÉDITS : {tot_crd:,.2f} MAD\n"
                f"• Écart à combler   : {diff:,.2f} MAD manquant {sens_manquant}.\n\n"
                f"💡 Guide débutant :\n"
                f"En comptabilité, chaque dirham entrant (Débit) doit provenir d'une ressource équivalente (Crédit).\n"
                f"Veuillez équilibrer votre saisie en ajustant vos montants ou en ajoutant une ligne "
                f"de {diff:,.2f} MAD {sens_manquant} avant de pouvoir comptabiliser."
            )

    # --------------------------------------------------------------------------
    # CONTRÔLES MÉTIER & PÉDAGOGIE FISCALE (CGI MAROC)
    # --------------------------------------------------------------------------

    def alertes_fiscales(self) -> list:
        """
        Contrôle non bloquant des dispositions du Code Général des Impôts (CGI) marocain.
        Fournit des avertissements éducatifs et bienveillants à l'utilisateur.
        """
        alertes = []
        # Contrôle du seuil des règlements en espèces (Article 11-II du CGI marocain)
        # Limite de 5 000 MAD TTC par jour et par fournisseur/partenaire.
        for ligne in self.lignes.select_related("compte").all():
            if ligne.compte.numero.startswith("516"):  # Compte de Caisse (5161...)
                montant_especes = max(ligne.debit, ligne.credit)
                if montant_especes > Decimal("5000.00"):
                    fraction_excedentaire = montant_especes - Decimal("5000.00")
                    alertes.append(
                        f"💡 Avertissement fiscal pédagogique (Art. 11-II du CGI Maroc) sur le compte "
                        f"« {ligne.compte.numero} - {ligne.compte.intitule} » :\n"
                        f"Le mouvement en espèces s'élève à {montant_especes:,.2f} MAD, ce qui dépasse le plafond légal "
                        f"de déductibilité fiscale fixé à 5 000 MAD TTC par jour et par fournisseur/partenaire.\n"
                        f"• Montant déductible fiscalement : 5 000,00 MAD\n"
                        f"• Fraction excédentaire : {fraction_excedentaire:,.2f} MAD (non déductible, à réintégrer lors du calcul de l'Impôt sur les Sociétés - IS)."
                    )
        return alertes

    # --------------------------------------------------------------------------
    # VALIDATION, VERROUILLAGE LÉGAL ET CONTRE-PASSATION (LOI 9-88)
    # --------------------------------------------------------------------------

    @transaction.atomic
    def valider_et_comptabiliser(self, user=None):
        """
        Valide définitivement l'écriture et active le verrouillage légal (Loi 9-88).
        Une fois comptabilisée, l'écriture ne peut plus être ni modifiée ni supprimée.
        """
        if self.statut == self.Statut.COMPTABILISE:
            raise ValidationError("Cette écriture est déjà comptabilisée et verrouillée.")

        # 1. Contrôle strict de l'équilibre Débit/Crédit
        self.verifier_equilibre()

        # 2. Verrouillage légal
        self.statut = self.Statut.COMPTABILISE
        self.date_comptabilisation = timezone.now()
        if user:
            self.auteur = user
        self.save(update_fields=["statut", "date_comptabilisation", "auteur", "updated_at"])

    @transaction.atomic
    def creer_contrepassation(self, user):
        """
        Génère une écriture légale de contre-passation (extourne) conformément à la Loi 9-88.
        Inverse strictement le sens Débit/Crédit de chaque ligne pour neutraliser l'écriture initiale.
        """
        if self.statut != self.Statut.COMPTABILISE:
            raise ValidationError("Seule une écriture validée au statut 'Comptabilisé' peut faire l'objet d'une contre-passation.")
        if self.statut == self.Statut.CONTREPASSE:
            raise ValidationError("Cette écriture a déjà fait l'objet d'une contre-passation.")

        # 1. Création de la pièce d'extourne (en brouillon pour autoriser l'insertion des lignes)
        piece_extourne = EcritureComptable.objects.create(
            journal=self.journal,
            numero_piece=f"EXT-{self.numero_piece}",
            date_comptable=timezone.now().date(),
            reference=f"Extourne de {self.reference}",
            libelle=f"Contre-passation / Extourne de la pièce {self.numero_piece} : {self.libelle}",
            statut=self.Statut.BROUILLON,
            auteur=user,
            contrepassation_de=self,
            notes_pedagogiques=(
                f"Écriture d'annulation légale (extourne) générée automatiquement pour neutraliser la pièce {self.numero_piece}.\n"
                f"Conformément à la Loi marocaine n° 9-88 (art. 11 & 16), l'écriture initiale reste intacte dans le grand livre, "
                f"et cette pièce inverse neutralise les soldes mathématiquement."
            ),
        )

        # 2. Inversion exacte de chaque ligne (le Débit initial devient Crédit, le Crédit initial devient Débit)
        for ligne in self.lignes.all():
            LigneEcriture.objects.create(
                ecriture=piece_extourne,
                compte=ligne.compte,
                debit=ligne.credit,   # Le crédit initial devient débit
                credit=ligne.debit,   # Le débit initial devient crédit
                libelle_ligne=f"Extourne: {ligne.libelle_ligne}",
                position=ligne.position,
            )

        # 3. Comptabilisation immédiate de l'écriture d'extourne
        piece_extourne.statut = self.Statut.COMPTABILISE
        piece_extourne.date_comptabilisation = timezone.now()
        piece_extourne.save(update_fields=["statut", "date_comptabilisation", "updated_at"])

        # 4. Marquage de l'écriture initiale comme contre-passée
        self.statut = self.Statut.CONTREPASSE
        self.save(update_fields=["statut", "updated_at"])

        return piece_extourne

    # --------------------------------------------------------------------------
    # IMMUABILITÉ EN BASE DE DONNÉES (LOI 9-88)
    # --------------------------------------------------------------------------

    def clean(self):
        super().clean()
        if self.pk:
            initial = EcritureComptable.objects.filter(pk=self.pk).first()
            if initial and initial.statut == self.Statut.COMPTABILISE:
                # Interdiction de modifier les champs financiers ou légaux d'une écriture comptabilisée
                if self.statut == self.Statut.BROUILLON:
                    raise ValidationError(
                        "🔒 Règle d'immuabilité (Loi marocaine n° 9-88) : "
                        "Une écriture comptabilisée ne peut jamais revenir à l'état de brouillon."
                    )
                for field_name in ["journal_id", "numero_piece", "date_comptable", "reference", "libelle"]:
                    if getattr(initial, field_name) != getattr(self, field_name):
                        raise ValidationError(
                            f"🔒 Règle d'immuabilité (Loi marocaine n° 9-88, art. 11 & 16) :\n"
                            f"Cette écriture est COMPTABILISÉE et légalement verrouillée. "
                            f"La modification du champ '{field_name}' est strictement interdite.\n"
                            f"Pour corriger une erreur, veuillez effectuer une contre-passation (extourne)."
                        )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.statut in (self.Statut.COMPTABILISE, self.Statut.CONTREPASSE):
            raise ValidationError(
                "🔒 Règle d'immuabilité légale (Loi marocaine n° 9-88) :\n"
                "Une écriture comptabilisée ou contre-passée fait partie intégrante des archives comptables légales "
                "et ne peut JAMAIS être supprimée du système.\n"
                "Pour neutraliser son impact, veuillez utiliser la fonction de contre-passation (extourne)."
            )
        return super().delete(*args, **kwargs)


class LigneEcriture(models.Model):
    """
    MODÈLE : LigneEcriture
    ----------------------
    Représente une ligne au sein d'une écriture comptable en partie double.

    Règles d'or pour le débutant :
    1. Sens unique : Une ligne comporte SOIT un montant au DÉBIT, SOIT un montant au CRÉDIT, jamais les deux.
    2. Pas de zéro : Au moins l'une des deux colonnes doit être supérieure à 0,00 MAD.
    3. Positivité : Les montants sont obligatoirement positifs (en comptabilité, on n'utilise pas de nombres négatifs).
    4. Immuabilité : Si l'écriture parente est comptabilisée, aucune ligne ne peut être modifiée ou supprimée.
    """

    ecriture = models.ForeignKey(
        EcritureComptable,
        on_delete=models.CASCADE,
        related_name="lignes",
        verbose_name="Pièce comptable parente",
    )
    compte = models.ForeignKey(
        CompteComptable,
        on_delete=models.PROTECT,
        related_name="lignes_ecritures",
        verbose_name="Compte comptable (PCGE)",
        help_text="Compte du Plan Comptable Marocain affecté par ce mouvement",
    )
    debit = models.DecimalField(
        "Débit (MAD)",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Montant inscrit au Débit (Emploi / Entrée de valeur)",
    )
    credit = models.DecimalField(
        "Crédit (MAD)",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Montant inscrit au Crédit (Ressource / Source du financement)",
    )
    libelle_ligne = models.CharField(
        "Libellé de la ligne",
        max_length=255,
        help_text="Explication courte de la ligne (ex: Facture client Alpha SARL, TVA collectée 20%, Vente formation)",
    )
    position = models.PositiveIntegerField(
        "Ordre d'affichage",
        default=1,
        help_text="Numéro d'ordre de la ligne dans la pièce (1, 2, 3...)",
    )

    class Meta:
        app_label = "finance"
        ordering = ["position", "id"]
        verbose_name = "Ligne d'écriture comptable"
        verbose_name_plural = "Lignes d'écriture comptable"

    def clean(self):
        super().clean()
        # 1. Vérification de l'immuabilité si l'écriture parente est déjà validée
        if self.ecriture_id:
            statut_parent = getattr(self.ecriture, "statut", None)
            if statut_parent == EcritureComptable.Statut.COMPTABILISE:
                raise ValidationError(
                    "🔒 Règle d'immuabilité (Loi 9-88) : "
                    "Cette écriture est comptabilisée. Il est strictement interdit d'en modifier ou d'en ajouter les lignes."
                )

        # 2. Vérification des montants Débit et Crédit
        deb = self.debit or Decimal("0.00")
        crd = self.credit or Decimal("0.00")

        if deb > Decimal("0.00") and crd > Decimal("0.00"):
            raise ValidationError(
                "⚠️ Règle comptable fondamentale : Une même ligne ne peut pas comporter simultanément un montant "
                "au Débit ET au Crédit.\n"
                "💡 Conseil débutant : Choisissez une seule colonne. Si une opération implique un débit et un crédit, "
                "saisissez deux lignes séparées."
            )

        if deb == Decimal("0.00") and crd == Decimal("0.00"):
            raise ValidationError(
                "⚠️ Montant obligatoire : Vous devez saisir un montant strictement supérieur à 0 soit au Débit, soit au Crédit."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.ecriture and self.ecriture.statut == EcritureComptable.Statut.COMPTABILISE:
            raise ValidationError(
                "🔒 Règle d'immuabilité (Loi 9-88) : Impossible de supprimer une ligne appartenant à une écriture comptabilisée."
            )
        return super().delete(*args, **kwargs)

    def __str__(self):
        sens = f"Débit: {self.debit} MAD" if self.debit > 0 else f"Crédit: {self.credit} MAD"
        return f"{self.compte.numero} | {self.libelle_ligne} | {sens}"


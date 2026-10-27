from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.finance.models import (
    CompteComptable,
    JournalComptable,
    EcritureComptable,
    LigneEcriture,
)


class AccountingEntryModuleTests(TestCase):
    """
    Suite de tests validant le module de saisie comptable marocaine (PCGE, Partie double, Loi 9-88, CGI).
    """

    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            username="junior_accountant",
            password="testpassword",
            first_name="Karim",
            last_name="El Fassi",
        )

        # Création des comptes types PCGE
        self.compte_client = CompteComptable.objects.create(
            numero="3421",
            intitule="Clients",
            classe=CompteComptable.Classe.CLASSE_3,
            sens_normal=CompteComptable.SensNormal.DEBIT,
        )
        self.compte_vente_ia = CompteComptable.objects.create(
            numero="7124",
            intitule="Ventes de services (Conseil en technologies & IA)",
            classe=CompteComptable.Classe.CLASSE_7,
            sens_normal=CompteComptable.SensNormal.CREDIT,
        )
        self.compte_tva_fact = CompteComptable.objects.create(
            numero="4455",
            intitule="État - TVA facturée",
            classe=CompteComptable.Classe.CLASSE_4,
            sens_normal=CompteComptable.SensNormal.CREDIT,
        )
        self.compte_banque = CompteComptable.objects.create(
            numero="5141",
            intitule="Banque BMCE / BOA",
            classe=CompteComptable.Classe.CLASSE_5,
            sens_normal=CompteComptable.SensNormal.DEBIT,
        )
        self.compte_caisse = CompteComptable.objects.create(
            numero="5161",
            intitule="Caisse centrale (Espèces)",
            classe=CompteComptable.Classe.CLASSE_5,
            sens_normal=CompteComptable.SensNormal.DEBIT,
        )

        # Journaux
        self.journal_ve = JournalComptable.objects.create(
            code="VE",
            intitule="Journal des Ventes",
            type_journal=JournalComptable.TypeJournal.VENTES,
        )
        self.journal_ca = JournalComptable.objects.create(
            code="CA",
            intitule="Journal de Caisse",
            type_journal=JournalComptable.TypeJournal.TRESORERIE,
            compte_contrepartie=self.compte_caisse,
        )

    def test_compte_comptable_coherence_classe(self):
        """Le numéro de compte doit impérativement débuter par le chiffre de sa classe."""
        compte_incoherent = CompteComptable(
            numero="6111",
            intitule="Fournisseurs erroné",
            classe=CompteComptable.Classe.CLASSE_4,  # Devrait commencer par 4
        )
        with self.assertRaises(ValidationError) as ctx:
            compte_incoherent.clean()
        self.assertIn("Règle PCGE Maroc", str(ctx.exception))

    def test_partie_double_blocage_si_desequilibre(self):
        """Une écriture déséquilibrée doit bloquer la validation et afficher un message pédagogique."""
        ecriture = EcritureComptable.objects.create(
            journal=self.journal_ve,
            numero_piece="PC-2026-0001",
            date_comptable=date(2026, 10, 15),
            reference="FAC-2026-0001",
            libelle="Facture Mission Conseil IA client Alpha SARL",
            auteur=self.user,
        )
        # Débit 12 000 MAD au compte client
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_client,
            debit=Decimal("12000.00"),
            credit=Decimal("0.00"),
            libelle_ligne="Créance client TTC",
            position=1,
        )
        # Crédit seulement 10 000 MAD en Ventes HT (manque 2 000 de TVA)
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_vente_ia,
            debit=Decimal("0.00"),
            credit=Decimal("10000.00"),
            libelle_ligne="Prestation IA HT",
            position=2,
        )

        self.assertFalse(ecriture.is_equilibre)
        self.assertEqual(ecriture.ecart, Decimal("2000.00"))

        # La tentative de validation doit échouer
        with self.assertRaises(ValidationError) as ctx:
            ecriture.valider_et_comptabiliser(user=self.user)

        error_msg = str(ctx.exception)
        self.assertIn("Déséquilibre comptable détecté", error_msg)
        self.assertIn("12,000.00 MAD", error_msg)
        self.assertIn("10,000.00 MAD", error_msg)
        self.assertIn("2,000.00 MAD manquant au CRÉDIT", error_msg)
        self.assertIn("Guide débutant", error_msg)

    def test_validation_reussie_quand_partie_double_equilibree(self):
        """L'écriture s'équilibre et se valide avec succès dès que Total Débit == Total Crédit."""
        ecriture = EcritureComptable.objects.create(
            journal=self.journal_ve,
            numero_piece="PC-2026-0002",
            date_comptable=date(2026, 10, 15),
            reference="FAC-2026-0002",
            libelle="Facture Mission Conseil IA client Beta SA",
            auteur=self.user,
        )
        # Ligne 1 : Débit Client TTC 12 000
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_client,
            debit=Decimal("12000.00"),
            credit=Decimal("0.00"),
            libelle_ligne="Créance client TTC",
            position=1,
        )
        # Ligne 2 : Crédit Vente HT 10 000
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_vente_ia,
            debit=Decimal("0.00"),
            credit=Decimal("10000.00"),
            libelle_ligne="Prestation IA HT",
            position=2,
        )
        # Ligne 3 : Crédit TVA facturée 2 000
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_tva_fact,
            debit=Decimal("0.00"),
            credit=Decimal("2000.00"),
            libelle_ligne="TVA facturée 20%",
            position=3,
        )

        self.assertTrue(ecriture.is_equilibre)
        self.assertEqual(ecriture.ecart, Decimal("0.00"))

        # Validation officielle
        ecriture.valider_et_comptabiliser(user=self.user)
        ecriture.refresh_from_db()

        self.assertEqual(ecriture.statut, EcritureComptable.Statut.COMPTABILISE)
        self.assertIsNotNone(ecriture.date_comptabilisation)

    def test_immuabilite_legale_loi_9_88(self):
        """Une écriture comptabilisée ne peut être ni modifiée ni supprimée (Loi 9-88)."""
        ecriture = EcritureComptable.objects.create(
            journal=self.journal_ve,
            numero_piece="PC-2026-0003",
            date_comptable=date(2026, 10, 15),
            reference="FAC-2026-0003",
            libelle="Écriture validée",
            auteur=self.user,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_client,
            debit=Decimal("5000.00"),
            credit=Decimal("0.00"),
            libelle_ligne="Débit",
            position=1,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_vente_ia,
            debit=Decimal("0.00"),
            credit=Decimal("5000.00"),
            libelle_ligne="Crédit",
            position=2,
        )
        ecriture.valider_et_comptabiliser(user=self.user)

        # 1. Tentative de modification du libellé
        ecriture.libelle = "Tentative de modification illégale"
        with self.assertRaises(ValidationError) as ctx:
            ecriture.save()
        self.assertIn("Règle d'immuabilité (Loi marocaine n° 9-88", str(ctx.exception))

        # 2. Tentative de suppression de l'écriture
        ecriture.refresh_from_db()
        with self.assertRaises(ValidationError) as ctx:
            ecriture.delete()
        self.assertIn("Règle d'immuabilité légale (Loi marocaine n° 9-88)", str(ctx.exception))

        # 3. Tentative d'ajout d'une nouvelle ligne sur une écriture validée
        with self.assertRaises(ValidationError) as ctx:
            LigneEcriture.objects.create(
                ecriture=ecriture,
                compte=self.compte_tva_fact,
                debit=Decimal("0.00"),
                credit=Decimal("100.00"),
                libelle_ligne="Ligne interdite",
                position=3,
            )
        self.assertIn("Règle d'immuabilité (Loi 9-88)", str(ctx.exception))

    def test_contrepassation_legale_loi_9_88(self):
        """La contre-passation crée une extourne inversant rigoureusement Débit et Crédit."""
        ecriture = EcritureComptable.objects.create(
            journal=self.journal_ve,
            numero_piece="PC-2026-0004",
            date_comptable=date(2026, 10, 15),
            reference="FAC-2026-0004",
            libelle="Erreur de saisie à annuler",
            auteur=self.user,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_client,
            debit=Decimal("8400.00"),
            credit=Decimal("0.00"),
            libelle_ligne="Client TTC",
            position=1,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_vente_ia,
            debit=Decimal("0.00"),
            credit=Decimal("7000.00"),
            libelle_ligne="Vente HT",
            position=2,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_tva_fact,
            debit=Decimal("0.00"),
            credit=Decimal("1400.00"),
            libelle_ligne="TVA",
            position=3,
        )
        ecriture.valider_et_comptabiliser(user=self.user)

        # Exécution de la contre-passation
        extourne = ecriture.creer_contrepassation(user=self.user)

        # L'écriture d'origine passe à CONTREPASSE
        ecriture.refresh_from_db()
        self.assertEqual(ecriture.statut, EcritureComptable.Statut.CONTREPASSE)

        # L'écriture d'extourne est créée et comptabilisée
        self.assertEqual(extourne.statut, EcritureComptable.Statut.COMPTABILISE)
        self.assertEqual(extourne.contrepassation_de, ecriture)
        self.assertEqual(extourne.numero_piece, "EXT-PC-2026-0004")

        # Vérification de l'inversion exacte des lignes
        lignes_extourne = list(extourne.lignes.order_by("position"))
        self.assertEqual(len(lignes_extourne), 3)

        # Ligne 1 : Le client qui était au Débit 8400 se retrouve au Crédit 8400
        self.assertEqual(lignes_extourne[0].compte, self.compte_client)
        self.assertEqual(lignes_extourne[0].debit, Decimal("0.00"))
        self.assertEqual(lignes_extourne[0].credit, Decimal("8400.00"))

        # Ligne 2 : La vente qui était au Crédit 7000 se retrouve au Débit 7000
        self.assertEqual(lignes_extourne[1].compte, self.compte_vente_ia)
        self.assertEqual(lignes_extourne[1].debit, Decimal("7000.00"))
        self.assertEqual(lignes_extourne[1].credit, Decimal("0.00"))

        # L'extourne est parfaitement équilibrée
        self.assertTrue(extourne.is_equilibre)

    def test_alerte_fiscale_especes_article_11_ii_cgi(self):
        """Détection bienveillante et non bloquante du dépassement du plafond espèces de 5 000 MAD."""
        ecriture = EcritureComptable.objects.create(
            journal=self.journal_ca,
            numero_piece="PC-CA-2026-0001",
            date_comptable=date(2026, 10, 20),
            reference="REÇU-009",
            libelle="Règlement espèces important client",
            auteur=self.user,
        )
        # Encaissement en caisse de 14 924,75 MAD (comme dans l'exemple de l'utilisateur)
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_caisse,
            debit=Decimal("14924.75"),
            credit=Decimal("0.00"),
            libelle_ligne="Encaissement espèces",
            position=1,
        )
        LigneEcriture.objects.create(
            ecriture=ecriture,
            compte=self.compte_client,
            debit=Decimal("0.00"),
            credit=Decimal("14924.75"),
            libelle_ligne="Solde client espèces",
            position=2,
        )

        alertes = ecriture.alertes_fiscales()
        self.assertEqual(len(alertes), 1)
        self.assertIn("Art. 11-II du CGI Maroc", alertes[0])
        self.assertIn("14,924.75 MAD", alertes[0])
        self.assertIn("9,924.75 MAD", alertes[0])
        self.assertIn("réintégrer lors du calcul de l'Impôt sur les Sociétés", alertes[0])

        # L'alerte est non bloquante : l'écriture peut être validée
        ecriture.valider_et_comptabiliser(user=self.user)
        self.assertEqual(ecriture.statut, EcritureComptable.Statut.COMPTABILISE)


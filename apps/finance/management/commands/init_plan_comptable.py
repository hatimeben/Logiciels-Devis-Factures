from django.core.management.base import BaseCommand
from apps.finance.models import CompteComptable, JournalComptable


class Command(BaseCommand):
    help = "Initialise le Plan Comptable Général Marocain (PCGE) et les Journaux types pour BenchTech Gestion."

    def handle(self, *args, **options):
        self.stdout.write("Initialisation du Plan Comptable Marocain (PCGE)...")

        # Comptes PCGE types
        comptes_data = [
            # Classe 1 : Financement Permanent
            {
                "numero": "1111",
                "intitule": "Capital social",
                "classe": CompteComptable.Classe.CLASSE_1,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "Capital initial apporté par les fondateurs de BenchTech. Reste au crédit.",
            },
            {
                "numero": "1481",
                "intitule": "Emprunts auprès des établissements de crédit",
                "classe": CompteComptable.Classe.CLASSE_1,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "Dettes financières à plus d'un an contractées auprès de la banque.",
            },
            # Classe 2 : Actif Immobilisé
            {
                "numero": "2111",
                "intitule": "Frais de constitution",
                "classe": CompteComptable.Classe.CLASSE_2,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Frais engagés lors de la création juridique de la société.",
            },
            {
                "numero": "2321",
                "intitule": "Matériel informatique",
                "classe": CompteComptable.Classe.CLASSE_2,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Ordinateurs, serveurs, switches et équipements réseau utilisés en interne.",
            },
            {
                "numero": "2355",
                "intitule": "Matériel de bureau",
                "classe": CompteComptable.Classe.CLASSE_2,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Mobilier, imprimantes et équipements durables du siège.",
            },
            # Classe 3 : Actif Circulant hors trésorerie
            {
                "numero": "3421",
                "intitule": "Clients",
                "classe": CompteComptable.Classe.CLASSE_3,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Créances sur les clients de BenchTech. Débité lors de la facturation, crédité lors du règlement.",
            },
            {
                "numero": "3455",
                "intitule": "État - TVA récupérable sur les charges",
                "classe": CompteComptable.Classe.CLASSE_3,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "TVA payée sur les achats et prestations reçues, que l'État nous rembourse ou déduit.",
            },
            {
                "numero": "3456",
                "intitule": "État - TVA récupérable sur immobilisations",
                "classe": CompteComptable.Classe.CLASSE_3,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "TVA payée sur les investissements en matériel durable (ordinateurs, serveurs).",
            },
            # Classe 4 : Passif Circulant hors trésorerie
            {
                "numero": "4411",
                "intitule": "Fournisseurs",
                "classe": CompteComptable.Classe.CLASSE_4,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "Dettes à court terme envers nos fournisseurs. Crédité à la réception de facture, débité au paiement.",
            },
            {
                "numero": "4441",
                "intitule": "CNSS (Caisse Nationale de Sécurité Sociale)",
                "classe": CompteComptable.Classe.CLASSE_4,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "Cotisations patronales et salariales à reverser à la CNSS.",
            },
            {
                "numero": "4452",
                "intitule": "État - Impôts, taxes et assimilés (IS)",
                "classe": CompteComptable.Classe.CLASSE_4,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "Impôt sur les Sociétés (IS) et acomptes provisionnels dus à la Direction Générale des Impôts.",
            },
            {
                "numero": "4455",
                "intitule": "État - TVA facturée",
                "classe": CompteComptable.Classe.CLASSE_4,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "TVA collectée sur les devis/factures émis aux clients, à reverser à l'État.",
            },
            # Classe 5 : Trésorerie
            {
                "numero": "5141",
                "intitule": "Banque (BANK OF AFRICA - BMCE)",
                "classe": CompteComptable.Classe.CLASSE_5,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Compte courant bancaire de l'entreprise. Débité lors des encaissements, crédité lors des décaissements.",
            },
            {
                "numero": "5161",
                "intitule": "Caisse centrale (Espèces)",
                "classe": CompteComptable.Classe.CLASSE_5,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Espèces liquides en caisse. Attention : règlements limités à 5 000 MAD TTC/jour/fournisseur (CGI).",
            },
            {
                "numero": "5111",
                "intitule": "Chèques à encaisser ou à l'encaissement",
                "classe": CompteComptable.Classe.CLASSE_5,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Chèques remis par les clients en attente de compensation bancaire.",
            },
            # Classe 6 : Charges
            {
                "numero": "6111",
                "intitule": "Achats de marchandises (Matériel informatique revendu)",
                "classe": CompteComptable.Classe.CLASSE_6,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Coût d'achat des PC, écrans, serveurs et pièces destinés à être revendus aux clients.",
            },
            {
                "numero": "6121",
                "intitule": "Achats de matières et fournitures consommables",
                "classe": CompteComptable.Classe.CLASSE_6,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Petites fournitures, câbles, clés USB, papier, consommables bureau.",
            },
            {
                "numero": "6131",
                "intitule": "Locations et charges locatives",
                "classe": CompteComptable.Classe.CLASSE_6,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Loyer du bureau BenchTech, charges de copropriété.",
            },
            {
                "numero": "6141",
                "intitule": "Frais de télécommunications & Internet",
                "classe": CompteComptable.Classe.CLASSE_6,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Abonnements fibre optique, forfaits mobiles professionnels, hébergement cloud.",
            },
            {
                "numero": "6147",
                "intitule": "Services bancaires et commissions",
                "classe": CompteComptable.Classe.CLASSE_6,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Frais de tenue de compte BOA, commissions de virements, frais d'effets.",
            },
            {
                "numero": "6171",
                "intitule": "Rémunérations du personnel",
                "classe": CompteComptable.Classe.CLASSE_6,
                "sens_normal": CompteComptable.SensNormal.DEBIT,
                "description_pedagogique": "Salaires bruts des collaborateurs et consultants salariés.",
            },
            # Classe 7 : Produits (Chiffre d'Affaires par pôle d'activité BenchTech)
            {
                "numero": "7111",
                "intitule": "Ventes de marchandises (Matériel informatique)",
                "classe": CompteComptable.Classe.CLASSE_7,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "Chiffre d'affaires hors taxes généré par le Pôle Vente de matériel informatique.",
            },
            {
                "numero": "7124",
                "intitule": "Ventes de services (Conseil en technologies & IA)",
                "classe": CompteComptable.Classe.CLASSE_7,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "Chiffre d'affaires hors taxes généré par le Pôle Conseil en technologies & Intelligence Artificielle.",
            },
            {
                "numero": "7125",
                "intitule": "Ventes de prestations (Formation professionnelle en informatique)",
                "classe": CompteComptable.Classe.CLASSE_7,
                "sens_normal": CompteComptable.SensNormal.CREDIT,
                "description_pedagogique": "Chiffre d'affaires hors taxes généré par le Pôle Formation professionnelle.",
            },
        ]

        comptes_crees = 0
        for item in comptes_data:
            compte, created = CompteComptable.objects.get_or_create(
                numero=item["numero"],
                defaults=item,
            )
            if created:
                comptes_crees += 1

        self.stdout.write(self.style.SUCCESS(f"{comptes_crees} comptes PCGE créés (sur {len(comptes_data)})."))

        # Journaux comptables standards
        compte_bq = CompteComptable.objects.filter(numero="5141").first()
        compte_ca = CompteComptable.objects.filter(numero="5161").first()

        journaux_data = [
            {
                "code": "VE",
                "intitule": "Journal des Ventes (Conseil, Formation, Matériel)",
                "type_journal": JournalComptable.TypeJournal.VENTES,
                "compte_contrepartie": None,
                "description_pedagogique": (
                    "Enregistre toutes les factures émises aux clients.\n"
                    "• Débit : Compte Client (3421) pour le montant TTC\n"
                    "• Crédit : Compte de Produits (7111 / 7124 / 7125) pour le HT\n"
                    "• Crédit : TVA facturée (4455) pour le montant de TVA."
                ),
            },
            {
                "code": "AC",
                "intitule": "Journal des Achats (Fournisseurs & Matériel)",
                "type_journal": JournalComptable.TypeJournal.ACHATS,
                "compte_contrepartie": None,
                "description_pedagogique": (
                    "Enregistre toutes les factures de charges et fournitures reçues.\n"
                    "• Débit : Compte de Charges (6111 / 6121...) pour le montant HT\n"
                    "• Débit : TVA récupérable (3455) pour le montant de TVA\n"
                    "• Crédit : Compte Fournisseur (4411) pour le montant TTC."
                ),
            },
            {
                "code": "BQ",
                "intitule": "Journal de Banque (BANK OF AFRICA - BMCE)",
                "type_journal": JournalComptable.TypeJournal.TRESORERIE,
                "compte_contrepartie": compte_bq,
                "description_pedagogique": (
                    "Enregistre tous les encaissements et décaissements sur le compte BMCE/BOA.\n"
                    "• Encaissement client : Débit 5141 (Banque) / Crédit 3421 (Client)\n"
                    "• Règlement fournisseur : Débit 4411 (Fournisseur) / Crédit 5141 (Banque)."
                ),
            },
            {
                "code": "CA",
                "intitule": "Journal de Caisse (Espèces)",
                "type_journal": JournalComptable.TypeJournal.TRESORERIE,
                "compte_contrepartie": compte_ca,
                "description_pedagogique": (
                    "Enregistre les règlements et menues dépenses réglés en liquide.\n"
                    "Rappel fiscal Art. 11-II CGI : Déductibilité plafonnée à 5 000 MAD TTC/jour/fournisseur."
                ),
            },
            {
                "code": "OD",
                "intitule": "Journal des Opérations Diverses",
                "type_journal": JournalComptable.TypeJournal.OPERATIONS_DIVERSES,
                "compte_contrepartie": None,
                "description_pedagogique": (
                    "Utilisé pour les salaires CNSS, la déclaration mensuelle/trimestrielle de TVA, "
                    "les amortissements et les écritures de régularisation d'inventaire."
                ),
            },
        ]

        journaux_crees = 0
        for item in journaux_data:
            journal, created = JournalComptable.objects.get_or_create(
                code=item["code"],
                defaults=item,
            )
            if created:
                journaux_crees += 1

        self.stdout.write(self.style.SUCCESS(f"{journaux_crees} journaux créés (sur {len(journaux_data)})."))
        self.stdout.write(self.style.SUCCESS("Plan Comptable Marocain & Journaux configurés avec succès !"))


# BENCHTECH Gestion

Socle de l'application locale de gestion, basé sur Django et SQLite.

## Prérequis

- Windows
- Python 3.11 ou version compatible avec Django 5.2

## Installation locale

Depuis PowerShell, à la racine du projet :

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

L'accueil `http://127.0.0.1:8000/` propose un tableau de bord complet avec le suivi
du chiffre d'affaires, des encaissements, des créances en retard, du solde de trésorerie
net et du pipeline commercial. L'administration reste disponible sur `/admin/`.
Le module Devis (`/devis/`) permet de créer, modifier et consulter les brouillons,
puis d'émettre un devis numéroté, d'en suivre le statut, de créer une révision et
de télécharger son PDF. Les factures (`/factures/`) peuvent être issues d'un devis
ou créées directement, avec édition des lignes, émission numérotée, PDF et
saisie des règlements clients. Le module Dépenses (`/depenses/`) permet d'enregistrer
les achats et charges avec leurs justificatifs et d'en suivre les règlements fournisseurs.

Pour essayer les devis, créez depuis l'administration un client, au moins une
prestation avec un prix HT et, si nécessaire, les taux de taxe applicables.
Attribuez à l'utilisateur les permissions `finance.view_quote`,
`finance.add_quote` et `finance.change_quote` depuis l'administration, puis
connectez-vous sur `http://127.0.0.1:8000/connexion/` avant d'ouvrir
`http://127.0.0.1:8000/devis/`. Les factures sont créées depuis un devis accepté :
ouvrez son détail et choisissez « Convertir en facture ». Vérifiez l'échéance et
les notes du brouillon, puis émettez la facture. Les paramètres de numérotation
DEV et FAC (préfixe, séparateur, année et largeur de séquence) sont
administrables ; validez les règles fiscales et de numérotation avant toute
émission de documents réels.

## Tests et vérification du schéma

```powershell
python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
```

## Paramètres métier et fichiers

La langue est configurée en français, le fuseau horaire sur `Africa/Casablanca`,
la saisie des dates est configurée en `JJ/MM/AAAA`, et la devise de référence sur
MAD. Aucun taux de taxe n'est préchargé. Les prix catalogue sont des prix de vente
HT ; un prix doit être renseigné lors de la création d'un service depuis
l'administration.
Les taux appliqués aux lignes de devis et factures disposent d'un instantané
distinct du taux de référence pour préserver l'historique. Les conversions
copient les désignations, prix, quantités, remises, taux et coordonnées client du
devis accepté. Les devis et factures émis sont protégés contre les modifications
silencieuses. Les règlements partiels ou totaux imputés recalculent en temps
réel le montant déjà payé, le reste à payer et ajustent le statut de la facture.
Lorsqu'un seul taux de taxe est actif, il est présélectionné sur les nouvelles
lignes de devis ; si plusieurs taux sont actifs, aucun n'est choisi à la place
de l'utilisateur. La désignation et le prix HT du catalogue sont proposés lors
du choix d'une prestation et restent modifiables sur le devis.

Les justificatifs sont stockés sous `media/justificatifs/`. Ils ne sont pas
exposés par une route publique dans ce socle. L'accès contrôlé, la sauvegarde et
la restauration seront traités dans une phase ultérieure.

## Référentiels et dépenses disponibles

Les clients, fournisseurs, catégories de dépenses, catégories de services,
services et projets se gèrent dans l'administration Django. Une dépense peut
être associée à un fournisseur, une catégorie, un projet et un justificatif.
Les paiements de dépense sont enregistrés séparément, en MAD, et le total payé,
le solde restant et le statut de paiement sont calculés depuis ces écritures.
Les paiements sont positifs, ne peuvent pas dépasser le montant de la dépense,
et ne sont pas modifiables ou supprimables depuis l'administration. Les
modifications de dépenses et les paiements y sont historisés avec l'utilisateur.
Les clés d'idempotence du formulaire de paiement empêchent de créer deux fois un
paiement lors de la répétition de la même soumission. Des déclencheurs SQLite
protègent aussi le plafond cumulé, l'immuabilité des paiements et la cohérence
d'une réduction du montant de la dépense, y compris si une écriture contourne
les validations des modèles.

Les montants utilisent deux décimales et les arrondis monétaires suivent
`ROUND_HALF_UP` au centime ; les tests correspondants sont dans
`apps/finance/tests.py`. Aucune conversion fiscale ou calcul de TVA n'est
implémenté à cette étape.

Les remboursements et corrections de paiement ne sont pas encore pris en charge.
Il n'existe pas encore de mécanisme d'écriture compensatrice ; ne pas corriger un
paiement en le supprimant ou en le modifiant directement dans la base. Ce flux
devra être défini avant la mise en production des opérations financières.
Les clés anti-doublon devront être propagées par toute future API ou interface
qui retente une soumission après une erreur réseau.

L'historique est en lecture seule depuis l'administration pour tous les rôles.
Les instantanés conservent le nom d'utilisateur afin de préserver l'identité
auditable lorsqu'un compte est supprimé ; la désactivation d'un compte laisse
également l'association à l'historique intacte. Les garde-fous SQL spécifiques
aux paiements sont installés par les migrations SQLite.

Le socle comprend les modèles relationnels et les contraintes structurelles.
Les services applicatifs chargés de l'affectation des paiements clients, du calcul
des soldes, des retards et des corrections par écritures compensatrices restent à
réaliser dans les phases suivantes.
La clé Django de repli est réservée au développement local ; toute utilisation
autre que locale doit définir `DJANGO_SECRET_KEY` et `DJANGO_DEBUG=0`.

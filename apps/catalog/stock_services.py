from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from .models import Article, MouvementStock


def get_or_create_cash_customer():
    """
    Retourne ou crée le client par défaut pour les ventes au comptant / particuliers,
    conforme aux exigences de traçabilité de la DGI marocaine (dispense d'ICE).
    """
    from apps.partners.models import Customer
    customer, _ = Customer.objects.get_or_create(
        name="Client Divers / Vente au Comptant",
        defaults={
            "city": "Casablanca",
            "country": "Maroc",
            "notes": "Compte de passage pour facturation au comptant et particuliers (conforme règles DGI Maroc).",
        },
    )
    return customer


def enregistrer_mouvement_stock(
    article_id: int,
    type_mouvement: str,
    quantite: Decimal,
    reference_document: str = "",
    motif: str = "",
    fournisseur=None,
    prix_unitaire_ht: Decimal = None,
    mettre_a_jour_prix_achat: bool = False,
    auteur=None,
    autoriser_stock_negatif: bool = False,
) -> MouvementStock:
    """
    Enregistre un mouvement de stock de manière atomique et met à jour le stock actuel.
    Garantit l'intégrité et la cohérence de l'historique sous SQLite.
    """
    quantite = Decimal(str(quantite))
    if quantite <= Decimal("0.00"):
        raise ValidationError("La quantité du mouvement doit être strictement positive.")

    prix_unitaire_ht = Decimal(str(prix_unitaire_ht)) if prix_unitaire_ht is not None else Decimal("0.00")

    with transaction.atomic():
        article = Article.objects.select_for_update().get(pk=article_id)
        stock_initial = article.stock_actuel

        if type_mouvement == MouvementStock.TypeMouvement.ENTREE:
            nouveau_stock = stock_initial + quantite
            update_data = {"stock_actuel": F("stock_actuel") + quantite}
            if mettre_a_jour_prix_achat and prix_unitaire_ht > Decimal("0.00"):
                article.prix_achat_ht = prix_unitaire_ht
                article.save(update_fields=["prix_achat_ht", "updated_at"])
            Article.objects.filter(pk=article.pk).update(**update_data)

        elif type_mouvement == MouvementStock.TypeMouvement.SORTIE:
            if not autoriser_stock_negatif and stock_initial < quantite:
                raise ValidationError(
                    f"Stock insuffisant pour l'article '{article.reference}' "
                    f"(Disponible: {stock_initial}, Demandé: {quantite})."
                )
            nouveau_stock = stock_initial - quantite
            Article.objects.filter(pk=article.pk).update(stock_actuel=F("stock_actuel") - quantite)

        elif type_mouvement == MouvementStock.TypeMouvement.AJUSTEMENT:
            # Pour l'ajustement d'inventaire, 'quantite' représente la valeur cible du nouveau stock
            nouveau_stock = quantite
            Article.objects.filter(pk=article.pk).update(stock_actuel=quantite)

        else:
            raise ValidationError(f"Type de mouvement inconnu : {type_mouvement}")

        mouvement = MouvementStock.objects.create(
            article=article,
            type_mouvement=type_mouvement,
            quantite=quantite,
            stock_avant=stock_initial,
            stock_apres=nouveau_stock,
            reference_document=reference_document,
            fournisseur=fournisseur,
            prix_unitaire_ht=prix_unitaire_ht if prix_unitaire_ht > 0 else (article.prix_achat_ht if type_mouvement == MouvementStock.TypeMouvement.ENTREE else article.prix_vente_ht),
            motif=motif,
            auteur=auteur,
        )
        return mouvement


def decrementer_stock_depuis_facture(invoice, actor=None, autoriser_stock_negatif: bool = False):
    """
    Décrémente le stock pour toutes les lignes de la facture liées à un article.
    Garantit l'idempotence : ne traite jamais deux fois la même facture.
    """
    from apps.finance.models import Invoice

    if invoice.status != Invoice.Status.ISSUED:
        return

    # Vérification anti-doublon (idempotence)
    doc_ref = invoice.number or f"Facture #{invoice.pk}"
    deja_traite = MouvementStock.objects.filter(
        reference_document=doc_ref,
        type_mouvement=MouvementStock.TypeMouvement.SORTIE,
    ).exists()

    if deja_traite:
        return

    lignes_stock = invoice.lines.filter(article__isnull=False).select_related("article")
    if not lignes_stock.exists():
        return

    with transaction.atomic():
        # Phase 1 : Contrôle préalable des disponibilités
        if not autoriser_stock_negatif:
            for line in lignes_stock:
                if line.article.stock_actuel < line.quantity:
                    raise ValidationError(
                        f"Impossible d'émettre la facture {invoice.number} : "
                        f"stock insuffisant pour '{line.article.designation}' "
                        f"(disponible: {line.article.stock_actuel}, requis: {line.quantity})."
                    )

        # Phase 2 : Enregistrement des sorties
        for line in lignes_stock:
            enregistrer_mouvement_stock(
                article_id=line.article_id,
                type_mouvement=MouvementStock.TypeMouvement.SORTIE,
                quantite=line.quantity,
                reference_document=doc_ref,
                prix_unitaire_ht=line.unit_price,
                motif=f"Facture client {doc_ref}",
                auteur=actor,
                autoriser_stock_negatif=autoriser_stock_negatif,
            )


def reintegrer_stock_depuis_annulation_facture(invoice, actor=None, motif="Annulation de facture"):
    """
    Réintègre le stock (mouvements d'ENTRÉE) pour tous les articles de la facture annulée.
    Idempotent : vérifie qu'aucun mouvement d'entrée d'annulation n'a déjà eu lieu pour cette facture.
    """
    doc_ref = invoice.number or f"Facture #{invoice.pk}"
    ref_annulation = f"Annulation {doc_ref}"

    deja_reintegre = MouvementStock.objects.filter(
        reference_document=ref_annulation,
        type_mouvement=MouvementStock.TypeMouvement.ENTREE,
    ).exists()
    if deja_reintegre:
        return

    lignes_stock = invoice.lines.filter(article__isnull=False).select_related("article")
    if not lignes_stock.exists():
        return

    with transaction.atomic():
        for line in lignes_stock:
            enregistrer_mouvement_stock(
                article_id=line.article_id,
                type_mouvement=MouvementStock.TypeMouvement.ENTREE,
                quantite=line.quantity,
                reference_document=ref_annulation,
                prix_unitaire_ht=line.unit_price,
                motif=f"Restitution suite annulation {doc_ref} : {motif}",
                auteur=actor,
            )


def reintegrer_stock_depuis_avoir(credit_note, actor=None):
    """
    Réintègre le stock (mouvements d'ENTRÉE) pour tous les articles retournés figurant sur l'avoir émis.
    Idempotent : vérifie qu'aucun mouvement d'entrée n'a déjà eu lieu pour cet avoir.
    """
    from apps.finance.models import CreditNote
    if credit_note.status != CreditNote.Status.ISSUED:
        return

    doc_ref = credit_note.number or f"Avoir #{credit_note.pk}"
    deja_reintegre = MouvementStock.objects.filter(
        reference_document=doc_ref,
        type_mouvement=MouvementStock.TypeMouvement.ENTREE,
    ).exists()
    if deja_reintegre:
        return

    lignes_stock = credit_note.lines.filter(article__isnull=False).select_related("article")
    if not lignes_stock.exists():
        return

    with transaction.atomic():
        for line in lignes_stock:
            enregistrer_mouvement_stock(
                article_id=line.article_id,
                type_mouvement=MouvementStock.TypeMouvement.ENTREE,
                quantite=line.quantity,
                reference_document=doc_ref,
                prix_unitaire_ht=line.unit_price,
                motif=f"Retour client sur avoir {doc_ref} (Facture {credit_note.invoice.number}) : {credit_note.get_reason_display()}",
                auteur=actor,
            )

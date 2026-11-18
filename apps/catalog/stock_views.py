import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.db.models import F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.finance.models import Invoice
from .models import Article, MouvementStock
from .stock_forms import ArticleForm, StockReceptionForm
from .stock_services import (
    enregistrer_mouvement_stock,
    get_or_create_cash_customer,
)


@login_required
def stock_dashboard(request):
    """
    Tableau de bord visuel des stocks inspiré d'Odoo et Zoho Inventory.
    Affiche les KPIs clés (valeur totale, alertes, ruptures) et la liste des marchandises.
    """
    articles_qs = Article.objects.filter(is_active=True)

    # Recherche
    q = request.GET.get("q", "").strip()
    if q:
        articles_qs = articles_qs.filter(
            Q(reference__icontains=q)
            | Q(designation__icontains=q)
            | Q(description__icontains=q)
        )

    # Filtrage par état de stock
    status_filter = request.GET.get("status", "all")
    if status_filter == "alert":
        articles_qs = articles_qs.filter(stock_actuel__lte=F("stock_alerte"), stock_actuel__gt=Decimal("0.00"))
    elif status_filter == "out_of_stock":
        articles_qs = articles_qs.filter(stock_actuel__lte=Decimal("0.00"))
    elif status_filter == "in_stock":
        articles_qs = articles_qs.filter(stock_actuel__gt=F("stock_alerte"))

    # Calcul des KPIs décisionnels sur l'ensemble du parc actif
    all_articles = Article.objects.filter(is_active=True)
    total_articles = all_articles.count()
    articles_rupture_count = all_articles.filter(stock_actuel__lte=Decimal("0.00")).count()
    articles_alerte_count = all_articles.filter(
        stock_actuel__lte=F("stock_alerte"), stock_actuel__gt=Decimal("0.00")
    ).count()

    # Valeur totale du stock physique (au coût d'achat unitaire HT)
    valeur_totale_stock = Decimal("0.00")
    for art in all_articles:
        valeur_totale_stock += art.valeur_stock_ht

    articles_list = list(articles_qs.order_by("reference"))

    return render(
        request,
        "finance/stocks/dashboard.html",
        {
            "articles": articles_list,
            "total_articles": total_articles,
            "valeur_totale_stock": valeur_totale_stock,
            "articles_alerte_count": articles_alerte_count,
            "articles_rupture_count": articles_rupture_count,
            "q": q,
            "status_filter": status_filter,
        },
    )


@login_required
def stock_reception(request):
    """
    Formulaire rapide de réception de marchandises (Entrée de stock)
    accessible aux collaborateurs sans passer par le panneau /admin/.
    """
    initial_data = {}
    article_id = request.GET.get("article_id")
    if article_id:
        article = get_object_or_404(Article, pk=article_id, is_active=True)
        initial_data["article"] = article
        initial_data["unit_price_ht"] = article.prix_achat_ht

    if request.method == "POST":
        form = StockReceptionForm(request.POST)
        if form.is_valid():
            article = form.cleaned_data["article"]
            supplier = form.cleaned_data.get("supplier")
            supplier_name = form.cleaned_data.get("supplier_name", "").strip()
            quantity = form.cleaned_data["quantity"]
            unit_price_ht = form.cleaned_data.get("unit_price_ht")
            ref_doc = form.cleaned_data["reference_document"]
            update_cost = form.cleaned_data.get("update_cost_price", False)
            notes = form.cleaned_data.get("notes", "")

            motif_parts = []
            if supplier:
                motif_parts.append(f"Fournisseur : {supplier.name}")
            elif supplier_name:
                motif_parts.append(f"Fournisseur : {supplier_name}")
            if notes:
                motif_parts.append(notes)
            motif = " | ".join(motif_parts) if motif_parts else "Réception de stock fournisseur"

            try:
                mvt = enregistrer_mouvement_stock(
                    article_id=article.pk,
                    type_mouvement=MouvementStock.TypeMouvement.ENTREE,
                    quantite=quantity,
                    reference_document=ref_doc,
                    motif=motif,
                    fournisseur=supplier,
                    prix_unitaire_ht=unit_price_ht,
                    mettre_a_jour_prix_achat=update_cost,
                    auteur=request.user,
                )
                messages.success(
                    request,
                    f"Réception validée : +{quantity} {article.designation} enregistrés. "
                    f"Stock actuel : {mvt.stock_apres}.",
                )
                return redirect("stock-dashboard")
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
    else:
        form = StockReceptionForm(initial=initial_data)

    return render(
        request,
        "finance/stocks/reception.html",
        {
            "form": form,
            "selected_article_id": article_id,
        },
    )


@login_required
def article_create(request):
    """
    Création d'un nouvel article / marchandise stockable (PCGE 3111).
    """
    if request.method == "POST":
        form = ArticleForm(request.POST)
        if form.is_valid():
            article = form.save(commit=False)
            initial_stock = form.cleaned_data.get("initial_stock") or Decimal("0.00")
            article.stock_actuel = Decimal("0.00")  # Sera alimenté par le mouvement d'entrée
            article.save()

            if initial_stock > Decimal("0.00"):
                enregistrer_mouvement_stock(
                    article_id=article.pk,
                    type_mouvement=MouvementStock.TypeMouvement.ENTREE,
                    quantite=initial_stock,
                    reference_document="INV-INITIAL",
                    motif="Stock physique initial à la création de la fiche",
                    prix_unitaire_ht=article.prix_achat_ht,
                    auteur=request.user,
                )

            messages.success(
                request,
                f"L'article {article.reference} ({article.designation}) a été créé avec succès.",
            )
            return redirect("stock-dashboard")
    else:
        form = ArticleForm()

    return render(
        request,
        "finance/stocks/article_form.html",
        {
            "form": form,
            "title": "Créer un nouvel article de stock",
        },
    )


@login_required
def stock_movements_journal(request):
    """
    Journal des mouvements de stock (Traçabilité complète style Sage 100).
    """
    movements_qs = MouvementStock.objects.select_related("article", "fournisseur", "auteur").all()

    type_filter = request.GET.get("type", "")
    if type_filter in MouvementStock.TypeMouvement.values:
        movements_qs = movements_qs.filter(type_mouvement=type_filter)

    article_id = request.GET.get("article_id", "")
    article_obj = None
    if article_id:
        movements_qs = movements_qs.filter(article_id=article_id)
        article_obj = Article.objects.filter(pk=article_id).first()

    q = request.GET.get("q", "").strip()
    if q:
        movements_qs = movements_qs.filter(
            Q(reference_document__icontains=q)
            | Q(article__reference__icontains=q)
            | Q(article__designation__icontains=q)
            | Q(motif__icontains=q)
        )

    movements = movements_qs[:200]

    return render(
        request,
        "finance/stocks/movements.html",
        {
            "movements": movements,
            "type_filter": type_filter,
            "article_obj": article_obj,
            "q": q,
            "types": MouvementStock.TypeMouvement.choices,
        },
    )


@login_required
def stock_export_movements_csv(request):
    """
    Export CSV du journal des mouvements de stock pour audit et fiduciaire.
    """
    movements = MouvementStock.objects.select_related("article", "fournisseur", "auteur").order_by("-date_mouvement")[:1000]

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = 'attachment; filename="Journal_Mouvements_Stock.csv"'

    writer = csv.writer(response, delimiter=";")
    writer.writerow([
        "ID Mouvement",
        "Date & Heure",
        "Type",
        "Réf Article",
        "Désignation",
        "Compte Stock PCGE",
        "Quantité",
        "Prix Unitaire HT (MAD)",
        "Stock Avant",
        "Stock Après",
        "Document Source",
        "Fournisseur / Tiers",
        "Motif",
        "Opérateur",
    ])

    for m in movements:
        writer.writerow([
            m.pk,
            m.date_mouvement.strftime("%d/%m/%Y %H:%M:%S"),
            m.get_type_mouvement_display(),
            m.article.reference,
            m.article.designation,
            m.article.compte_stock,
            f"{m.quantite:.2f}",
            f"{m.prix_unitaire_ht:.2f}",
            f"{m.stock_avant:.2f}",
            f"{m.stock_apres:.2f}",
            m.reference_document or "",
            m.fournisseur.name if m.fournisseur else "",
            m.motif or "",
            m.auteur.get_username() if m.auteur else "",
        ])

    return response


@login_required
def quick_cash_sale(request):
    """
    Vente Rapide au Comptant (Traçabilité DGI Maroc) :
    Initialise une facture au nom du client par défaut 'Client Divers / Vente au Comptant'
    et redirige immédiatement vers l'ajout d'articles en stock.
    """
    customer = get_or_create_cash_customer()
    invoice = Invoice.objects.create(
        customer=customer,
        notes="Vente au comptant - Particulier / Client de passage (Dispense d'ICE)",
    )
    messages.info(
        request,
        f"Facture comptant brouillon #{invoice.pk} créée. Ajoutez vos articles pour validation et décompte du stock.",
    )
    return redirect("invoice-edit", invoice_id=invoice.pk)


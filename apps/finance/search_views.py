from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse

from apps.catalog.models import Article, Service
from apps.partners.models import Customer
from .models import CreditNote, Invoice, Quote
from .quote_calculations import calculate_quote_totals


@login_required
def global_search_api(request):
    """
    Endpoint JSON pour la recherche globale rapide (Ctrl + K).
    Recherche instantanément dans :
    - Clients (nom, ville, téléphone, ICE)
    - Factures (numéro, nom client, prestation)
    - Devis (numéro, nom client, prestation)
    - Articles de stock (désignation, référence)
    - Prestations / Services du catalogue
    """
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": [], "query": query, "count": 0})

    results = []

    # 1. Clients
    customers = Customer.objects.filter(
        Q(name__icontains=query)
        | Q(city__icontains=query)
        | Q(phone__icontains=query)
        | Q(ice__icontains=query)
        | Q(tax_identifier__icontains=query)
    )[:5]
    for c in customers:
        subtitle_parts = []
        if c.city:
            subtitle_parts.append(c.city)
        if c.ice:
            subtitle_parts.append(f"ICE: {c.ice}")
        if c.phone:
            subtitle_parts.append(c.phone)
        subtitle = " · ".join(subtitle_parts) if subtitle_parts else "Client actif"

        results.append({
            "category": "Clients",
            "type": "client",
            "icon": "👤",
            "badge": "Client",
            "badge_class": "badge-client",
            "title": c.name,
            "subtitle": subtitle,
            "url": f"{reverse('invoice-list')}?customer={c.pk}",
        })

    # 2. Factures
    invoices = Invoice.objects.filter(
        Q(number__icontains=query)
        | Q(customer__name__icontains=query)
        | Q(lines__description__icontains=query)
    ).select_related("customer").prefetch_related("lines").distinct()[:5]
    for inv in invoices:
        totals = calculate_quote_totals(inv.lines.all())
        num_str = inv.number or f"Brouillon #{inv.pk}"
        status_disp = inv.get_status_display()
        results.append({
            "category": "Factures",
            "type": "facture",
            "icon": "▧",
            "badge": f"Facture · {status_disp}",
            "badge_class": f"badge-status badge-inv-{inv.status}",
            "title": f"{num_str} — {inv.customer.name}",
            "subtitle": f"{totals['total']} MAD TTC · Émise le {inv.issue_date.strftime('%d/%m/%Y') if inv.issue_date else 'Brouillon'}",
            "url": reverse("invoice-detail", args=[inv.pk]),
        })

    # 3. Devis
    quotes = Quote.objects.filter(
        Q(number__icontains=query)
        | Q(customer__name__icontains=query)
        | Q(lines__description__icontains=query)
    ).select_related("customer").prefetch_related("lines").distinct()[:5]
    for q in quotes:
        totals = calculate_quote_totals(q.lines.all())
        num_str = q.number or f"Brouillon #{q.pk}"
        status_disp = q.get_status_display()
        results.append({
            "category": "Devis",
            "type": "devis",
            "icon": "▤",
            "badge": f"Devis · {status_disp}",
            "badge_class": f"badge-status badge-quote-{q.status}",
            "title": f"{num_str} — {q.customer.name}",
            "subtitle": f"{totals['net']} MAD HT · {status_disp}",
            "url": reverse("quote-detail", args=[q.pk]),
        })

    # 3b. Avoirs (Notes de crédit)
    credit_notes = CreditNote.objects.filter(
        Q(number__icontains=query)
        | Q(customer__name__icontains=query)
        | Q(invoice__number__icontains=query)
        | Q(reason__icontains=query)
    ).select_related("customer", "invoice")[:5]
    for cn in credit_notes:
        num_str = cn.number or f"Avoir Brouillon #{cn.pk}"
        status_disp = cn.get_status_display()
        ref_inv = f"Sur facture {cn.invoice.number or cn.invoice.pk}"
        results.append({
            "category": "Avoirs & Retours",
            "type": "avoir",
            "icon": "↩️",
            "badge": f"Avoir · {status_disp}",
            "badge_class": f"badge-status badge-cn-{cn.status}",
            "title": f"{num_str} — {cn.customer.name}",
            "subtitle": f"{cn.total_ttc} MAD TTC · {ref_inv} · {cn.get_reason_display()}",
            "url": reverse("credit-note-detail", args=[cn.pk]),
        })

    # 4. Articles de stock
    articles = Article.objects.filter(
        Q(designation__icontains=query)
        | Q(reference__icontains=query)
        | Q(description__icontains=query)
    )[:5]
    for a in articles:
        ref_txt = f"Réf: {a.reference}" if a.reference else "Sans réf"
        results.append({
            "category": "Stock & Matériel",
            "type": "stock",
            "icon": "📦",
            "badge": f"Stock : {int(a.stock_actuel)}",
            "badge_class": "badge-stock",
            "title": a.designation,
            "subtitle": f"{ref_txt} · Prix: {a.prix_vente_ht} MAD HT · Stock disponible: {int(a.stock_actuel)}",
            "url": reverse("stock-dashboard"),
        })

    # 5. Prestations & Services
    services = Service.objects.filter(
        Q(name__icontains=query)
        | Q(category__name__icontains=query)
    ).select_related("category")[:4]
    for s in services:
        cat_name = s.category.name if s.category else "Service général"
        results.append({
            "category": "Catalogue Prestations",
            "type": "service",
            "icon": "🛠️",
            "badge": cat_name,
            "badge_class": "badge-service",
            "title": s.name,
            "subtitle": f"Tarif indicatif : {s.default_unit_price} MAD HT",
            "url": reverse("quote-create"),
        })

    return JsonResponse({
        "results": results,
        "count": len(results),
        "query": query,
    })

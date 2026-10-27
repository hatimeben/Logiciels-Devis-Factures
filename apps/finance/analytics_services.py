"""
Module décisionnel et d'analyse Business Intelligence (BI) pour BenchTech Gestion.
Fournit le calcul en temps réel des KPIs exécutifs :
1. Chiffre d'Affaires par Ligne d'Activité (Conseil/IA, Formation, Matériel).
2. Taux de Conversion Commercial & Pipeline des Devis.
3. Trésorerie, Recouvrement & Encours Clients (Balance âgée des créances échues).
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db.models import Sum
from django.utils import timezone

from .models import Invoice, InvoiceLine, Payment, Quote
from .money import round_money
from .quote_calculations import calculate_quote_line, calculate_quote_totals

ZERO = Decimal("0.00")

# Définition des 3 Pôles Stratégiques BenchTech
POLE_CONSEIL_IA = {
    "key": "conseil_ia",
    "name": "Conseil en technologies & IA",
    "short_name": "Conseil & IA",
    "color": "#1B2A4A",      # Bleu Nuit impérial BenchTech
    "accent": "#2B4374",
    "bg_tint": "#F1F5F9",
    "icon": "🧠",
}

POLE_FORMATION = {
    "key": "formation",
    "name": "Formation professionnelle en informatique",
    "short_name": "Formation pro",
    "color": "#D4AF37",      # Or Impérial BenchTech
    "accent": "#C59B27",
    "bg_tint": "#FAF5E8",
    "icon": "🎓",
}

POLE_MATERIEL = {
    "key": "materiel",
    "name": "Vente de matériel informatique",
    "short_name": "Matériel IT",
    "color": "#2563EB",      # Bleu Royal IT
    "accent": "#1D4ED8",
    "bg_tint": "#EFF6FF",
    "icon": "🖥️",
}


def classify_activity_pole(
    service_category_name: Optional[str] = None,
    service_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Règle décisionnelle de classification automatique d'une prestation
    parmi les trois pôles stratégiques de BenchTech.
    """
    corpus = " ".join(
        filter(None, [service_category_name, service_name, description])
    ).lower()

    # 1. Formation professionnelle
    formation_markers = [
        "formation", "atelier", "workshop", "coaching", "training",
        "cours", "séminaire", "seminaire", "certif", "pédagogique", "pedagogique"
    ]
    if any(marker in corpus for marker in formation_markers):
        return POLE_FORMATION

    # 2. Vente de matériel informatique
    hardware_markers = [
        "matériel", "materiel", "équipement", "equipement", "ordinateur", "serveur",
        "switch", "routeur", "écran", "ecran", "pc", "laptop", "imprimante", "hardware",
        "disque", "ram", "rack", "onduleur", "câble", "cable", "périphérique", "peripherique"
    ]
    if any(marker in corpus for marker in hardware_markers):
        return POLE_MATERIEL

    # 3. Conseil en technologies & IA (cœur de métier / défaut pour prestations d'ingénierie, dev, cloud, IA)
    return POLE_CONSEIL_IA


def get_revenue_by_activity() -> Dict[str, Any]:
    """
    1. Suivi du Chiffre d'Affaires par Ligne d'Activité :
       Ventilation dynamique et agrégation en temps réel du CA réalisé et facturé
       entre les trois pôles stratégiques de BenchTech.
    """
    # Factures émises et non annulées (CA effectif)
    invoices = (
        Invoice.objects.exclude(status__in=[Invoice.Status.DRAFT, Invoice.Status.CANCELLED])
        .prefetch_related("lines__service__category")
    )

    poles_data = {
        POLE_CONSEIL_IA["key"]: {
            **POLE_CONSEIL_IA,
            "net_ht": ZERO,
            "total_ttc": ZERO,
            "count": 0,
            "percentage": 0.0,
            "avg_ticket": ZERO,
        },
        POLE_FORMATION["key"]: {
            **POLE_FORMATION,
            "net_ht": ZERO,
            "total_ttc": ZERO,
            "count": 0,
            "percentage": 0.0,
            "avg_ticket": ZERO,
        },
        POLE_MATERIEL["key"]: {
            **POLE_MATERIEL,
            "net_ht": ZERO,
            "total_ttc": ZERO,
            "count": 0,
            "percentage": 0.0,
            "avg_ticket": ZERO,
        },
    }

    total_net_ht = ZERO
    total_ttc = ZERO

    for invoice in invoices:
        for line in invoice.lines.all():
            amounts = calculate_quote_line(
                line.quantity,
                line.unit_price,
                line.discount_type,
                line.discount_value,
                line.tax_percentage,
            )
            cat_name = (
                line.service.category.name
                if (line.service and line.service.category)
                else ""
            )
            srv_name = line.service.name if line.service else ""
            pole = classify_activity_pole(cat_name, srv_name, line.description)
            key = pole["key"]

            poles_data[key]["net_ht"] += amounts["net"]
            poles_data[key]["total_ttc"] += amounts["total"]
            poles_data[key]["count"] += 1
            total_net_ht += amounts["net"]
            total_ttc += amounts["total"]

    poles_list: List[Dict[str, Any]] = []
    for key in [POLE_CONSEIL_IA["key"], POLE_FORMATION["key"], POLE_MATERIEL["key"]]:
        item = poles_data[key]
        item["net_ht"] = round_money(item["net_ht"])
        item["total_ttc"] = round_money(item["total_ttc"])
        if total_net_ht > ZERO:
            item["percentage"] = round(float((item["net_ht"] / total_net_ht) * 100), 1)
        else:
            item["percentage"] = 0.0
        item["avg_ticket"] = (
            round_money(item["net_ht"] / item["count"])
            if item["count"] > 0
            else ZERO
        )
        poles_list.append(item)

    return {
        "poles": poles_list,
        "total_net_ht": round_money(total_net_ht),
        "total_ttc": round_money(total_ttc),
    }


def get_conversion_metrics() -> Dict[str, Any]:
    """
    2. Taux de Conversion Commercial & Pipeline des Devis :
       - Taux de conversion en temps réel (devis convertis en factures validées / devis émis).
       - Suivi des états du cycle de négociation : En cours, Acceptés, Refusés, Brouillons.
    """
    all_quotes = list(Quote.objects.prefetch_related("invoices", "lines"))

    total_quotes_count = len(all_quotes)
    draft_quotes = [q for q in all_quotes if q.status == Quote.Status.DRAFT]
    issued_quotes = [q for q in all_quotes if q.status != Quote.Status.DRAFT]

    sent_quotes = [q for q in all_quotes if q.status == Quote.Status.SENT]
    accepted_quotes = [q for q in all_quotes if q.status == Quote.Status.ACCEPTED]
    partially_invoiced_quotes = [q for q in all_quotes if q.status == Quote.Status.PARTIALLY_INVOICED]
    converted_quotes = [q for q in all_quotes if q.status in {Quote.Status.CONVERTED, Quote.Status.PARTIALLY_INVOICED}]
    fully_converted_quotes = [q for q in all_quotes if q.status == Quote.Status.CONVERTED]
    rejected_quotes = [q for q in all_quotes if q.status in {Quote.Status.REJECTED, Quote.Status.CANCELLED}]
    expired_quotes = [q for q in all_quotes if q.status == Quote.Status.EXPIRED]

    def _calc_totals(q_list):
        total_net = ZERO
        total_ttc = ZERO
        for q in q_list:
            t = calculate_quote_totals(q.lines.all())
            total_net += t["net"]
            total_ttc += t["total"]
        return round_money(total_net), round_money(total_ttc)

    draft_ht, draft_ttc = _calc_totals(draft_quotes)
    sent_ht, sent_ttc = _calc_totals(sent_quotes)
    accepted_ht, accepted_ttc = _calc_totals(accepted_quotes)
    converted_ht, converted_ttc = _calc_totals(converted_quotes)
    rejected_ht, rejected_ttc = _calc_totals(rejected_quotes + expired_quotes)
    issued_ht, issued_ttc = _calc_totals(issued_quotes)

    issued_count = len(issued_quotes)
    converted_count = len(converted_quotes)
    won_count = converted_count + len(accepted_quotes)

    # Taux de conversion strict : Devis transformés en factures / Total devis émis
    conversion_rate = (
        round((converted_count / issued_count) * 100, 1)
        if issued_count > 0
        else 0.0
    )

    # Taux d'acceptation commercial (gagnés : acceptés + convertis)
    win_rate = (
        round((won_count / issued_count) * 100, 1)
        if issued_count > 0
        else 0.0
    )

    # Pourcentages pour le graphique / barre de flux
    negotiation_share = round((len(sent_quotes) / issued_count) * 100, 1) if issued_count > 0 else 0.0
    won_share = round((won_count / issued_count) * 100, 1) if issued_count > 0 else 0.0
    rejected_share = round((len(rejected_quotes + expired_quotes) / issued_count) * 100, 1) if issued_count > 0 else 0.0

    return {
        "conversion_rate": conversion_rate,
        "win_rate": win_rate,
        "total_quotes_count": total_quotes_count,
        "issued_count": issued_count,
        "converted_count": converted_count,
        "converted_ht": converted_ht,
        "converted_ttc": converted_ttc,
        # En cours de négociation
        "negotiation_count": len(sent_quotes),
        "negotiation_ht": sent_ht,
        "negotiation_ttc": sent_ttc,
        "negotiation_share": negotiation_share,
        # Acceptés (en attente + convertis)
        "accepted_only_count": len(accepted_quotes),
        "accepted_only_ttc": accepted_ttc,
        "won_count": won_count,
        "won_ht": round_money(accepted_ht + converted_ht),
        "won_ttc": round_money(accepted_ttc + converted_ttc),
        "won_share": won_share,
        # Refusés / Expirés
        "rejected_count": len(rejected_quotes + expired_quotes),
        "rejected_ht": rejected_ht,
        "rejected_ttc": rejected_ttc,
        "rejected_share": rejected_share,
        # Brouillons
        "draft_count": len(draft_quotes),
        "draft_ht": draft_ht,
        "draft_ttc": draft_ttc,
    }


def get_cashflow_and_aging_receivables(today=None) -> Dict[str, Any]:
    """
    3. Trésorerie & Encours Clients (Créances échues) :
       - Visualisation de la trésorerie (encaissements validés vs restants dus).
       - Suivi analytique des encours (créances échues vs non échues) et balance âgée.
    """
    if today is None:
        today = timezone.localdate()

    invoices = list(
        Invoice.objects.exclude(status__in=[Invoice.Status.DRAFT, Invoice.Status.CANCELLED])
        .select_related("customer")
        .prefetch_related("lines", "payment_allocations")
    )

    total_invoiced_ttc = ZERO
    total_collected_ttc = ZERO
    total_remaining_due = ZERO

    healthy_total = ZERO
    healthy_count = 0

    overdue_total = ZERO
    overdue_count = 0

    aging_less_30 = ZERO
    aging_less_30_count = 0
    aging_30_60 = ZERO
    aging_30_60_count = 0
    aging_over_60 = ZERO
    aging_over_60_count = 0

    overdue_invoices: List[Dict[str, Any]] = []

    for inv in invoices:
        inv_ttc = inv.total_ttc
        inv_paid = inv.total_paid
        inv_remaining = inv.remaining_balance

        total_invoiced_ttc += inv_ttc
        total_collected_ttc += inv_paid

        if inv_remaining > ZERO:
            total_remaining_due += inv_remaining
            is_overdue = bool(inv.due_date and inv.due_date < today)

            if is_overdue:
                days_overdue = (today - inv.due_date).days
                overdue_total += inv_remaining
                overdue_count += 1

                if days_overdue <= 30:
                    aging_less_30 += inv_remaining
                    aging_less_30_count += 1
                elif days_overdue <= 60:
                    aging_30_60 += inv_remaining
                    aging_30_60_count += 1
                else:
                    aging_over_60 += inv_remaining
                    aging_over_60_count += 1

                overdue_invoices.append({
                    "pk": inv.pk,
                    "number": inv.number or f"Facture #{inv.pk}",
                    "customer_name": (
                        inv.customer_snapshot.get("name")
                        if inv.customer_snapshot
                        else inv.customer.name
                    ),
                    "issue_date": inv.issue_date,
                    "due_date": inv.due_date,
                    "days_overdue": days_overdue,
                    "total_ttc": inv_ttc,
                    "paid": inv_paid,
                    "remaining": inv_remaining,
                })
            else:
                healthy_total += inv_remaining
                healthy_count += 1

    # Trier les retards du plus critique au plus récent
    overdue_invoices.sort(key=lambda x: x["days_overdue"], reverse=True)

    # Taux de recouvrement
    recovery_rate = (
        round(float((total_collected_ttc / total_invoiced_ttc) * 100), 1)
        if total_invoiced_ttc > ZERO
        else 0.0
    )

    # Répartition de l'encours
    if total_remaining_due > ZERO:
        overdue_share = round(float((overdue_total / total_remaining_due) * 100), 1)
        healthy_share = round(float((healthy_total / total_remaining_due) * 100), 1)
    else:
        overdue_share = 0.0
        healthy_share = 0.0

    return {
        "total_invoiced_ttc": round_money(total_invoiced_ttc),
        "total_collected_ttc": round_money(total_collected_ttc),
        "total_remaining_due": round_money(total_remaining_due),
        "recovery_rate": recovery_rate,
        # Créances saines (non échues)
        "healthy_total": round_money(healthy_total),
        "healthy_count": healthy_count,
        "healthy_share": healthy_share,
        # Créances échues (en retard)
        "overdue_total": round_money(overdue_total),
        "overdue_count": overdue_count,
        "overdue_share": overdue_share,
        # Balance âgée
        "aging_less_30": round_money(aging_less_30),
        "aging_less_30_count": aging_less_30_count,
        "aging_30_60": round_money(aging_30_60),
        "aging_30_60_count": aging_30_60_count,
        "aging_over_60": round_money(aging_over_60),
        "aging_over_60_count": aging_over_60_count,
        # Top créances prioritaires
        "overdue_invoices": overdue_invoices[:6],
    }


def get_executive_dashboard_context(today=None) -> Dict[str, Any]:
    """
    Agrège l'ensemble des métriques décisionnelles pour le tableau de bord exécutif.
    """
    if today is None:
        today = timezone.localdate()

    first_day_month = today.replace(day=1)
    first_day_year = today.replace(month=1, day=1)

    activity_data = get_revenue_by_activity()
    conversion_data = get_conversion_metrics()
    cashflow_data = get_cashflow_and_aging_receivables(today)

    # Encaissements mensuels
    monthly_payments = (
        Payment.objects.filter(payment_date__gte=first_day_month).aggregate(
            total=Sum("amount")
        )["total"]
        or ZERO
    )

    # Activité récente
    recent_invoices = (
        Invoice.objects.exclude(status=Invoice.Status.DRAFT)
        .select_related("customer")
        .prefetch_related("lines")
        .order_by("-issue_date", "-id")[:5]
    )
    recent_payments = (
        Payment.objects.select_related("customer")
        .prefetch_related("allocations__invoice")
        .order_by("-payment_date", "-id")[:5]
    )

    return {
        "today": today,
        "brand_slogan": "shaping tomorrow, today",
        "activity": activity_data,
        "conversion": conversion_data,
        "cashflow": cashflow_data,
        "monthly_payments": round_money(monthly_payments),
        "recent_invoices": recent_invoices,
        "recent_payments": recent_payments,
    }


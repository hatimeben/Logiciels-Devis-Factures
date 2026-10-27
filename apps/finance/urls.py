from django.urls import path

from . import (
    accounting_views,
    company_views,
    dashboard_views,
    expense_views,
    invoice_views,
    quote_views,
)


urlpatterns = [
    # Dashboard
    path("tableau-de-bord/", dashboard_views.dashboard, name="dashboard"),

    # Paramètres de l'entreprise
    path("parametres/entreprise/", company_views.company_settings_view, name="company-settings"),

    # Devis
    path("devis/", quote_views.quote_list, name="quote-list"),
    path("devis/nouveau/", quote_views.quote_create, name="quote-create"),
    path("devis/<int:quote_id>/", quote_views.quote_detail, name="quote-detail"),
    path("devis/<int:quote_id>/modifier/", quote_views.quote_edit, name="quote-edit"),
    path("devis/<int:quote_id>/envoyer/", quote_views.quote_send, name="quote-send"),
    path(
        "devis/<int:quote_id>/statut/",
        quote_views.quote_change_status,
        name="quote-change-status",
    ),
    path(
        "devis/<int:quote_id>/nouvelle-version/",
        quote_views.quote_revision,
        name="quote-revision",
    ),
    path("devis/<int:quote_id>/pdf/", quote_views.quote_pdf, name="quote-pdf"),
    path("devis/<int:quote_id>/convertir-en-facture/", invoice_views.invoice_convert, name="invoice-convert"),

    # Factures
    path("factures/", invoice_views.invoice_list, name="invoice-list"),
    path("factures/nouvelle/", invoice_views.invoice_create, name="invoice-create"),
    path("factures/paiements/", invoice_views.payment_list, name="payment-list"),
    path("factures/<int:invoice_id>/", invoice_views.invoice_detail, name="invoice-detail"),
    path("factures/<int:invoice_id>/modifier/", invoice_views.invoice_edit, name="invoice-edit"),
    path("factures/<int:invoice_id>/emettre/", invoice_views.invoice_issue, name="invoice-issue"),
    path("factures/<int:invoice_id>/paiement/", invoice_views.invoice_payment, name="invoice-payment"),
    path("factures/<int:invoice_id>/pdf/", invoice_views.invoice_pdf, name="invoice-pdf"),

    # Dépenses
    path("depenses/", expense_views.expense_list, name="expense-list"),
    path("depenses/nouvelle/", expense_views.expense_create, name="expense-create"),
    path("depenses/<int:expense_id>/", expense_views.expense_detail, name="expense-detail"),
    path("depenses/<int:expense_id>/modifier/", expense_views.expense_edit, name="expense-edit"),
    path("depenses/<int:expense_id>/paiement/", expense_views.expense_payment, name="expense-payment"),
    path("depenses/<int:expense_id>/justificatif/", expense_views.expense_receipt, name="expense-receipt"),

    # Comptabilité & Export Fiduciaire (TVA marocaine)
    path("comptabilite/", accounting_views.accounting_dashboard, name="accounting-dashboard"),
    path("comptabilite/export/pack/", accounting_views.export_fiduciary_pack_xlsx, name="accounting-export-pack"),
    path("comptabilite/export/tva/csv/", accounting_views.export_vat_csv, name="accounting-export-vat-csv"),
    path("comptabilite/export/ventes/csv/", accounting_views.export_sales_csv, name="accounting-export-sales-csv"),
    path("comptabilite/export/reglements/csv/", accounting_views.export_payments_csv, name="accounting-export-payments-csv"),
]

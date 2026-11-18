from django.urls import path
from apps.catalog import stock_views

from . import (
    accounting_views,
    backup_views,
    company_views,
    credit_note_views,
    dashboard_views,
    expense_views,
    invoice_views,
    quote_views,
    search_views,
)


urlpatterns = [
    # Recherche globale rapide (Ctrl + K)
    path("api/recherche-globale/", search_views.global_search_api, name="global-search-api"),

    # Dashboard
    path("tableau-de-bord/", dashboard_views.dashboard, name="dashboard"),

    # Paramètres de l'entreprise & Sauvegardes
    path("parametres/entreprise/", company_views.company_settings_view, name="company-settings"),
    path("parametres/sauvegarde/creer/", backup_views.backup_create, name="backup-create"),
    path("parametres/sauvegarde/telecharger/<str:filename>/", backup_views.backup_download, name="backup-download"),

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
    path("devis/<int:quote_id>/docx/", quote_views.quote_docx, name="quote-docx"),
    path("devis/<int:quote_id>/supprimer/", quote_views.quote_delete, name="quote-delete"),
    path("devis/<int:quote_id>/cloner/", quote_views.quote_clone, name="quote-clone"),
    path("devis/<int:quote_id>/convertir-en-facture/", invoice_views.invoice_convert, name="invoice-convert"),

    # Factures
    path("factures/", invoice_views.invoice_list, name="invoice-list"),
    path("factures/nouvelle/", invoice_views.invoice_create, name="invoice-create"),
    path("factures/paiements/", invoice_views.payment_list, name="payment-list"),
    path("factures/<int:invoice_id>/", invoice_views.invoice_detail, name="invoice-detail"),
    path("factures/<int:invoice_id>/modifier/", invoice_views.invoice_edit, name="invoice-edit"),
    path("factures/<int:invoice_id>/emettre/", invoice_views.invoice_issue, name="invoice-issue"),
    path("factures/<int:invoice_id>/annuler/", invoice_views.invoice_cancel, name="invoice-cancel"),
    path("factures/<int:invoice_id>/cloner/", invoice_views.invoice_clone, name="invoice-clone"),
    path("factures/<int:invoice_id>/paiement/", invoice_views.invoice_payment, name="invoice-payment"),
    path("factures/<int:invoice_id>/update-ice/", invoice_views.update_invoice_customer_ice, name="invoice-update-ice"),
    path("factures/<int:invoice_id>/pdf/", invoice_views.invoice_pdf, name="invoice-pdf"),
    path("factures/<int:invoice_id>/docx/", invoice_views.invoice_docx, name="invoice-docx"),

    # Avoirs (Notes de crédit / Retours partiels & totaux)
    path("avoirs/", credit_note_views.credit_note_list, name="credit-note-list"),
    path("factures/<int:invoice_id>/avoir/nouveau/", credit_note_views.credit_note_create, name="credit-note-create"),
    path("avoirs/<int:credit_note_id>/", credit_note_views.credit_note_detail, name="credit-note-detail"),
    path("avoirs/<int:credit_note_id>/emettre/", credit_note_views.credit_note_issue, name="credit-note-issue"),
    path("avoirs/<int:credit_note_id>/pdf/", credit_note_views.credit_note_pdf, name="credit-note-pdf"),
    path("avoirs/<int:credit_note_id>/docx/", credit_note_views.credit_note_docx, name="credit-note-docx"),

    # Dépenses
    path("depenses/", expense_views.expense_list, name="expense-list"),
    path("depenses/nouvelle/", expense_views.expense_create, name="expense-create"),
    path("depenses/<int:expense_id>/", expense_views.expense_detail, name="expense-detail"),
    path("depenses/<int:expense_id>/modifier/", expense_views.expense_edit, name="expense-edit"),
    path("depenses/<int:expense_id>/paiement/", expense_views.expense_payment, name="expense-payment"),
    path("depenses/<int:expense_id>/justificatif/", expense_views.expense_receipt, name="expense-receipt"),

    # Gestion des Stocks & Mouvements (Odoo / Zoho / Sage 100)
    path("stocks/", stock_views.stock_dashboard, name="stock-dashboard"),
    path("stocks/reception/", stock_views.stock_reception, name="stock-reception"),
    path("stocks/nouveau/", stock_views.article_create, name="stock-article-create"),
    path("stocks/mouvements/", stock_views.stock_movements_journal, name="stock-movements"),
    path("stocks/mouvements/export/csv/", stock_views.stock_export_movements_csv, name="stock-movements-export-csv"),
    path("stocks/vente-comptant/", stock_views.quick_cash_sale, name="stock-quick-cash-sale"),

    # Comptabilité & Export Fiduciaire (TVA marocaine)
    path("comptabilite/", accounting_views.accounting_dashboard, name="accounting-dashboard"),
    path("comptabilite/export/pack/", accounting_views.export_fiduciary_pack_xlsx, name="accounting-export-pack"),
    path("comptabilite/export/tva/csv/", accounting_views.export_vat_csv, name="accounting-export-vat-csv"),
    path("comptabilite/export/ventes/csv/", accounting_views.export_sales_csv, name="accounting-export-sales-csv"),
    path("comptabilite/export/reglements/csv/", accounting_views.export_payments_csv, name="accounting-export-payments-csv"),
    path("comptabilite/export/reintegrations/csv/", accounting_views.export_reintegration_csv, name="accounting-export-reintegration-csv"),
    path("comptabilite/export/achats/csv/", accounting_views.export_purchases_csv, name="accounting-export-purchases-csv"),
]

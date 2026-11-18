from html import escape
from io import BytesIO
import os
from decimal import Decimal

from django.utils.dateformat import format as format_date
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import CompanySettings
from .quote_calculations import calculate_quote_totals
from .quote_pdf import (
    _build_company_header,
    _money,
    _register_fonts,
    GOLD,
    GOLD_DARK,
    GOLD_LIGHT,
    GRID,
    INK,
    MIDNIGHT_BLUE,
    MIDNIGHT_LIGHT,
    MUTED,
    PALE,
)

BURGUNDY = colors.HexColor("#991B1B")
BURGUNDY_LIGHT = colors.HexColor("#FEF2F2")


def build_credit_note_pdf(credit_note):
    company = CompanySettings.get_settings()
    regular_font, bold_font = _register_fonts()
    buffer = BytesIO()
    number = credit_note.number or f"Avoir brouillon #{credit_note.pk}"
    issue_date = credit_note.issue_date or credit_note.created_at.date()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "CreditNoteBody",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=8.5,
        leading=12,
        textColor=INK,
    )
    title_style = ParagraphStyle(
        "CreditNoteTitle",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=18,
        leading=22,
        textColor=BURGUNDY,
        alignment=TA_RIGHT,
    )
    meta_label = ParagraphStyle(
        "CreditNoteMetaLabel",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=7.5,
        leading=10,
        textColor=GOLD_DARK,
        alignment=TA_RIGHT,
    )
    meta_value = ParagraphStyle(
        "CreditNoteMetaValue",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=8.5,
        leading=11,
        textColor=INK,
        alignment=TA_RIGHT,
    )

    story = []

    # 1. En-tête société
    company_cell = _build_company_header(company, body, styles["Normal"], bold_font)

    meta_paragraphs = [
        Paragraph("FACTURE D'AVOIR", title_style),
        Spacer(1, 2 * mm),
        Paragraph("NOTE DE CRÉDIT", ParagraphStyle("SubAvoir", parent=title_style, fontSize=11, leading=14, textColor=MUTED)),
        Spacer(1, 3 * mm),
        Paragraph("RÉFÉRENCE AVOIR", meta_label),
        Paragraph(f"<b>{escape(number)}</b>", meta_value),
        Paragraph("DATE D'ÉMISSION", meta_label),
        Paragraph(format_date(issue_date, "d/m/Y"), meta_value),
        Paragraph("FACTURE D'ORIGINE", meta_label),
        Paragraph(f"<b>{escape(credit_note.invoice.number or credit_note.invoice.pk)}</b>", meta_value),
    ]

    header_table = Table(
        [[company_cell, meta_paragraphs]],
        colWidths=[104 * mm, 70 * mm],
    )
    header_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.extend([header_table, Spacer(1, 6 * mm)])

    # 2. Bandeau Client & Motif
    customer_info = credit_note.customer_snapshot or {
        "name": credit_note.customer.name,
        "email": credit_note.customer.email,
        "phone": credit_note.customer.phone,
        "address": credit_note.customer.address,
        "city": credit_note.customer.city,
        "country": credit_note.customer.country,
        "ice": getattr(credit_note.customer, "ice", ""),
        "tax_identifier": credit_note.customer.tax_identifier,
    }

    client_html = (
        f"<b><font color='{MIDNIGHT_BLUE.hexval()}'>CLIENT CRÉDITÉ</font></b><br/>"
        f"<b>{escape(customer_info.get('name', ''))}</b><br/>"
    )
    if customer_info.get("address"):
        client_html += f"{escape(customer_info['address'])}<br/>"
    if customer_info.get("city"):
        client_html += f"{escape(customer_info['city'])} · {escape(customer_info.get('country', 'Maroc'))}<br/>"
    if customer_info.get("ice"):
        client_html += f"<b>ICE :</b> {escape(customer_info['ice'])} &nbsp;&nbsp; "
    if customer_info.get("tax_identifier"):
        client_html += f"<b>IF :</b> {escape(customer_info['tax_identifier'])}<br/>"

    motif_html = (
        f"<b><font color='{BURGUNDY.hexval()}'>MOTIF DE L'AVOIR</font></b><br/>"
        f"<b>{escape(credit_note.get_reason_display())}</b><br/>"
    )
    if credit_note.reason_details:
        motif_html += f"<font color='{MUTED.hexval()}'>{escape(credit_note.reason_details)}</font><br/>"
    motif_html += (
        f"<font size='7' color='{MUTED.hexval()}'>Rattaché à la facture <b>{escape(credit_note.invoice.number or credit_note.invoice.pk)}</b> "
        f"émise le {format_date(credit_note.invoice.issue_date or credit_note.invoice.created_at.date(), 'd/m/Y')}.</font>"
    )

    info_table = Table(
        [[
            Paragraph(client_html, ParagraphStyle("CNClient", parent=body, fontSize=8, leading=11)),
            Paragraph(motif_html, ParagraphStyle("CNMotif", parent=body, fontSize=8, leading=11)),
        ]],
        colWidths=[87 * mm, 87 * mm],
    )
    info_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), PALE),
            ("BACKGROUND", (1, 0), (1, 0), BURGUNDY_LIGHT),
            ("BOX", (0, 0), (0, 0), 0.5, GRID),
            ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#FCA5A5")),
            ("LINEBEFORE", (0, 0), (0, 0), 2.5, MIDNIGHT_BLUE),
            ("LINEBEFORE", (1, 0), (1, 0), 2.5, BURGUNDY),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    story.extend([info_table, Spacer(1, 6 * mm)])

    # 3. Tableau des lignes retournées / créditées
    lines = list(credit_note.lines.all())
    for line in lines:
        line.amounts = calculate_quote_totals([line])

    table_data = [[
        Paragraph(f"<b><font color='white'>DÉSIGNATION / PRESTATION RETOURNÉE</font></b>", ParagraphStyle("TH1", parent=body, fontSize=7.5, leading=9)),
        Paragraph(f"<b><font color='white'>QTÉ</font></b>", ParagraphStyle("TH2", parent=body, fontSize=7.5, leading=9, alignment=TA_RIGHT)),
        Paragraph(f"<b><font color='white'>P.U. HT</font></b>", ParagraphStyle("TH3", parent=body, fontSize=7.5, leading=9, alignment=TA_RIGHT)),
        Paragraph(f"<b><font color='white'>TVA</font></b>", ParagraphStyle("TH4", parent=body, fontSize=7.5, leading=9, alignment=TA_RIGHT)),
        Paragraph(f"<b><font color='white'>NET HT CRÉDITÉ</font></b>", ParagraphStyle("TH5", parent=body, fontSize=7.5, leading=9, alignment=TA_RIGHT)),
    ]]

    for line in lines:
        table_data.append([
            Paragraph(escape(line.description), body),
            Paragraph(f"{line.quantity:g}", ParagraphStyle("TD2", parent=body, alignment=TA_RIGHT)),
            Paragraph(_money(line.unit_price), ParagraphStyle("TD3", parent=body, alignment=TA_RIGHT)),
            Paragraph(f"{line.tax_percentage} %", ParagraphStyle("TD4", parent=body, alignment=TA_RIGHT)),
            Paragraph(_money(line.amounts["net"]), ParagraphStyle("TD5", parent=body, alignment=TA_RIGHT, fontName=bold_font)),
        ])

    lines_table = Table(
        table_data,
        colWidths=[84 * mm, 18 * mm, 24 * mm, 18 * mm, 30 * mm],
        repeatRows=1,
    )
    lines_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BURGUNDY),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, GRID),
        ])
    )
    story.extend([lines_table, Spacer(1, 6 * mm)])

    # 4. Totaux de l'avoir
    totals = calculate_quote_totals(lines)
    summary_data = [
        [Paragraph("TOTAL BRUT HT CRÉDITÉ", ParagraphStyle("ST1", parent=body, fontSize=8, alignment=TA_RIGHT)), Paragraph(f"− {_money(totals['gross'])} MAD", ParagraphStyle("SV1", parent=body, fontSize=8, alignment=TA_RIGHT))],
    ]
    if totals["discount"] > Decimal("0.00"):
        summary_data.append([
            Paragraph("REMISES DÉDUITES", ParagraphStyle("ST2", parent=body, fontSize=8, alignment=TA_RIGHT)),
            Paragraph(f"+ {_money(totals['discount'])} MAD", ParagraphStyle("SV2", parent=body, fontSize=8, alignment=TA_RIGHT)),
        ])
    summary_data.extend([
        [Paragraph("TOTAL NET HT CRÉDITÉ", ParagraphStyle("ST3", parent=body, fontSize=8.5, alignment=TA_RIGHT, fontName=bold_font)), Paragraph(f"− {_money(totals['net'])} MAD", ParagraphStyle("SV3", parent=body, fontSize=8.5, alignment=TA_RIGHT, fontName=bold_font))],
        [Paragraph(f"TVA DÉDUCTIBLE CRÉDITÉE", ParagraphStyle("ST4", parent=body, fontSize=8, alignment=TA_RIGHT)), Paragraph(f"− {_money(totals['tax'])} MAD", ParagraphStyle("SV4", parent=body, fontSize=8, alignment=TA_RIGHT))],
        [Paragraph(f"<b><font color='{BURGUNDY.hexval()}'>NET À CRÉDITER / DÉDUIRE TTC</font></b>", ParagraphStyle("ST5", parent=body, fontSize=10, alignment=TA_RIGHT)), Paragraph(f"<b><font color='{BURGUNDY.hexval()}'>− {_money(totals['total'])} MAD</font></b>", ParagraphStyle("SV5", parent=body, fontSize=10, alignment=TA_RIGHT))],
    ])

    summary_table = Table(
        summary_data,
        colWidths=[120 * mm, 54 * mm],
    )
    summary_table.setStyle(
        TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, -1), (-1, -1), 1.5, BURGUNDY),
            ("BACKGROUND", (0, -1), (-1, -1), BURGUNDY_LIGHT),
        ])
    )
    story.extend([summary_table, Spacer(1, 6 * mm)])

    # 5. Bloc de validation & Cachet officiel
    left_sign = [
        Paragraph(
            f"<b><font color='{MIDNIGHT_BLUE.hexval()}'>IMPUTATION COMPTABLE & FISCALE</font></b>",
            ParagraphStyle("AvoirSignHead", parent=body, fontSize=8, leading=10),
        ),
        Spacer(1, 1 * mm),
        Paragraph(
            f"<font color='{MUTED.hexval()}'>Le montant TTC de cet avoir de <b>{_money(totals['total'])} MAD</b> "
            f"est imputé sur le solde restant dû de la facture d'origine ou porté au crédit du compte client.<br/>"
            f"TVA et stock réintégrés conformément aux règles comptables marocaines.</font>",
            ParagraphStyle("AvoirSignSub", parent=body, fontSize=7.5, leading=10),
        ),
        Spacer(1, 10 * mm),
    ]

    company_sign = [
        Paragraph(
            f"<b><font color='{MIDNIGHT_BLUE.hexval()}'>POUR {escape(company.name.upper())}</font></b>",
            ParagraphStyle("CompAvoirHead", parent=body, fontSize=8, leading=10),
        ),
        Paragraph(
            f"<font color='{MUTED.hexval()}'>Cachet officiel & Signature autorisée :</font>",
            ParagraphStyle("CompAvoirSub", parent=body, fontSize=7, leading=9),
        ),
    ]
    stamp_path = company.stamp.path if (company.stamp and os.path.exists(company.stamp.path)) else None
    if stamp_path:
        company_sign.append(Spacer(1, 1 * mm))
        company_sign.append(Image(stamp_path, width=46 * mm, height=23 * mm))
    else:
        company_sign.append(Spacer(1, 14 * mm))

    sign_table = Table(
        [[left_sign, company_sign]],
        colWidths=[87 * mm, 87 * mm],
    )
    sign_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), PALE),
            ("BACKGROUND", (1, 0), (1, 0), PALE),
            ("BOX", (0, 0), (0, 0), 0.5, GRID),
            ("BOX", (1, 0), (1, 0), 0.5, GRID),
            ("LINEBEFORE", (0, 0), (0, 0), 2.0, BURGUNDY),
            ("LINEBEFORE", (1, 0), (1, 0), 2.0, BURGUNDY),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )
    story.extend([sign_table])

    def draw_page(canvas, document):
        canvas.saveState()
        width, height = A4
        if credit_note.status == "draft":
            canvas.saveState()
            canvas.translate(width / 2, height / 2)
            canvas.rotate(35)
            canvas.setFillColor(colors.HexColor("#D97706"))
            canvas.setFillAlpha(0.12)
            canvas.setFont(bold_font, 36)
            canvas.drawCentredString(0, 0, "AVOIR BROUILLON")
            canvas.restoreState()

        # Séparateur de pied de page
        canvas.setStrokeColor(BURGUNDY)
        canvas.setLineWidth(0.75)
        canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)

        canvas.setFont(regular_font, 6.8)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9.5 * mm, company.legal_notice())
        canvas.drawRightString(width - 18 * mm, 9.5 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()


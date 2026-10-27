from html import escape
from io import BytesIO
from decimal import Decimal

from django.utils.dateformat import format as format_date
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import CompanySettings
from .quote_calculations import calculate_quote_line, calculate_quote_totals
from .quote_pdf import (
    _register_fonts,
    _money,
    _build_company_header,
    MIDNIGHT_BLUE,
    MIDNIGHT_LIGHT,
    GOLD,
    GOLD_DARK,
    GOLD_LIGHT,
    INK,
    MUTED,
    PALE,
    GRID,
)


def build_invoice_pdf(invoice):
    company = CompanySettings.get_settings()
    regular_font, bold_font = _register_fonts()
    buffer = BytesIO()
    number = invoice.number or f"Brouillon #{invoice.pk}"
    issue_date = invoice.issue_date or invoice.created_at.date()

    # Format A4 strict : 210 x 297 mm
    # Marges 18 mm -> Largeur imprimable utile = 174 mm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=f"Facture {number}",
        author=f"{company.name} Gestion",
    )
    base = getSampleStyleSheet()
    customer = invoice.customer_snapshot or {
        "name": invoice.customer.name,
        "address": invoice.customer.address,
        "city": invoice.customer.city,
    }
    body = ParagraphStyle(
        "InvoiceBody",
        parent=base["BodyText"],
        fontName=regular_font,
        fontSize=8.5,
        leading=12,
        textColor=INK,
        spaceAfter=2,
    )
    muted = ParagraphStyle(
        "InvoiceMuted",
        parent=body,
        fontSize=8,
        leading=11,
        textColor=MUTED,
    )
    header = ParagraphStyle(
        "InvoiceHeader",
        parent=body,
        fontName=bold_font,
        fontSize=8,
        leading=10,
        textColor=colors.white,
    )
    cell = ParagraphStyle(
        "InvoiceCell",
        parent=body,
        fontSize=7.5,
        leading=10,
    )
    cell_right = ParagraphStyle(
        "InvoiceCellRight",
        parent=cell,
        alignment=TA_RIGHT,
    )
    summary = ParagraphStyle(
        "InvoiceSummary",
        parent=body,
        fontName=bold_font,
        fontSize=9.5,
        leading=13,
        textColor=MIDNIGHT_BLUE,
        alignment=TA_RIGHT,
    )

    story = [
        *_build_company_header(company, body, muted, bold_font),
        Spacer(1, 7 * mm),
    ]

    client_elements = [
        Paragraph(f"<b><font color='{GOLD_DARK.hexval()}'>DESTINATAIRE</font></b>", body),
        Spacer(1, 1.5 * mm),
        Paragraph(
            f"<b>{escape(customer['name'])}</b>",
            ParagraphStyle("CustName", parent=body, fontSize=9.5, leading=12, textColor=MIDNIGHT_BLUE),
        ),
    ]
    addr_city = []
    if customer.get("address"):
        addr_city.append(escape(customer["address"]).replace("\n", "<br/>"))
    if customer.get("city"):
        addr_city.append(escape(customer["city"]))
    if addr_city:
        client_elements.append(Paragraph("<br/>".join(addr_city), muted))

    client_box = Table([[client_elements]], colWidths=[90 * mm])
    client_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.4, GRID),
        ("LINEBEFORE", (0, 0), (0, -1), 2, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))

    meta_elements = [
        Paragraph("FACTURE", ParagraphStyle(
            "InvoiceTitle", parent=base["Title"], fontName=bold_font,
            fontSize=22, leading=26, textColor=MIDNIGHT_BLUE, alignment=TA_RIGHT,
        )),
        Spacer(1, 2 * mm),
        Paragraph(
            "<br/>".join(
                filter(
                    None,
                    [
                        f"<b>N°</b> <font color='{MIDNIGHT_BLUE.hexval()}'>{escape(number)}</font>",
                        f"<b>Date</b> : {format_date(issue_date, 'd/m/Y')}",
                        f"<b>Échéance</b> : {format_date(invoice.due_date, 'd/m/Y')}"
                        if invoice.due_date
                        else None,
                    ],
                )
            ),
            ParagraphStyle("DocMeta", parent=body, alignment=TA_RIGHT, leading=13),
        ),
    ]

    head = Table([[client_box, meta_elements]], colWidths=[94 * mm, 80 * mm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.extend([head, Spacer(1, 7 * mm)])

    lines = list(invoice.lines.all())
    totals = calculate_quote_totals(lines)
    has_discount = any(
        line.discount_value and line.discount_value > Decimal("0.00")
        for line in lines
    )

    # Largeur totale exacte : 174 mm (alignée à 100 % sur les marges A4)
    if has_discount:
        headers = [
            Paragraph("Désignation", header),
            Paragraph("Qté", header),
            Paragraph("PU HT", header),
            Paragraph("Remise", header),
            Paragraph("Total HT", header),
        ]
        col_widths = [72 * mm, 14 * mm, 30 * mm, 27 * mm, 31 * mm]
    else:
        headers = [
            Paragraph("Désignation", header),
            Paragraph("Qté", header),
            Paragraph("PU HT", header),
            Paragraph("Total HT", header),
        ]
        col_widths = [95 * mm, 18 * mm, 30 * mm, 31 * mm]

    rows = [headers]
    for line in lines:
        amounts = calculate_quote_line(
            line.quantity, line.unit_price, line.discount_type,
            line.discount_value, line.tax_percentage,
        )
        if has_discount:
            discount = (
                f"{line.discount_value}%"
                if line.discount_type == "percentage"
                else _money(line.discount_value)
            ) if (line.discount_value and line.discount_value > Decimal("0.00")) else "—"
            rows.append([
                Paragraph(escape(line.description), cell),
                Paragraph(str(line.quantity), cell_right),
                Paragraph(_money(line.unit_price), cell_right),
                Paragraph(discount, cell_right),
                Paragraph(_money(amounts["net"]), cell_right),
            ])
        else:
            rows.append([
                Paragraph(escape(line.description), cell),
                Paragraph(str(line.quantity), cell_right),
                Paragraph(_money(line.unit_price), cell_right),
                Paragraph(_money(amounts["net"]), cell_right),
            ])
    table = LongTable(
        rows,
        colWidths=col_widths,
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), MIDNIGHT_BLUE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, GOLD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
    ]))
    story.extend([table, Spacer(1, 6 * mm)])

    tax_totals = {}
    for line in lines:
        amount = calculate_quote_line(
            line.quantity, line.unit_price, line.discount_type,
            line.discount_value, line.tax_percentage,
        )["tax"]
        key = (line.tax_name or "Sans taxe", line.tax_percentage or Decimal("0.00"))
        tax_totals[key] = tax_totals.get(key, Decimal("0.00")) + amount

    if has_discount:
        summary_rows = [
            ["Total brut HT", _money(totals["gross"])],
            ["Remises", f"- {_money(totals['discount'])}"],
            ["Total net HT", _money(totals["net"])],
        ]
    else:
        summary_rows = [
            ["Total HT", _money(totals["net"])],
        ]
    summary_rows.extend(
        [f"{name} ({percentage} %)", _money(amount)]
        for (name, percentage), amount in sorted(tax_totals.items())
        if amount
    )
    summary_rows.append(["Total TTC", _money(totals["total"])])
    if invoice.total_paid > Decimal("0.00"):
        summary_rows.append(["Montant déjà réglé", f"- {_money(invoice.total_paid)}"])
        summary_rows.append(["Net à payer", _money(invoice.remaining_balance)])
    summary_table = Table(
        [[
            Paragraph(escape(label), body),
            Paragraph(
                escape(amount),
                summary if label in {"Total TTC", "Net à payer"} else body
            ),
        ] for label, amount in summary_rows],
        colWidths=[54 * mm, 45 * mm],
        hAlign="RIGHT",
    )
    summary_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.5, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(summary_table)

    if invoice.notes:
        notes_table = Table(
            [[
                Paragraph(
                    f"<b><font color='{MIDNIGHT_BLUE.hexval()}'>CONDITIONS & REMARQUES</font></b><br/>"
                    + escape(invoice.notes).replace("\n", "<br/>"),
                    ParagraphStyle("NotesText", parent=body, fontSize=8, leading=11),
                )
            ]],
            colWidths=[174 * mm],
        )
        notes_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PALE),
                ("BOX", (0, 0), (-1, -1), 0.5, GRID),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, GOLD),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ])
        )
        story.extend([
            Spacer(1, 6 * mm),
            notes_table,
        ])

    if company.bank_name or company.rib:
        bank_lines = []
        if company.bank_name:
            bank_lines.append(f"<b>Banque :</b> {escape(company.bank_name)}")
        if company.rib:
            bank_lines.append(f"<b>RIB (virement) :</b> <font face='{bold_font}'>{escape(company.formatted_rib or company.rib)}</font>")
        bank_table = Table(
            [[
                Paragraph(
                    f"<b><font color='{MIDNIGHT_BLUE.hexval()}'>MODALITÉS DE RÈGLEMENT PAR VIREMENT BANCAIRE</font></b><br/>"
                    + " &nbsp;&nbsp;·&nbsp;&nbsp; ".join(bank_lines),
                    ParagraphStyle("BankText", parent=body, fontSize=8, leading=11),
                )
            ]],
            colWidths=[174 * mm],
        )
        bank_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), GOLD_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, GRID),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, GOLD),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ])
        )
        story.extend([
            Spacer(1, 6 * mm),
            bank_table,
        ])

    def draw_page(canvas, document):
        canvas.saveState()
        width, height = A4
        if invoice.status == "draft":
            canvas.saveState()
            canvas.translate(width / 2, height / 2)
            canvas.rotate(35)
            canvas.setFillColor(colors.HexColor("#D97706"))
            canvas.setFillAlpha(0.12)
            canvas.setFont(bold_font, 36)
            canvas.drawCentredString(0, 0, "BROUILLON - NON ÉMISE")
            canvas.restoreState()
        elif invoice.status == "paid":
            canvas.saveState()
            canvas.translate(width / 2, height / 2)
            canvas.rotate(35)
            canvas.setFillColor(colors.HexColor("#16A34A"))
            canvas.setFillAlpha(0.12)
            canvas.setFont(bold_font, 36)
            canvas.drawCentredString(0, 0, "FACTURE ACQUITTÉE")
            canvas.restoreState()

        # Séparateur de pied de page en Or impérial fin
        canvas.setStrokeColor(GOLD)
        canvas.setLineWidth(0.75)
        canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)

        canvas.setFont(regular_font, 6.8)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9.5 * mm, company.legal_notice())
        canvas.drawRightString(width - 18 * mm, 9.5 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()

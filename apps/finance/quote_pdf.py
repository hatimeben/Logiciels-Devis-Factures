from html import escape
from io import BytesIO
import os
from pathlib import Path
from decimal import Decimal

from django.utils.dateformat import format as format_date
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
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
from .quote_calculations import calculate_quote_line, calculate_quote_totals

# Palette d'impression Haute Définition : Bleu Nuit & Or
MIDNIGHT_BLUE = colors.HexColor("#0F172A")  # Bleu Nuit profond (structure & en-têtes)
MIDNIGHT_LIGHT = colors.HexColor("#1E293B") # Bleu Nuit adouci
GOLD = colors.HexColor("#C59B27")           # Or impérial (accents, séparateurs & totaux)
GOLD_DARK = colors.HexColor("#9A7516")      # Or soutenu (titres de rubriques & libellés)
GOLD_LIGHT = colors.HexColor("#FAF5E8")     # Fond crème doré (encadrés & surbrillance)
INK = colors.HexColor("#0F172A")            # Encre principale (texte noir bleuté HD)
MUTED = colors.HexColor("#475569")          # Encre secondaire (coordonnées & légal)
PALE = colors.HexColor("#F8FAFC")           # Fond de ligne alternée
GRID = colors.HexColor("#E2E8F0")           # Bordures fines du tableau


def _money(amount):
    value = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{value} MAD"


def _register_fonts():
    font_dir = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    regular = font_dir / "arial.ttf"
    bold = font_dir / "arialbd.ttf"
    if regular.exists() and bold.exists():
        if "BenchTechArial" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("BenchTechArial", str(regular)))
            pdfmetrics.registerFont(TTFont("BenchTechArial-Bold", str(bold)))
        return "BenchTechArial", "BenchTechArial-Bold"
    return "Helvetica", "Helvetica-Bold"


def _build_company_header(company, body_style, muted_style, bold_font):
    elements = []
    has_logo = False
    if company.logo:
        try:
            logo_path = company.logo.path
            if os.path.exists(logo_path):
                from PIL import Image as PILImage
                with PILImage.open(logo_path) as img:
                    orig_w, orig_h = img.size
                max_w = 48 * mm
                max_h = 20 * mm
                ratio = min(max_w / orig_w, max_h / orig_h)
                w = orig_w * ratio
                h = orig_h * ratio
                elements.append(Image(logo_path, width=w, height=h, hAlign="LEFT"))
                has_logo = True
        except Exception:
            has_logo = False

    if not has_logo:
        elements.append(
            Paragraph(
                escape(company.name),
                ParagraphStyle("Brand", parent=body_style, fontName=bold_font, fontSize=16, leading=19, textColor=MIDNIGHT_BLUE),
            )
        )

    if company.tagline:
        elements.append(
            Paragraph(
                f"<b><font color='{GOLD_DARK.hexval()}'>{escape(company.tagline)}</font></b>",
                muted_style,
            )
        )

    contact_parts = []
    if company.address:
        contact_parts.append(escape(company.address).replace("\n", " "))
    if company.city:
        loc = escape(company.city)
        if company.postal_code:
            loc += f" ({escape(company.postal_code)})"
        contact_parts.append(loc)
    contact_info = []
    if company.phone:
        contact_info.append(f"Tél : {escape(company.phone)}")
    if company.email:
        contact_info.append(escape(company.email))
    if company.website:
        contact_info.append(escape(company.website))
    if contact_info:
        contact_parts.append(" · ".join(contact_info))
    if contact_parts:
        elements.append(Paragraph("<br/>".join(contact_parts), muted_style))

    return elements


def build_quote_pdf(quote):
    company = CompanySettings.get_settings()
    regular_font, bold_font = _register_fonts()
    buffer = BytesIO()
    document_number = quote.number or f"Brouillon #{quote.pk}"
    issue_date = quote.issue_date or quote.created_at.date()

    # Format A4 strict : 210 x 297 mm
    # Marges 18 mm -> Largeur imprimable utile = 174 mm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        title=f"Devis {document_number}",
        author=f"{company.name} Gestion",
    )
    base = getSampleStyleSheet()
    customer = quote.customer_snapshot or {
        "name": quote.customer.name,
        "address": quote.customer.address,
        "city": quote.customer.city,
    }
    styles = {
        "title": ParagraphStyle(
            "QuoteTitle",
            parent=base["Title"],
            fontName=bold_font,
            fontSize=22,
            leading=26,
            textColor=MIDNIGHT_BLUE,
            alignment=TA_RIGHT,
        ),
        "body": ParagraphStyle(
            "QuoteBody",
            parent=base["BodyText"],
            fontName=regular_font,
            fontSize=8.5,
            leading=12,
            textColor=INK,
            spaceAfter=2,
        ),
        "muted": ParagraphStyle(
            "QuoteMuted",
            parent=base["BodyText"],
            fontName=regular_font,
            fontSize=8,
            leading=11,
            textColor=MUTED,
        ),
        "header": ParagraphStyle(
            "QuoteHeader",
            parent=base["BodyText"],
            fontName=bold_font,
            fontSize=8,
            leading=10,
            textColor=colors.white,
        ),
        "cell": ParagraphStyle(
            "QuoteCell",
            parent=base["BodyText"],
            fontName=regular_font,
            fontSize=7.5,
            leading=10,
            textColor=INK,
        ),
        "cell_right": ParagraphStyle(
            "QuoteCellRight",
            parent=base["BodyText"],
            fontName=regular_font,
            fontSize=7.5,
            leading=10,
            textColor=INK,
            alignment=TA_RIGHT,
        ),
        "summary": ParagraphStyle(
            "QuoteSummary",
            parent=base["BodyText"],
            fontName=bold_font,
            fontSize=9.5,
            leading=13,
            textColor=MIDNIGHT_BLUE,
            alignment=TA_RIGHT,
        ),
    }

    story = [
        *_build_company_header(company, styles["body"], styles["muted"], bold_font),
        Spacer(1, 7 * mm),
    ]

    client_elements = [
        Paragraph(f"<b><font color='{GOLD_DARK.hexval()}'>DESTINATAIRE</font></b>", styles["body"]),
        Spacer(1, 1.5 * mm),
        Paragraph(
            f"<b>{escape(customer['name'])}</b>",
            ParagraphStyle("CustName", parent=styles["body"], fontSize=9.5, leading=12, textColor=MIDNIGHT_BLUE),
        ),
    ]
    addr_city = []
    if customer.get("address"):
        addr_city.append(escape(customer["address"]).replace("\n", "<br/>"))
    if customer.get("city"):
        addr_city.append(escape(customer["city"]))
    if addr_city:
        client_elements.append(Paragraph("<br/>".join(addr_city), styles["muted"]))

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
        Paragraph("DEVIS", styles["title"]),
        Spacer(1, 2 * mm),
        Paragraph(
            "<br/>".join(
                filter(
                    None,
                    [
                        f"<b>N°</b> <font color='{MIDNIGHT_BLUE.hexval()}'>{escape(document_number)}</font>",
                        f"<b>Date</b> : {format_date(issue_date, 'd/m/Y')}",
                        f"<b>Valable jusqu'au</b> : {format_date(quote.valid_until, 'd/m/Y')}"
                        if quote.valid_until
                        else None,
                    ],
                )
            ),
            ParagraphStyle("DocMeta", parent=styles["body"], alignment=TA_RIGHT, leading=13),
        ),
    ]

    head = Table([[client_box, meta_elements]], colWidths=[94 * mm, 80 * mm])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.extend([head, Spacer(1, 7 * mm)])

    lines = list(quote.lines.all())
    totals = calculate_quote_totals(lines)
    has_discount = any(
        line.discount_value and line.discount_value > Decimal("0.00")
        for line in lines
    )

    # Largeur totale exacte : 174 mm (alignée à 100 % sur les marges A4)
    if has_discount:
        headers = [
            Paragraph("Désignation", styles["header"]),
            Paragraph("Qté", styles["header"]),
            Paragraph("PU HT", styles["header"]),
            Paragraph("Remise", styles["header"]),
            Paragraph("Total HT", styles["header"]),
        ]
        col_widths = [72 * mm, 14 * mm, 30 * mm, 27 * mm, 31 * mm]
    else:
        headers = [
            Paragraph("Désignation", styles["header"]),
            Paragraph("Qté", styles["header"]),
            Paragraph("PU HT", styles["header"]),
            Paragraph("Total HT", styles["header"]),
        ]
        col_widths = [95 * mm, 18 * mm, 30 * mm, 31 * mm]

    rows = [headers]
    for line in lines:
        amounts = calculate_quote_line(
            line.quantity,
            line.unit_price,
            line.discount_type,
            line.discount_value,
            line.tax_percentage,
        )
        if has_discount:
            discount_label = (
                f"{line.discount_value}%"
                if line.discount_type == "percentage"
                else _money(line.discount_value)
            ) if (line.discount_value and line.discount_value > Decimal("0.00")) else "—"
            rows.append(
                [
                    Paragraph(escape(line.description), styles["cell"]),
                    Paragraph(str(line.quantity), styles["cell_right"]),
                    Paragraph(_money(line.unit_price), styles["cell_right"]),
                    Paragraph(discount_label, styles["cell_right"]),
                    Paragraph(_money(amounts["net"]), styles["cell_right"]),
                ]
            )
        else:
            rows.append(
                [
                    Paragraph(escape(line.description), styles["cell"]),
                    Paragraph(str(line.quantity), styles["cell_right"]),
                    Paragraph(_money(line.unit_price), styles["cell_right"]),
                    Paragraph(_money(amounts["net"]), styles["cell_right"]),
                ]
            )
    table = LongTable(
        rows,
        colWidths=col_widths,
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), MIDNIGHT_BLUE),
                ("LINEBELOW", (0, 0), (-1, 0), 1.5, GOLD),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, GRID),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
            ]
        )
    )
    story.extend([table, Spacer(1, 6 * mm)])

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
    for tax_name, tax_amount in _tax_totals(quote):
        summary_rows.append([f"Taxe - {tax_name}", _money(tax_amount)])
    summary_rows.append(["Total TTC", _money(totals["total"])])
    summary = Table(
        [
            [
                Paragraph(escape(label), styles["body"]),
                Paragraph(escape(amount), styles["summary"] if label == "Total TTC" else styles["body"]),
            ]
            for label, amount in summary_rows
        ],
        colWidths=[54 * mm, 45 * mm],
        hAlign="RIGHT",
    )
    summary.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 1.5, GOLD),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(summary)

    if quote.notes:
        notes_table = Table(
            [[
                Paragraph(
                    f"<b><font color='{MIDNIGHT_BLUE.hexval()}'>CONDITIONS & REMARQUES</font></b><br/>"
                    + escape(quote.notes).replace("\n", "<br/>"),
                    ParagraphStyle("NotesText", parent=styles["body"], fontSize=8, leading=11),
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

    if company.bank_name or company.rib or getattr(company, "display_rib", None):
        bank_name = getattr(company, "display_bank_name", company.bank_name or "BANK OF AFRICA (BMCE)")
        rib_value = getattr(company, "display_rib", company.formatted_rib or company.rib or "011 780 0000642100038454 63")
        doc_ref = escape(document_number)

        bank_html = (
            f"<b><font color='{MIDNIGHT_BLUE.hexval()}'>RIB (Virement bancaire)</font></b><br/>"
            f"• <b>Banque :</b> {escape(bank_name)} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
            f"• <b>RIB :</b> <font face='{bold_font}' color='{MIDNIGHT_BLUE.hexval()}'>{escape(rib_value)}</font><br/>"
            f"• <b>Note :</b> <font color='{GOLD_DARK.hexval()}'>Merci de rappeler la référence du devis ({doc_ref}) sur l'ordre de virement.</font>"
        )
        bank_table = Table(
            [[
                Paragraph(
                    bank_html,
                    ParagraphStyle("QuoteBankText", parent=styles["body"], fontSize=8, leading=12),
                )
            ]],
            colWidths=[174 * mm],
        )
        bank_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), GOLD_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, GRID),
                ("LINEBEFORE", (0, 0), (0, -1), 3.0, GOLD),
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
        if quote.status == "draft":
            canvas.saveState()
            canvas.translate(width / 2, height / 2)
            canvas.rotate(35)
            canvas.setFillColor(colors.HexColor("#D97706"))
            canvas.setFillAlpha(0.12)
            canvas.setFont(bold_font, 36)
            canvas.drawCentredString(0, 0, "BROUILLON - NON ÉMIS")
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


def _tax_totals(quote):
    totals = {}
    for line in quote.lines.all():
        amounts = calculate_quote_line(
            line.quantity,
            line.unit_price,
            line.discount_type,
            line.discount_value,
            line.tax_percentage,
        )
        key = (line.tax_name or "Sans taxe", line.tax_percentage or Decimal("0.00"))
        totals[key] = totals.get(key, Decimal("0.00")) + amounts["tax"]
    return [
        (f"{name} ({percentage} %)", amount)
        for (name, percentage), amount in sorted(totals.items())
        if amount
    ]

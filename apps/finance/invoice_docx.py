"""
Génération de documents Word (.docx) professionnels pour les factures BENCHTECH.
Exacte réplique du modèle PDF (apps/finance/invoice_pdf.py) :
- Mêmes dimensions (A4, marges 18 mm, largeur utile 174 mm)
- Même palette graphique Haute Définition : Bleu Nuit (#0F172A) & Or (#C59B27, #9A7516)
- Mêmes informations : En-tête entreprise, Destinataire, Métadonnées, Tableau des lignes HT,
  Récapitulatif financier (avec Déjà réglé et Net à payer si paiements), Conditions, RIB,
  Mentions légales complètes (ICE, IF, RC) et pagination.
"""
from decimal import Decimal
from io import BytesIO
import os

from django.utils.dateformat import format as format_date
import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Mm, Pt, RGBColor

from apps.finance.models import CompanySettings
from apps.finance.quote_calculations import calculate_quote_line, calculate_quote_totals

# Palette d'impression Haute Définition identique au PDF
COLOR_MIDNIGHT = RGBColor(15, 23, 42)      # #0F172A - Bleu Nuit profond
COLOR_MIDNIGHT_LIGHT = RGBColor(30, 41, 59)# #1E293B
COLOR_GOLD = RGBColor(197, 155, 39)        # #C59B27 - Or impérial
COLOR_GOLD_DARK = RGBColor(154, 117, 22)   # #9A7516 - Or soutenu
COLOR_INK = RGBColor(15, 23, 42)           # #0F172A - Encre principale
COLOR_MUTED = RGBColor(71, 85, 105)        # #475569 - Encre secondaire
COLOR_AMBER = RGBColor(217, 119, 6)        # #D97706 - Brouillon
COLOR_GREEN = RGBColor(21, 128, 61)        # #15803D - Acquittée

HEX_MIDNIGHT = "0F172A"
HEX_GOLD = "C59B27"
HEX_GOLD_DARK = "9A7516"
HEX_GOLD_LIGHT = "FAF5E8"
HEX_PALE = "F8FAFC"
HEX_GRID = "E2E8F0"
HEX_AMBER = "D97706"


def _money(amount):
    val = f"{Decimal(str(amount or 0)):,.2f}".replace(",", " ").replace(".", ",")
    return f"{val} MAD"


def _set_cell_background(cell, hex_color):
    tcPr = cell._tc.get_or_add_tcPr()
    tcPr.append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>'))


def _set_cell_margins(cell, top=80, bottom=80, left=100, right=100):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('w:top', top), ('w:bottom', bottom), ('w:left', left), ('w:right', right)]:
        node = OxmlElement(m)
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)


def _set_cell_borders(cell, top=None, bottom=None, left=None, right=None):
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    borders = {'top': top, 'bottom': bottom, 'left': left, 'right': right}
    for side, border in borders.items():
        if border:
            val, sz, color = border
            el = OxmlElement(f'w:{side}')
            el.set(qn('w:val'), val)
            el.set(qn('w:sz'), str(sz))
            el.set(qn('w:space'), '0')
            el.set(qn('w:color'), color)
            tcBorders.append(el)
        else:
            el = OxmlElement(f'w:{side}')
            el.set(qn('w:val'), 'none')
            tcBorders.append(el)
    tcPr.append(tcBorders)


def build_invoice_docx(invoice):
    company = CompanySettings.get_settings()
    doc = docx.Document()

    # Format A4 strict (210 x 297 mm), marges 18 mm
    section = doc.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.left_margin = Mm(18)
    section.right_margin = Mm(18)
    section.top_margin = Mm(18)
    section.bottom_margin = Mm(18)

    normal_style = doc.styles['Normal']
    normal_style.font.name = 'Arial'
    normal_style.font.size = Pt(8.5)
    normal_style.font.color.rgb = COLOR_INK

    number = invoice.number or f"Brouillon #{invoice.pk}"
    issue_date = invoice.issue_date or invoice.created_at.date()

    # Bandeau brouillon si non émis
    if invoice.status == "draft":
        draft_table = doc.add_table(rows=1, cols=1)
        draft_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        draft_table.autofit = False
        draft_table.columns[0].width = Mm(174)
        c_draft = draft_table.cell(0, 0)
        _set_cell_background(c_draft, "FEF3C7")
        _set_cell_margins(c_draft, top=60, bottom=60, left=100, right=100)
        _set_cell_borders(c_draft,
            top=("single", 8, HEX_AMBER),
            bottom=("single", 8, HEX_AMBER),
            left=("single", 8, HEX_AMBER),
            right=("single", 8, HEX_AMBER),
        )
        p_draft = c_draft.paragraphs[0]
        p_draft.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_draft.paragraph_format.space_after = Pt(0)
        r_dr = p_draft.add_run("⚠ FACTURE BROUILLON — NON ÉMISE")
        r_dr.bold = True
        r_dr.font.size = Pt(10)
        r_dr.font.color.rgb = COLOR_AMBER
        doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # En-tête Entreprise
    header_table = doc.add_table(rows=1, cols=1)
    header_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    header_table.autofit = False
    header_table.columns[0].width = Mm(174)
    c_header = header_table.cell(0, 0)

    p_comp = c_header.paragraphs[0]
    p_comp.paragraph_format.space_after = Pt(2)
    p_comp.paragraph_format.line_spacing = 1.15

    has_logo = False
    if company.logo and os.path.exists(company.logo.path):
        try:
            from PIL import Image as PILImage
            with PILImage.open(company.logo.path) as img:
                orig_w, orig_h = img.size
            max_w_mm = 48
            max_h_mm = 20
            ratio = min(max_w_mm / orig_w, max_h_mm / orig_h)
            w_mm = orig_w * ratio
            r_logo = p_comp.add_run()
            r_logo.add_picture(company.logo.path, width=Mm(w_mm))
            has_logo = True
            p_comp = c_header.add_paragraph()
            p_comp.paragraph_format.space_after = Pt(2)
            p_comp.paragraph_format.line_spacing = 1.15
        except Exception:
            has_logo = False

    if not has_logo:
        r_name = p_comp.add_run(company.name or "BENCHTECH")
        r_name.bold = True
        r_name.font.size = Pt(16)
        r_name.font.color.rgb = COLOR_MIDNIGHT

    if company.tagline:
        p_tag = c_header.add_paragraph()
        p_tag.paragraph_format.space_after = Pt(2)
        r_tag = p_tag.add_run(company.tagline)
        r_tag.bold = True
        r_tag.font.size = Pt(8)
        r_tag.font.color.rgb = COLOR_GOLD_DARK

    contact_parts = []
    if company.address:
        contact_parts.append(company.address.replace("\n", " "))
    loc = company.city or ""
    if company.postal_code:
        loc += f" ({company.postal_code})"
    if loc:
        contact_parts.append(loc)
    contact_info = []
    if company.phone:
        contact_info.append(f"Tél : {company.phone}")
    if company.email:
        contact_info.append(company.email)
    if company.website:
        contact_info.append(company.website)
    if contact_info:
        contact_parts.append(" · ".join(contact_info))

    if contact_parts:
        p_contacts = c_header.add_paragraph()
        p_contacts.paragraph_format.space_after = Pt(0)
        p_contacts.paragraph_format.line_spacing = 1.15
        r_c = p_contacts.add_run("\n".join(contact_parts))
        r_c.font.size = Pt(8)
        r_c.font.color.rgb = COLOR_MUTED

    p_sp1 = doc.add_paragraph()
    p_sp1.paragraph_format.space_before = Pt(6)
    p_sp1.paragraph_format.space_after = Pt(4)

    # Bloc Deux Colonnes : Destinataire (94 mm) & FACTURE Métadonnées (80 mm)
    head_table = doc.add_table(rows=1, cols=2)
    head_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    head_table.autofit = False
    head_table.columns[0].width = Mm(94)
    head_table.columns[1].width = Mm(80)

    cell_client = head_table.cell(0, 0)
    cell_meta = head_table.cell(0, 1)

    _set_cell_background(cell_client, HEX_PALE)
    _set_cell_margins(cell_client, top=90, bottom=90, left=120, right=120)
    _set_cell_borders(
        cell_client,
        top=("single", 4, HEX_GRID),
        bottom=("single", 4, HEX_GRID),
        left=("single", 16, HEX_GOLD),
        right=("single", 4, HEX_GRID),
    )

    p_dest_title = cell_client.paragraphs[0]
    p_dest_title.paragraph_format.space_after = Pt(2)
    r_dt = p_dest_title.add_run("DESTINATAIRE")
    r_dt.bold = True
    r_dt.font.size = Pt(8.5)
    r_dt.font.color.rgb = COLOR_GOLD_DARK

    customer = dict(invoice.customer_snapshot or {})
    if not customer.get("name") and invoice.customer:
        customer["name"] = invoice.customer.name
    if not customer.get("address") and invoice.customer:
        customer["address"] = invoice.customer.address
    if not customer.get("city") and invoice.customer:
        customer["city"] = invoice.customer.city
    if not customer.get("ice") and invoice.customer:
        customer["ice"] = getattr(invoice.customer, "ice", "")
    if not customer.get("tax_identifier") and invoice.customer:
        customer["tax_identifier"] = getattr(invoice.customer, "tax_identifier", "")

    p_dest_name = cell_client.add_paragraph()
    p_dest_name.paragraph_format.space_after = Pt(2)
    r_dn = p_dest_name.add_run(customer.get("name", ""))
    r_dn.bold = True
    r_dn.font.size = Pt(9.5)
    r_dn.font.color.rgb = COLOR_MIDNIGHT

    addr_parts = []
    if customer.get("address"):
        addr_parts.append(customer["address"])
    if customer.get("city"):
        addr_parts.append(customer["city"])
    if addr_parts:
        p_dest_addr = cell_client.add_paragraph()
        p_dest_addr.paragraph_format.space_after = Pt(0)
        p_dest_addr.paragraph_format.line_spacing = 1.15
        r_da = p_dest_addr.add_run("\n".join(addr_parts))
        r_da.font.size = Pt(8)
        r_da.font.color.rgb = COLOR_MUTED

    fiscal_parts = []
    if customer.get("ice"):
        fiscal_parts.append(f"ICE : {customer['ice']}")
    if customer.get("tax_identifier"):
        fiscal_parts.append(f"IF : {customer['tax_identifier']}")
    if fiscal_parts:
        p_dest_fisc = cell_client.add_paragraph()
        p_dest_fisc.paragraph_format.space_before = Pt(2)
        p_dest_fisc.paragraph_format.space_after = Pt(0)
        r_df = p_dest_fisc.add_run(" · ".join(fiscal_parts))
        r_df.font.size = Pt(8)
        r_df.bold = True
        r_df.font.color.rgb = COLOR_MIDNIGHT

    # Métadonnées Facture
    p_m_title = cell_meta.paragraphs[0]
    p_m_title.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_m_title.paragraph_format.space_after = Pt(4)
    is_paid = (invoice.status == "paid")
    r_mt = p_m_title.add_run("FACTURE ACQUITTÉE" if is_paid else "FACTURE")
    r_mt.bold = True
    r_mt.font.size = Pt(20 if is_paid else 22)
    r_mt.font.color.rgb = COLOR_GREEN if is_paid else COLOR_MIDNIGHT

    p_m_lines = cell_meta.add_paragraph()
    p_m_lines.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_m_lines.paragraph_format.line_spacing = 1.25

    r_n_lbl = p_m_lines.add_run("N° ")
    r_n_lbl.bold = True
    r_n_lbl.font.size = Pt(8.5)
    r_n_val = p_m_lines.add_run(f"{number}\n")
    r_n_val.bold = True
    r_n_val.font.size = Pt(9.5)
    r_n_val.font.color.rgb = COLOR_MIDNIGHT

    r_d_lbl = p_m_lines.add_run("Date : ")
    r_d_lbl.bold = True
    r_d_lbl.font.size = Pt(8.5)
    p_m_lines.add_run(f"{format_date(issue_date, 'd/m/Y')}\n")

    if invoice.due_date:
        r_v_lbl = p_m_lines.add_run("Échéance : ")
        r_v_lbl.bold = True
        r_v_lbl.font.size = Pt(8.5)
        p_m_lines.add_run(f"{format_date(invoice.due_date, 'd/m/Y')}\n")

    r_curr_lbl = p_m_lines.add_run("Montants en : ")
    r_curr_lbl.bold = True
    r_curr_lbl.font.size = Pt(8.5)
    p_m_lines.add_run("MAD")

    p_sp2 = doc.add_paragraph()
    p_sp2.paragraph_format.space_before = Pt(6)
    p_sp2.paragraph_format.space_after = Pt(4)

    # Tableau des lignes
    lines = list(invoice.lines.all())
    totals = calculate_quote_totals(lines)
    has_discount = any(
        line.discount_value and line.discount_value > Decimal("0.00")
        for line in lines
    )

    if has_discount:
        col_headers = ["Désignation", "Qté", "PU HT", "Remise", "Total HT"]
        col_widths = [Mm(72), Mm(14), Mm(30), Mm(27), Mm(31)]
    else:
        col_headers = ["Désignation", "Qté", "PU HT", "Total HT"]
        col_widths = [Mm(95), Mm(18), Mm(30), Mm(31)]

    lines_table = doc.add_table(rows=1, cols=len(col_headers))
    lines_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    lines_table.autofit = False

    hdr_cells = lines_table.rows[0].cells
    for i, title in enumerate(col_headers):
        hdr_cells[i].width = col_widths[i]
        _set_cell_background(hdr_cells[i], HEX_MIDNIGHT)
        _set_cell_margins(hdr_cells[i], top=90, bottom=90, left=90, right=90)
        _set_cell_borders(
            hdr_cells[i],
            top=("single", 4, HEX_MIDNIGHT),
            bottom=("single", 12, HEX_GOLD),
            left=("none", 0, "auto"),
            right=("none", 0, "auto"),
        )
        p = hdr_cells[i].paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if i >= 1 else WD_ALIGN_PARAGRAPH.LEFT
        r = p.add_run(title)
        r.bold = True
        r.font.size = Pt(8)
        r.font.color.rgb = RGBColor(255, 255, 255)

    for idx, line in enumerate(lines):
        row = lines_table.add_row()
        row_cells = row.cells
        amounts = calculate_quote_line(
            line.quantity, line.unit_price, line.discount_type,
            line.discount_value, line.tax_percentage,
        )
        bg = HEX_PALE if idx % 2 == 1 else "FFFFFF"
        qty_str = str(int(line.quantity)) if line.quantity % 1 == 0 else str(line.quantity)

        if has_discount:
            disc_label = (
                f"{line.discount_value}%"
                if line.discount_type == "percentage"
                else _money(line.discount_value)
            ) if (line.discount_value and line.discount_value > Decimal("0.00")) else "—"

            row_data = [
                (line.description, WD_ALIGN_PARAGRAPH.LEFT, False),
                (qty_str, WD_ALIGN_PARAGRAPH.RIGHT, False),
                (_money(line.unit_price), WD_ALIGN_PARAGRAPH.RIGHT, False),
                (disc_label, WD_ALIGN_PARAGRAPH.RIGHT, False),
                (_money(amounts["net"]), WD_ALIGN_PARAGRAPH.RIGHT, False),
            ]
        else:
            row_data = [
                (line.description, WD_ALIGN_PARAGRAPH.LEFT, False),
                (qty_str, WD_ALIGN_PARAGRAPH.RIGHT, False),
                (_money(line.unit_price), WD_ALIGN_PARAGRAPH.RIGHT, False),
                (_money(amounts["net"]), WD_ALIGN_PARAGRAPH.RIGHT, False),
            ]

        for i, (text, align, is_bold) in enumerate(row_data):
            row_cells[i].width = col_widths[i]
            _set_cell_background(row_cells[i], bg)
            _set_cell_margins(row_cells[i], top=80, bottom=80, left=90, right=90)
            _set_cell_borders(
                row_cells[i],
                top=("single", 4, HEX_GRID),
                bottom=("single", 4, HEX_GRID),
                left=("none", 0, "auto"),
                right=("none", 0, "auto"),
            )
            p = row_cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.alignment = align
            r = p.add_run(text)
            r.bold = is_bold
            r.font.size = Pt(7.5)
            r.font.color.rgb = COLOR_INK

    p_sp3 = doc.add_paragraph()
    p_sp3.paragraph_format.space_before = Pt(4)
    p_sp3.paragraph_format.space_after = Pt(2)

    # Récapitulatif des totaux
    tax_totals = {}
    for line in lines:
        amount = calculate_quote_line(
            line.quantity, line.unit_price, line.discount_type,
            line.discount_value, line.tax_percentage,
        )["tax"]
        key = (line.tax_name or "Sans taxe", line.tax_percentage or Decimal("0.00"))
        tax_totals[key] = tax_totals.get(key, Decimal("0.00")) + amount

    summary_rows = []
    if has_discount:
        summary_rows.append(("Total brut HT", _money(totals["gross"])))
        summary_rows.append(("Remises", f"- {_money(totals['discount'])}"))
        summary_rows.append(("Total net HT", _money(totals["net"])))
    else:
        summary_rows.append(("Total HT", _money(totals["net"])))

    summary_rows.extend(
        [f"{name} ({percentage} %)", _money(amount)]
        for (name, percentage), amount in sorted(tax_totals.items())
        if amount
    )
    summary_rows.append(("Total TTC", _money(totals["total"])))

    if invoice.total_paid > Decimal("0.00"):
        summary_rows.append(("Montant déjà réglé", f"- {_money(invoice.total_paid)}"))
        summary_rows.append(("Net à payer", _money(invoice.remaining_balance)))

    sum_table = doc.add_table(rows=len(summary_rows), cols=2)
    sum_table.alignment = WD_TABLE_ALIGNMENT.RIGHT
    sum_table.autofit = False
    sum_table.columns[0].width = Mm(54)
    sum_table.columns[1].width = Mm(45)

    for i, (lbl, val) in enumerate(summary_rows):
        r_cells = sum_table.rows[i].cells
        r_cells[0].width = Mm(54)
        r_cells[1].width = Mm(45)
        _set_cell_margins(r_cells[0], top=60, bottom=60, left=80, right=80)
        _set_cell_margins(r_cells[1], top=60, bottom=60, left=80, right=80)

        is_grand = (lbl in {"Total TTC", "Net à payer"})
        if is_grand:
            _set_cell_borders(
                r_cells[0],
                top=("single", 12, HEX_GOLD),
                bottom=("none", 0, "auto"),
                left=("none", 0, "auto"),
                right=("none", 0, "auto"),
            )
            _set_cell_borders(
                r_cells[1],
                top=("single", 12, HEX_GOLD),
                bottom=("none", 0, "auto"),
                left=("none", 0, "auto"),
                right=("none", 0, "auto"),
            )
        else:
            _set_cell_borders(
                r_cells[0],
                top=("none", 0, "auto"),
                bottom=("none", 0, "auto"),
                left=("none", 0, "auto"),
                right=("none", 0, "auto"),
            )
            _set_cell_borders(
                r_cells[1],
                top=("none", 0, "auto"),
                bottom=("none", 0, "auto"),
                left=("none", 0, "auto"),
                right=("none", 0, "auto"),
            )

        p0 = r_cells[0].paragraphs[0]
        p0.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p0.paragraph_format.space_after = Pt(0)
        r0 = p0.add_run(lbl)
        r0.bold = is_grand
        r0.font.size = Pt(9.5 if is_grand else 8.5)
        r0.font.color.rgb = COLOR_MIDNIGHT if is_grand else COLOR_INK

        p1 = r_cells[1].paragraphs[0]
        p1.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p1.paragraph_format.space_after = Pt(0)
        r1 = p1.add_run(val)
        r1.bold = is_grand
        r1.font.size = Pt(9.5 if is_grand else 8.5)
        r1.font.color.rgb = COLOR_MIDNIGHT if is_grand else COLOR_INK

    # Notes / Observations
    if invoice.notes:
        p_sp4 = doc.add_paragraph()
        p_sp4.paragraph_format.space_before = Pt(6)
        p_sp4.paragraph_format.space_after = Pt(2)

        notes_table = doc.add_table(rows=1, cols=1)
        notes_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        notes_table.autofit = False
        notes_table.columns[0].width = Mm(174)
        c_notes = notes_table.cell(0, 0)
        _set_cell_background(c_notes, HEX_PALE)
        _set_cell_margins(c_notes, top=90, bottom=90, left=120, right=120)
        _set_cell_borders(
            c_notes,
            top=("single", 4, HEX_GRID),
            bottom=("single", 4, HEX_GRID),
            left=("single", 18, HEX_GOLD),
            right=("single", 4, HEX_GRID),
        )

        p_n = c_notes.paragraphs[0]
        p_n.paragraph_format.space_after = Pt(2)
        r_nh = p_n.add_run("CONDITIONS & REMARQUES\n")
        r_nh.bold = True
        r_nh.font.size = Pt(8)
        r_nh.font.color.rgb = COLOR_MIDNIGHT

        p_nb = c_notes.add_paragraph()
        p_nb.paragraph_format.space_after = Pt(0)
        p_nb.paragraph_format.line_spacing = 1.15
        r_nt = p_nb.add_run(invoice.notes)
        r_nt.font.size = Pt(8)
        r_nt.font.color.rgb = COLOR_INK

    # RIB / Virement Bancaire
    if company.bank_name or company.rib or getattr(company, "display_rib", None):
        p_sp5 = doc.add_paragraph()
        p_sp5.paragraph_format.space_before = Pt(6)
        p_sp5.paragraph_format.space_after = Pt(2)

        bank_name = getattr(company, "display_bank_name", company.bank_name or "BANK OF AFRICA (BMCE)")
        rib_value = getattr(company, "display_rib", company.formatted_rib or company.rib or "011 780 0000642100038454 63")

        bank_table = doc.add_table(rows=1, cols=1)
        bank_table.alignment = WD_TABLE_ALIGNMENT.CENTER
        bank_table.autofit = False
        bank_table.columns[0].width = Mm(174)
        c_bank = bank_table.cell(0, 0)
        _set_cell_background(c_bank, HEX_GOLD_LIGHT)
        _set_cell_margins(c_bank, top=90, bottom=90, left=120, right=120)
        _set_cell_borders(
            c_bank,
            top=("single", 4, HEX_GRID),
            bottom=("single", 4, HEX_GRID),
            left=("single", 22, HEX_GOLD),
            right=("single", 4, HEX_GRID),
        )

        p_b = c_bank.paragraphs[0]
        p_b.paragraph_format.space_after = Pt(2)
        r_bh = p_b.add_run("RIB (Virement bancaire)\n")
        r_bh.bold = True
        r_bh.font.size = Pt(8)
        r_bh.font.color.rgb = COLOR_MIDNIGHT

        p_bb = c_bank.add_paragraph()
        p_bb.paragraph_format.space_after = Pt(0)
        p_bb.paragraph_format.line_spacing = 1.2
        p_bb.add_run("• ").bold = True
        r_bn_lbl = p_bb.add_run("Banque : ")
        r_bn_lbl.bold = True
        p_bb.add_run(f"{bank_name}     ")
        p_bb.add_run("• ").bold = True
        r_rb_lbl = p_bb.add_run("RIB : ")
        r_rb_lbl.bold = True
        r_rib = p_bb.add_run(f"{rib_value}\n")
        r_rib.bold = True
        r_rib.font.color.rgb = COLOR_MIDNIGHT

        p_bb.add_run("• ").bold = True
        r_note_lbl = p_bb.add_run("Note : ")
        r_note_lbl.bold = True
        r_note = p_bb.add_run(f"Merci de rappeler la référence de la facture ({number}) sur l'ordre de virement.")
        r_note.font.color.rgb = COLOR_GOLD_DARK

    # Cadre de signature & Cachet officiel
    p_sp6 = doc.add_paragraph()
    p_sp6.paragraph_format.space_before = Pt(6)
    p_sp6.paragraph_format.space_after = Pt(2)

    sign_table = doc.add_table(rows=1, cols=2)
    sign_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    sign_table.autofit = False
    sign_table.columns[0].width = Mm(87)
    sign_table.columns[1].width = Mm(87)

    # Colonne gauche : Conditions & Montant
    c_left = sign_table.cell(0, 0)
    _set_cell_background(c_left, HEX_PALE)
    _set_cell_margins(c_left, top=90, bottom=90, left=120, right=120)
    _set_cell_borders(
        c_left,
        top=("single", 4, HEX_GRID),
        bottom=("single", 4, HEX_GRID),
        left=("single", 14, HEX_GOLD),
        right=("single", 4, HEX_GRID),
    )
    p_l = c_left.paragraphs[0]
    p_l.paragraph_format.space_after = Pt(2)
    r_lh = p_l.add_run("RÈGLEMENT & CONDITIONS\n")
    r_lh.bold = True
    r_lh.font.size = Pt(8)
    r_lh.font.color.rgb = COLOR_MIDNIGHT

    p_lb = c_left.add_paragraph()
    p_lb.paragraph_format.space_after = Pt(20)
    r_lb = p_lb.add_run(f"Facture arrêtée à la somme de {_money(totals['total'])} MAD TTC.\nEn votre aimable règlement par virement ou chèque.")
    r_lb.font.size = Pt(7.5)
    r_lb.font.color.rgb = COLOR_MUTED

    # Colonne droite : Entreprise avec Cachet
    c_stamp = sign_table.cell(0, 1)
    _set_cell_background(c_stamp, HEX_PALE)
    _set_cell_margins(c_stamp, top=90, bottom=90, left=120, right=120)
    _set_cell_borders(
        c_stamp,
        top=("single", 4, HEX_GRID),
        bottom=("single", 4, HEX_GRID),
        left=("single", 14, HEX_GOLD),
        right=("single", 4, HEX_GRID),
    )
    p_comp = c_stamp.paragraphs[0]
    p_comp.paragraph_format.space_after = Pt(2)
    r_ch = p_comp.add_run(f"POUR {company.name.upper()}\n")
    r_ch.bold = True
    r_ch.font.size = Pt(8)
    r_ch.font.color.rgb = COLOR_MIDNIGHT

    p_cb = c_stamp.add_paragraph()
    p_cb.paragraph_format.space_after = Pt(2)
    r_cb = p_cb.add_run("Cachet officiel & Signature autorisée :")
    r_cb.font.size = Pt(7.5)
    r_cb.font.color.rgb = COLOR_MUTED

    if company.stamp and os.path.exists(company.stamp.path):
        p_img = c_stamp.add_paragraph()
        p_img.paragraph_format.space_before = Pt(2)
        p_img.paragraph_format.space_after = Pt(2)
        r_img = p_img.add_run()
        r_img.add_picture(company.stamp.path, width=Mm(45))
    else:
        p_cb.paragraph_format.space_after = Pt(24)

    # Pied de page légal
    footer = section.footer
    footer_p = footer.paragraphs[0]
    footer_p.text = ""

    footer_table = footer.add_table(rows=1, cols=2, width=Mm(174))
    footer_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    footer_table.autofit = False
    footer_table.columns[0].width = Mm(150)
    footer_table.columns[1].width = Mm(24)

    c_f_left = footer_table.cell(0, 0)
    c_f_right = footer_table.cell(0, 1)

    _set_cell_margins(c_f_left, top=40, bottom=0, left=0, right=40)
    _set_cell_margins(c_f_right, top=40, bottom=0, left=40, right=0)
    _set_cell_borders(
        c_f_left,
        top=("single", 6, HEX_GOLD),
        bottom=("none", 0, "auto"),
        left=("none", 0, "auto"),
        right=("none", 0, "auto"),
    )
    _set_cell_borders(
        c_f_right,
        top=("single", 6, HEX_GOLD),
        bottom=("none", 0, "auto"),
        left=("none", 0, "auto"),
        right=("none", 0, "auto"),
    )

    p_fl = c_f_left.paragraphs[0]
    p_fl.paragraph_format.space_after = Pt(0)
    r_leg = p_fl.add_run(company.legal_notice())
    r_leg.font.size = Pt(6.8)
    r_leg.font.color.rgb = COLOR_MUTED

    p_fr = c_f_right.paragraphs[0]
    p_fr.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_fr.paragraph_format.space_after = Pt(0)
    r_pg = p_fr.add_run("Page ")
    r_pg.font.size = Pt(6.8)
    r_pg.font.color.rgb = COLOR_MUTED
    r_pg._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>'))
    r_pg._r.append(parse_xml(f'<w:instrText {nsdecls("w")} xml:space="preserve"> PAGE </w:instrText>'))
    r_pg._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>'))
    r_pg._r.append(parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>'))

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


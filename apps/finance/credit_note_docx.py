from decimal import Decimal
from io import BytesIO
import os

from django.utils.dateformat import format as format_date
import docx
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Mm, Pt, RGBColor

from apps.finance.models import CompanySettings
from apps.finance.quote_calculations import calculate_quote_totals

COLOR_MIDNIGHT = RGBColor(15, 23, 42)
COLOR_BURGUNDY = RGBColor(153, 27, 27)
COLOR_MUTED = RGBColor(71, 85, 105)
COLOR_GOLD = RGBColor(197, 155, 39)
COLOR_GOLD_DARK = RGBColor(154, 117, 22)

HEX_BURGUNDY = "991B1B"
HEX_BURGUNDY_LIGHT = "FEF2F2"
HEX_PALE = "F8FAFC"
HEX_GRID = "E2E8F0"
HEX_MIDNIGHT = "0F172A"


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


def build_credit_note_docx(credit_note):
    company = CompanySettings.get_settings()
    doc = docx.Document()

    section = doc.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(16)
    section.bottom_margin = Mm(16)
    section.left_margin = Mm(18)
    section.right_margin = Mm(18)

    number = credit_note.number or f"Avoir brouillon #{credit_note.pk}"
    issue_date = credit_note.issue_date or credit_note.created_at.date()

    # 1. En-tête
    header_table = doc.add_table(rows=1, cols=2)
    header_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    header_table.autofit = False
    header_table.columns[0].width = Mm(104)
    header_table.columns[1].width = Mm(70)

    cell_comp = header_table.cell(0, 0)
    p_comp = cell_comp.paragraphs[0]
    p_comp.paragraph_format.space_after = Pt(2)

    if company.logo and os.path.exists(company.logo.path):
        run_logo = p_comp.add_run()
        run_logo.add_picture(company.logo.path, height=Mm(14))
        p_comp = cell_comp.add_paragraph()
        p_comp.paragraph_format.space_after = Pt(2)

    r_cname = p_comp.add_run(company.name + "\n")
    r_cname.bold = True
    r_cname.font.size = Pt(11)
    r_cname.font.color.rgb = COLOR_MIDNIGHT

    if company.tagline:
        r_tag = p_comp.add_run(company.tagline + "\n")
        r_tag.italic = True
        r_tag.font.size = Pt(8)
        r_tag.font.color.rgb = COLOR_MUTED

    coord_parts = []
    if company.address:
        coord_parts.append(company.address)
    if company.city:
        coord_parts.append(f"{company.city} · {company.country}")
    if company.phone:
        coord_parts.append(f"Tél : {company.phone}")
    if company.email:
        coord_parts.append(f"Email : {company.email}")
    if coord_parts:
        r_coord = p_comp.add_run(" · ".join(coord_parts))
        r_coord.font.size = Pt(7.5)
        r_coord.font.color.rgb = COLOR_MUTED

    # Meta Avoir
    cell_meta = header_table.cell(0, 1)
    p_meta = cell_meta.paragraphs[0]
    p_meta.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_meta.paragraph_format.space_after = Pt(2)

    r_t = p_meta.add_run("FACTURE D'AVOIR\n")
    r_t.bold = True
    r_t.font.size = Pt(15)
    r_t.font.color.rgb = COLOR_BURGUNDY

    r_st = p_meta.add_run("NOTE DE CRÉDIT\n")
    r_st.font.size = Pt(9)
    r_st.font.color.rgb = COLOR_MUTED

    r_ref = p_meta.add_run(f"RÉF. : {number}\n")
    r_ref.bold = True
    r_ref.font.size = Pt(9.5)
    r_ref.font.color.rgb = COLOR_MIDNIGHT

    r_dt = p_meta.add_run(f"Date : {format_date(issue_date, 'd/m/Y')}\n")
    r_dt.font.size = Pt(8.5)
    r_dt.font.color.rgb = COLOR_MUTED

    r_orig = p_meta.add_run(f"Facture d'origine : {credit_note.invoice.number or credit_note.invoice.pk}")
    r_orig.bold = True
    r_orig.font.size = Pt(8.5)
    r_orig.font.color.rgb = COLOR_BURGUNDY

    # 2. Bandeau Client & Motif
    p_sp = doc.add_paragraph()
    p_sp.paragraph_format.space_before = Pt(6)
    p_sp.paragraph_format.space_after = Pt(2)

    info_table = doc.add_table(rows=1, cols=2)
    info_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    info_table.autofit = False
    info_table.columns[0].width = Mm(87)
    info_table.columns[1].width = Mm(87)

    c_cli = info_table.cell(0, 0)
    _set_cell_background(c_cli, HEX_PALE)
    _set_cell_margins(c_cli, top=80, bottom=80, left=100, right=100)
    _set_cell_borders(c_cli, top=("single", 4, HEX_GRID), bottom=("single", 4, HEX_GRID), left=("single", 14, HEX_MIDNIGHT), right=("single", 4, HEX_GRID))

    p_cli = c_cli.paragraphs[0]
    p_cli.paragraph_format.space_after = Pt(2)
    r_ch = p_cli.add_run("CLIENT CRÉDITÉ\n")
    r_ch.bold = True
    r_ch.font.size = Pt(8)
    r_ch.font.color.rgb = COLOR_MIDNIGHT

    cust_info = credit_note.customer_snapshot or {"name": credit_note.customer.name}
    r_cn = p_cli.add_run(f"{cust_info.get('name', '')}\n")
    r_cn.bold = True
    r_cn.font.size = Pt(9)
    if cust_info.get("address"):
        p_cli.add_run(f"{cust_info['address']}\n").font.size = Pt(8)
    if cust_info.get("city"):
        p_cli.add_run(f"{cust_info['city']} · {cust_info.get('country', 'Maroc')}\n").font.size = Pt(8)
    if cust_info.get("ice"):
        p_cli.add_run(f"ICE : {cust_info['ice']}  ").font.size = Pt(8)
    if cust_info.get("tax_identifier"):
        p_cli.add_run(f"IF : {cust_info['tax_identifier']}").font.size = Pt(8)

    c_mot = info_table.cell(0, 1)
    _set_cell_background(c_mot, HEX_BURGUNDY_LIGHT)
    _set_cell_margins(c_mot, top=80, bottom=80, left=100, right=100)
    _set_cell_borders(c_mot, top=("single", 4, "FCA5A5"), bottom=("single", 4, "FCA5A5"), left=("single", 14, HEX_BURGUNDY), right=("single", 4, "FCA5A5"))

    p_mot = c_mot.paragraphs[0]
    p_mot.paragraph_format.space_after = Pt(2)
    r_mh = p_mot.add_run("MOTIF DE L'AVOIR\n")
    r_mh.bold = True
    r_mh.font.size = Pt(8)
    r_mh.font.color.rgb = COLOR_BURGUNDY

    r_mn = p_mot.add_run(f"{credit_note.get_reason_display()}\n")
    r_mn.bold = True
    r_mn.font.size = Pt(9)
    if credit_note.reason_details:
        p_mot.add_run(f"{credit_note.reason_details}\n").font.size = Pt(8)
    p_mot.add_run(f"Rattaché à la facture {credit_note.invoice.number or credit_note.invoice.pk}").font.size = Pt(7.5)

    # 3. Lignes de l'avoir
    p_sp2 = doc.add_paragraph()
    p_sp2.paragraph_format.space_before = Pt(6)
    p_sp2.paragraph_format.space_after = Pt(2)

    lines = list(credit_note.lines.all())
    for line in lines:
        line.amounts = calculate_quote_totals([line])

    lines_table = doc.add_table(rows=1 + len(lines), cols=5)
    lines_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    lines_table.autofit = False
    col_widths = [Mm(84), Mm(18), Mm(24), Mm(18), Mm(30)]
    for row in lines_table.rows:
        for idx, width in enumerate(col_widths):
            row.cells[idx].width = width

    # En-têtes du tableau
    headers = ["DÉSIGNATION / PRESTATION RETOURNÉE", "QTÉ", "P.U. HT", "TVA", "NET HT CRÉDITÉ"]
    for idx, h_text in enumerate(headers):
        c = lines_table.cell(0, idx)
        _set_cell_background(c, HEX_BURGUNDY)
        _set_cell_margins(c, top=60, bottom=60, left=70, right=70)
        p = c.paragraphs[0]
        if idx > 0:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r = p.add_run(h_text)
        r.bold = True
        r.font.size = Pt(7.5)
        r.font.color.rgb = RGBColor(255, 255, 255)

    for row_idx, line in enumerate(lines, start=1):
        c_bg = HEX_PALE if row_idx % 2 == 0 else "FFFFFF"
        # Col 0: Description
        c0 = lines_table.cell(row_idx, 0)
        _set_cell_background(c0, c_bg)
        _set_cell_margins(c0, top=60, bottom=60, left=70, right=70)
        _set_cell_borders(c0, bottom=("single", 2, HEX_GRID))
        c0.paragraphs[0].add_run(line.description).font.size = Pt(8.5)

        # Col 1: Qté
        c1 = lines_table.cell(row_idx, 1)
        _set_cell_background(c1, c_bg)
        _set_cell_margins(c1, top=60, bottom=60, left=70, right=70)
        _set_cell_borders(c1, bottom=("single", 2, HEX_GRID))
        p1 = c1.paragraphs[0]
        p1.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p1.add_run(f"{line.quantity:g}").font.size = Pt(8.5)

        # Col 2: P.U. HT
        c2 = lines_table.cell(row_idx, 2)
        _set_cell_background(c2, c_bg)
        _set_cell_margins(c2, top=60, bottom=60, left=70, right=70)
        _set_cell_borders(c2, bottom=("single", 2, HEX_GRID))
        p2 = c2.paragraphs[0]
        p2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p2.add_run(_money(line.unit_price)).font.size = Pt(8.5)

        # Col 3: TVA
        c3 = lines_table.cell(row_idx, 3)
        _set_cell_background(c3, c_bg)
        _set_cell_margins(c3, top=60, bottom=60, left=70, right=70)
        _set_cell_borders(c3, bottom=("single", 2, HEX_GRID))
        p3 = c3.paragraphs[0]
        p3.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p3.add_run(f"{line.tax_percentage} %").font.size = Pt(8.5)

        # Col 4: Net HT
        c4 = lines_table.cell(row_idx, 4)
        _set_cell_background(c4, c_bg)
        _set_cell_margins(c4, top=60, bottom=60, left=70, right=70)
        _set_cell_borders(c4, bottom=("single", 2, HEX_GRID))
        p4 = c4.paragraphs[0]
        p4.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r4 = p4.add_run(_money(line.amounts["net"]))
        r4.bold = True
        r4.font.size = Pt(8.5)

    # 4. Totaux
    totals = calculate_quote_totals(lines)
    p_sp3 = doc.add_paragraph()
    p_sp3.paragraph_format.space_before = Pt(6)
    p_sp3.paragraph_format.space_after = Pt(2)

    sum_table = doc.add_table(rows=3, cols=2)
    sum_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    sum_table.autofit = False
    sum_table.columns[0].width = Mm(120)
    sum_table.columns[1].width = Mm(54)

    rows_data = [
        ("TOTAL NET HT CRÉDITÉ", f"− {_money(totals['net'])} MAD", False),
        ("TVA DÉDUCTIBLE CRÉDITÉE", f"− {_money(totals['tax'])} MAD", False),
        ("NET À CRÉDITER / DÉDUIRE TTC", f"− {_money(totals['total'])} MAD", True),
    ]

    for r_i, (lbl, val, is_grand) in enumerate(rows_data):
        c_lbl = sum_table.cell(r_i, 0)
        c_val = sum_table.cell(r_i, 1)
        _set_cell_margins(c_lbl, top=40, bottom=40, left=60, right=60)
        _set_cell_margins(c_val, top=40, bottom=40, left=60, right=60)

        if is_grand:
            _set_cell_background(c_lbl, HEX_BURGUNDY_LIGHT)
            _set_cell_background(c_val, HEX_BURGUNDY_LIGHT)
            _set_cell_borders(c_lbl, bottom=("single", 12, HEX_BURGUNDY))
            _set_cell_borders(c_val, bottom=("single", 12, HEX_BURGUNDY))

        p_l = c_lbl.paragraphs[0]
        p_l.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_l = p_l.add_run(lbl)
        r_l.bold = is_grand
        r_l.font.size = Pt(9.5 if is_grand else 8)
        if is_grand:
            r_l.font.color.rgb = COLOR_BURGUNDY

        p_v = c_val.paragraphs[0]
        p_v.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_v = p_v.add_run(val)
        r_v.bold = is_grand
        r_v.font.size = Pt(9.5 if is_grand else 8)
        if is_grand:
            r_v.font.color.rgb = COLOR_BURGUNDY

    # 5. Bloc de validation & Cachet officiel
    p_sp4 = doc.add_paragraph()
    p_sp4.paragraph_format.space_before = Pt(6)
    p_sp4.paragraph_format.space_after = Pt(2)

    sign_table = doc.add_table(rows=1, cols=2)
    sign_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    sign_table.autofit = False
    sign_table.columns[0].width = Mm(87)
    sign_table.columns[1].width = Mm(87)

    c_left = sign_table.cell(0, 0)
    _set_cell_background(c_left, HEX_PALE)
    _set_cell_margins(c_left, top=80, bottom=80, left=100, right=100)
    _set_cell_borders(c_left, top=("single", 4, HEX_GRID), bottom=("single", 4, HEX_GRID), left=("single", 14, HEX_BURGUNDY), right=("single", 4, HEX_GRID))

    p_sl = c_left.paragraphs[0]
    p_sl.paragraph_format.space_after = Pt(2)
    r_lh = p_sl.add_run("IMPUTATION COMPTABLE & FISCALE\n")
    r_lh.bold = True
    r_lh.font.size = Pt(8)
    r_lh.font.color.rgb = COLOR_MIDNIGHT

    p_lb = c_left.add_paragraph()
    p_lb.paragraph_format.space_after = Pt(12)
    r_lb = p_lb.add_run(
        f"Avoir de {_money(totals['total'])} MAD imputé sur le compte client.\n"
        f"TVA et stocks régularisés conformément aux règles comptables marocaines."
    )
    r_lb.font.size = Pt(7.5)
    r_lb.font.color.rgb = COLOR_MUTED

    c_right = sign_table.cell(0, 1)
    _set_cell_background(c_right, HEX_PALE)
    _set_cell_margins(c_right, top=80, bottom=80, left=100, right=100)
    _set_cell_borders(c_right, top=("single", 4, HEX_GRID), bottom=("single", 4, HEX_GRID), left=("single", 14, HEX_BURGUNDY), right=("single", 4, HEX_GRID))

    p_comp = c_right.paragraphs[0]
    p_comp.paragraph_format.space_after = Pt(2)
    r_ch = p_comp.add_run(f"POUR {company.name.upper()}\n")
    r_ch.bold = True
    r_ch.font.size = Pt(8)
    r_ch.font.color.rgb = COLOR_MIDNIGHT

    p_cb = c_right.add_paragraph()
    p_cb.paragraph_format.space_after = Pt(2)
    r_cb = p_cb.add_run("Cachet officiel & Signature autorisée :")
    r_cb.font.size = Pt(7.5)
    r_cb.font.color.rgb = COLOR_MUTED

    if company.stamp and os.path.exists(company.stamp.path):
        p_img = c_right.add_paragraph()
        p_img.paragraph_format.space_before = Pt(2)
        p_img.paragraph_format.space_after = Pt(2)
        r_img = p_img.add_run()
        r_img.add_picture(company.stamp.path, width=Mm(45))
    else:
        p_cb.paragraph_format.space_after = Pt(24)

    # 6. Pied de page
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
    _set_cell_borders(c_f_left, top=("single", 6, HEX_BURGUNDY), bottom=("none", 0, "auto"), left=("none", 0, "auto"), right=("none", 0, "auto"))
    _set_cell_borders(c_f_right, top=("single", 6, HEX_BURGUNDY), bottom=("none", 0, "auto"), left=("none", 0, "auto"), right=("none", 0, "auto"))

    p_fl = c_f_left.paragraphs[0]
    p_fl.paragraph_format.space_after = Pt(0)
    r_notice = p_fl.add_run(company.legal_notice())
    r_notice.font.size = Pt(6.5)
    r_notice.font.color.rgb = COLOR_MUTED

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


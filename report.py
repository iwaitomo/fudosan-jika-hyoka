"""評価結果を PDF / Excel / Word で出力するモジュール。

app.py から汎用的な ctx（辞書）を受け取り、各形式のバイト列を返す。
ctx の形（app.py 側で組み立てる）:

    {
        "title": "不動産（土地）簡易時価評価 報告書",
        "date":  "2026-09-28",
        "sections": [
            {"heading": "対象土地", "kind": "kv",
             "rows": [("所在", "神奈川県…"), ("地積", "90.00 ㎡"), …]},
            {"heading": "評価方法の比較", "kind": "table",
             "columns": ["評価方法", "㎡単価(円)"],
             "rows": [["最寄の公示・調査地点", "123,000"], …]},
        ],
        "notes": ["※ …", …],
    }

日本語フォントは reportlab 同梱の CID フォント（HeiseiKakuGo-W5）を使うので、
外部フォントファイルを同梱しなくても Streamlit Cloud で文字化けしない。
"""
from __future__ import annotations

import io


# ============================================================
#  Excel（.xlsx）
# ============================================================
def to_excel(ctx: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "評価結果"
    ws.sheet_view.showGridLines = False

    thin = Side(style="thin", color="BBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="1F4E79")
    head_font = Font(bold=True, color="FFFFFF")
    sec_font = Font(bold=True, size=12, color="1F4E79")
    wrap = Alignment(vertical="top", wrap_text=True)

    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 52

    r = 1
    ws.cell(r, 1, ctx.get("title", "評価結果")).font = Font(bold=True, size=15)
    r += 1
    ws.cell(r, 1, f"作成日：{ctx.get('date', '')}").font = Font(color="666666")
    r += 2

    for sec in ctx.get("sections", []):
        ws.cell(r, 1, sec.get("heading", "")).font = sec_font
        r += 1
        if sec.get("kind") == "kv":
            for k, v in sec.get("rows", []):
                c1 = ws.cell(r, 1, str(k)); c2 = ws.cell(r, 2, str(v))
                c1.font = Font(bold=True); c1.border = border; c2.border = border
                c1.alignment = wrap; c2.alignment = wrap
                r += 1
        else:  # table
            cols = sec.get("columns", [])
            for j, name in enumerate(cols, start=1):
                c = ws.cell(r, j, str(name))
                c.fill = head_fill; c.font = head_font; c.border = border
                c.alignment = Alignment(horizontal="center")
            r += 1
            for row in sec.get("rows", []):
                for j, val in enumerate(row, start=1):
                    c = ws.cell(r, j, "" if val is None else str(val))
                    c.border = border; c.alignment = wrap
                r += 1
        r += 1  # セクション間の空行

    for note in ctx.get("notes", []):
        ws.cell(r, 1, str(note)).font = Font(size=9, color="666666")
        r += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ============================================================
#  Word（.docx）
# ============================================================
def to_word(ctx: dict) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    # 既定フォントを日本語に
    style = doc.styles["Normal"]
    style.font.name = "Yu Gothic"
    style.font.size = Pt(10.5)
    try:
        from docx.oxml.ns import qn
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "Yu Gothic")
    except Exception:
        pass

    h = doc.add_heading(ctx.get("title", "評価結果"), level=0)
    p = doc.add_paragraph(f"作成日：{ctx.get('date', '')}")
    p.runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    for sec in ctx.get("sections", []):
        doc.add_heading(sec.get("heading", ""), level=1)
        if sec.get("kind") == "kv":
            rows = sec.get("rows", [])
            t = doc.add_table(rows=len(rows), cols=2)
            t.style = "Light Grid Accent 1"
            for i, (k, v) in enumerate(rows):
                t.cell(i, 0).text = str(k)
                t.cell(i, 1).text = str(v)
                for run in t.cell(i, 0).paragraphs[0].runs:
                    run.font.bold = True
        else:  # table
            cols = sec.get("columns", [])
            body = sec.get("rows", [])
            t = doc.add_table(rows=1 + len(body), cols=len(cols))
            t.style = "Light Grid Accent 1"
            for j, name in enumerate(cols):
                cell = t.cell(0, j)
                cell.text = str(name)
                for run in cell.paragraphs[0].runs:
                    run.font.bold = True
            for i, row in enumerate(body, start=1):
                for j, val in enumerate(row):
                    t.cell(i, j).text = "" if val is None else str(val)

    if ctx.get("notes"):
        doc.add_paragraph("")
        for note in ctx["notes"]:
            para = doc.add_paragraph(str(note))
            para.runs[0].font.size = Pt(9)
            para.runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ============================================================
#  PDF（.pdf）
# ============================================================
_FONT = "HeiseiKakuGo-W5"      # ゴシック（見出し等）
_FONT_MIN = "HeiseiMin-W3"      # 明朝（本文）


def _ensure_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    for name in (_FONT, _FONT_MIN):
        try:
            pdfmetrics.getFont(name)
        except Exception:
            pdfmetrics.registerFont(UnicodeCIDFont(name))


def to_pdf(ctx: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle)

    _ensure_fonts()
    base = getSampleStyleSheet()
    title_st = ParagraphStyle("t", parent=base["Title"], fontName=_FONT, fontSize=17, leading=22)
    date_st = ParagraphStyle("d", parent=base["Normal"], fontName=_FONT_MIN, fontSize=9,
                             textColor=colors.HexColor("#666666"))
    head_st = ParagraphStyle("h", parent=base["Heading2"], fontName=_FONT, fontSize=12,
                             textColor=colors.HexColor("#1F4E79"), spaceBefore=10, spaceAfter=4)
    cell_st = ParagraphStyle("c", parent=base["Normal"], fontName=_FONT_MIN, fontSize=9.5, leading=13)
    cellb_st = ParagraphStyle("cb", parent=cell_st, fontName=_FONT)
    headcell_st = ParagraphStyle("hc", parent=cell_st, fontName=_FONT, textColor=colors.white)
    note_st = ParagraphStyle("n", parent=base["Normal"], fontName=_FONT_MIN, fontSize=8,
                             leading=11, textColor=colors.HexColor("#666666"))

    def P(text, st=cell_st):
        return Paragraph(("" if text is None else str(text)).replace("\n", "<br/>"), st)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=ctx.get("title", "評価結果"),
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm)
    avail = A4[0] - 36 * mm
    flow = [P(ctx.get("title", "評価結果"), title_st),
            P(f"作成日：{ctx.get('date', '')}", date_st), Spacer(1, 6 * mm)]

    grid = colors.HexColor("#BBBBBB")
    headbg = colors.HexColor("#1F4E79")

    for sec in ctx.get("sections", []):
        flow.append(P(sec.get("heading", ""), head_st))
        if sec.get("kind") == "kv":
            data = [[P(k, cellb_st), P(v)] for k, v in sec.get("rows", [])]
            if not data:
                continue
            tbl = Table(data, colWidths=[avail * 0.36, avail * 0.64])
            tbl.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, grid),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F0F4FA")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
        else:  # table
            cols = sec.get("columns", [])
            ncol = len(cols)
            data = [[P(c, headcell_st) for c in cols]]
            for row in sec.get("rows", []):
                data.append([P(v) for v in row])
            if ncol == 2:
                widths = [avail * 0.60, avail * 0.40]
            else:
                widths = [avail / ncol] * ncol
            tbl = Table(data, colWidths=widths, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, grid),
                ("BACKGROUND", (0, 0), (-1, 0), headbg),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
        flow.append(tbl)
        flow.append(Spacer(1, 4 * mm))

    if ctx.get("notes"):
        flow.append(Spacer(1, 2 * mm))
        for note in ctx["notes"]:
            flow.append(P(note, note_st))

    doc.build(flow)
    return buf.getvalue()

"""PDF reports (ReportLab): student result cards and the examination summary."""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .context import ReportData, gather, money

NAVY = colors.HexColor("#13294b")
BLUE = colors.HexColor("#2a78d6")
RED = colors.HexColor("#d03b3b")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
LINE = colors.HexColor("#dde3ec")
SOFT = colors.HexColor("#f5f7fb")
OK_BG = colors.HexColor("#e8f1fc")
BAD_BG = colors.HexColor("#fdecec")
NEUTRAL_BG = colors.HexColor("#f1f3f7")
WARN_BG = colors.HexColor("#fff6e0")

# --- fonts: a Unicode TTF if available (names may not be ASCII), else Helvetica -------
FONT, BOLD = "Helvetica", "Helvetica-Bold"
for regular, bold in [("segoeui.ttf", "segoeuib.ttf"), ("arial.ttf", "arialbd.ttf"),
                      ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")]:
    for folder in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu"), Path("/Library/Fonts")):
        if (folder / regular).exists() and (folder / bold).exists():
            try:
                pdfmetrics.registerFont(TTFont("ReportSans", str(folder / regular)))
                pdfmetrics.registerFont(TTFont("ReportSans-Bold", str(folder / bold)))
                FONT, BOLD = "ReportSans", "ReportSans-Bold"
            except Exception:  # pragma: no cover - font problems fall back to Helvetica
                pass
            break
    if FONT != "Helvetica":
        break


def S(size=9, bold=False, color=INK, align=0, leading=None):
    return ParagraphStyle("s", fontName=BOLD if bold else FONT, fontSize=size, textColor=color, alignment=align,
                          leading=leading or size * 1.3)


def _decorate(data: ReportData, title: str):
    def draw(canvas, doc):
        w, h = A4
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, h - 14 * mm, w, 14 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont(BOLD, 10)
        canvas.drawString(15 * mm, h - 9 * mm, title)
        canvas.setFont(FONT, 8.5)
        canvas.drawRightString(w - 15 * mm, h - 9 * mm,
                               f"{data.exam.name} · {data.exam.class_section} · {data.exam.exam_date:%d %b %Y}")
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 7.5)
        canvas.drawString(15 * mm, 9 * mm, f"Generated {data.generated_at:%d %B %Y %H:%M} · Results {data.status_label}")
        canvas.drawRightString(w - 15 * mm, 9 * mm, f"Page {doc.page}")
        if not data.finalized:
            canvas.setFillColor(colors.HexColor("#d03b3b"))
            canvas.setFillAlpha(0.07)
            canvas.setFont(BOLD, 64)
            canvas.translate(w / 2, h / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "PROVISIONAL")
        canvas.restoreState()
    return draw


def _doc(buf, title):
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=22 * mm,
                             bottomMargin=16 * mm, title=title, author="MCQ Examination Checker")


def _boxes(items, widths=None):
    """A row of stat boxes: [(label, value, sub)]."""
    cells = [[Paragraph(label, S(7.5, color=MUTED)) for label, _, _ in items],
             [Paragraph(value, S(15, True)) for _, value, _ in items],
             [Paragraph(sub, S(7.5, color=MUTED)) for _, _, sub in items]]
    t = Table(cells, colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SOFT), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0, colors.white), ("LINEAFTER", (0, 0), (-2, -1), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


# --- student result cards -----------------------------------------------------------------

def _card(data: ReportData, row: dict) -> list:
    exam, res = data.exam, row["result"]
    story = [Paragraph("Student Result Card", S(16, True, NAVY)), Spacer(1, 2 * mm)]
    ident = [
        [Paragraph("Student", S(7.5, color=MUTED)), Paragraph("Roll number", S(7.5, color=MUTED)),
         Paragraph("Subject", S(7.5, color=MUTED)), Paragraph("Examination date", S(7.5, color=MUTED))],
        [Paragraph(row["name"] or "—", S(11, True)), Paragraph(row["roll"] or "—", S(11, True)),
         Paragraph(exam.subject, S(10)), Paragraph(f"{exam.exam_date:%d %B %Y}", S(10))],
    ]
    t = Table(ident, colWidths=[62 * mm, 38 * mm, 40 * mm, 40 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [Paragraph(f"{exam.name} · {exam.class_section}", S(10, color=MUTED)), Spacer(1, 4 * mm), t,
              Spacer(1, 4 * mm)]

    if not res:
        story.append(Paragraph(f"Result pending: {row['pending_reason']}.", S(11, True, RED)))
        return story

    verdict = "PASS" if res.passed else "FAIL"
    story.append(_boxes([
        ("Marks obtained", f"{money(res.obtained)} / {money(res.max_marks)}", f"{money(exam.marks_per_question)} per correct answer"),
        ("Percentage", f"{money(res.percentage)}%", f"Pass mark {money(exam.passing_percentage)}%"),
        ("Result", verdict, "Final" if data.finalized else "Provisional"),
        ("Class rank", str(res.rank or "—"), f"of {data.stats['evaluated']} student{'s' if data.stats['evaluated'] != 1 else ''}"),
    ], widths=[48 * mm, 44 * mm, 44 * mm, 44 * mm]))
    story.append(Spacer(1, 3 * mm))
    story.append(_boxes([
        ("Correct", str(res.correct), ""), ("Incorrect", str(res.incorrect),
                                             f"−{money(exam.negative_marks)} each" if exam.negative_marking else "No negative marking"),
        ("Unanswered", str(res.unanswered), "Score zero"), ("Multiple marked", str(res.multiple),
                                                           exam.get_multiple_mark_policy_display()),
    ], widths=[45 * mm] * 4))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Question-by-question", S(10, True, NAVY)))
    story.append(Paragraph("Each cell: question · your answer · correct answer (shown when different). "
                           "Shaded red: incorrect · grey: unanswered · amber: multiple marks.", S(7.5, color=MUTED)))
    story.append(Spacer(1, 2 * mm))

    per_row = 10
    cells, styles = [], [("GRID", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                         ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    qs = res.questions
    for start in range(0, len(qs), per_row):
        line = []
        for j, item in enumerate(qs[start:start + per_row]):
            resp = {"unanswered": "–"}.get(item["outcome"], item["response"] or "–")
            extra = "" if item["outcome"] == "correct" else f"  <font color='#067647'>{item['key']}</font>"
            line.append(Paragraph(f"<font color='#64748b' size='7'>{item['q']}</font>  <b>{resp}</b>{extra}",
                                  S(8.5, align=TA_CENTER)))
            bg = {"incorrect": BAD_BG, "unanswered": NEUTRAL_BG, "multiple": WARN_BG}.get(item["outcome"])
            if bg:
                styles.append(("BACKGROUND", (j, len(cells)), (j, len(cells)), bg))
        line += [""] * (per_row - len(line))
        cells.append(line)
    grid = Table(cells, colWidths=[18 * mm] * per_row)
    grid.setStyle(TableStyle(styles))
    story.append(grid)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"Marked against the verified answer key “{exam.answer_key_version.key.title}” "
                           f"(version {exam.answer_key_version.number}). Obtained marks = correct × "
                           f"{money(exam.marks_per_question)}" +
                           (f" − incorrect × {money(exam.negative_marks)}" if exam.negative_marking else "") + ".",
                           S(7.5, color=MUTED)))
    return story


def result_cards(exam, papers=None) -> bytes:
    data = gather(exam)
    rows = data.rows if papers is None else [r for r in data.rows if r["paper"].pk in {p.pk for p in papers}]
    buf = io.BytesIO()
    doc = _doc(buf, f"Result cards — {exam.name}")
    story = []
    for i, row in enumerate(rows):
        if i:
            story.append(PageBreak())
        story += _card(data, row)
    if not story:
        story = [Paragraph("No students to report.", S(11))]
    deco = _decorate(data, "STUDENT RESULT CARD")
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()


# --- examination summary ------------------------------------------------------------------

def _distribution_chart(data: ReportData) -> Drawing:
    d = Drawing(180 * mm, 55 * mm)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 12 * mm, 10 * mm, 160 * mm, 40 * mm
    chart.data = [[b["count"] for b in data.distribution]]
    chart.categoryAxis.categoryNames = [b["label"] + "%" for b in data.distribution]
    chart.categoryAxis.labels.fontName = FONT
    chart.categoryAxis.labels.fontSize = 6.5
    chart.valueAxis.labels.fontName = FONT
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    top = max([b["count"] for b in data.distribution] + [1])
    chart.valueAxis.valueMax = top
    chart.valueAxis.valueStep = max(1, top // 5)
    chart.valueAxis.strokeColor = LINE
    chart.valueAxis.gridStrokeColor = LINE
    chart.valueAxis.visibleGrid = True
    chart.categoryAxis.strokeColor = LINE
    chart.bars[0].fillColor = BLUE
    chart.bars[0].strokeColor = None
    chart.barSpacing = 2
    chart.groupSpacing = 6
    d.add(chart)
    d.add(String(12 * mm, 52 * mm, "Students per percentage band", fontName=FONT, fontSize=7, fillColor=MUTED))
    return d


def _table(header, rows, widths, align_right_from=None):
    t = Table([header] + rows, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
             ("FONTNAME", (0, 0), (-1, 0), BOLD), ("FONTNAME", (0, 1), (-1, -1), FONT),
             ("FONTSIZE", (0, 0), (-1, -1), 8), ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT])]
    if align_right_from is not None:
        style.append(("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def summary_pdf(exam) -> bytes:
    data = gather(exam)
    s = data.stats
    buf = io.BytesIO()
    doc = _doc(buf, f"Examination summary — {exam.name}")
    story = [Paragraph("Examination Summary", S(16, True, NAVY)),
             Paragraph(f"{exam.name} · {exam.subject} · {exam.class_section}", S(10, color=MUTED)), Spacer(1, 4 * mm)]
    det = Table([[Paragraph(k, S(8, True)), Paragraph(v, S(8.5))] for k, v in data.details],
                colWidths=[45 * mm, 135 * mm])
    det.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
    story += [det, Spacer(1, 5 * mm), Paragraph("Class statistics", S(11, True, NAVY)), Spacer(1, 2 * mm)]
    avg = f"{money(s['average'])}" if s["average"] is not None else "—"
    story.append(_boxes([
        ("Students", str(s["students"]), f"{s['uploaded']} paper{'s' if s['uploaded'] != 1 else ''} uploaded"),
        ("Evaluated", str(s["evaluated"]), f"{s['pending']} pending"),
        ("Class average", avg, f"{money(s['average_pct'])}%" if s["average_pct"] is not None else ""),
        ("Highest / lowest", f"{money(s['highest'])} / {money(s['lowest'])}", f"of {money(s['max_marks'])}"),
        ("Passed / failed", f"{s['passed']} / {s['failed']}",
         f"{money(s['pass_rate'])}% pass rate" if s["pass_rate"] is not None else ""),
    ], widths=[36 * mm] * 5))
    story += [Spacer(1, 5 * mm), Paragraph("Marks distribution", S(11, True, NAVY)), _distribution_chart(data)]

    ranked = [r for r in data.rows if r["result"]]
    if ranked:
        rows = [[str(r["rank"]), r["roll"] or "—", Paragraph(r["name"] or "—", S(8)), str(r["result"].correct),
                 str(r["result"].incorrect), str(r["result"].unanswered), money(r["result"].obtained),
                 f"{money(r['result'].percentage)}%", "Pass" if r["result"].passed else "Fail"] for r in ranked]
        story += [Spacer(1, 3 * mm), KeepTogether([Paragraph("Class result", S(11, True, NAVY)), Spacer(1, 2 * mm)]),
                  _table(["Rank", "Roll No.", "Student", "Correct", "Incorrect", "Blank", "Marks", "%", "Status"], rows,
                         [12 * mm, 22 * mm, 50 * mm, 16 * mm, 17 * mm, 14 * mm, 16 * mm, 17 * mm, 16 * mm], 3)]
    pending = [r for r in data.rows if not r["result"]]
    if pending:
        story += [Spacer(1, 3 * mm), Paragraph(f"Pending ({len(pending)}): " + ", ".join(
            f"paper {r['paper'].sequence}{' (' + r['roll'] + ')' if r['roll'] else ''} — {r['pending_reason']}"
            for r in pending), S(8, color=RED))]

    if data.questions and data.stats["evaluated"]:
        story += [PageBreak(), Paragraph("Question-wise analysis", S(14, True, NAVY)), Spacer(1, 2 * mm)]
        hard = data.difficulty["hardest"]
        story.append(Paragraph("Most difficult: " + ", ".join(f"Q{q['q']} ({money(q['correct_pct'])}% correct)"
                                                               for q in hard), S(9)))
        if data.difficulty["high_error"]:
            story.append(Paragraph("High error rate (≥ 50% incorrect): " + ", ".join(
                f"Q{q['q']}" for q in data.difficulty["high_error"]), S(9, color=RED)))
        story.append(Paragraph(f"Answered correctly by ≥ 80%: {len(data.difficulty['mostly_correct'])} questions",
                               S(9)))
        story.append(Spacer(1, 3 * mm))
        rows = [[f"Q{q['q']}", q["key"], str(q["correct"]), str(q["incorrect"]), str(q["unanswered"]),
                 str(q["multiple"]), f"{money(q['correct_pct'])}%",
                 f"{q['top_wrong']} ({q['top_wrong_count']})" if q["top_wrong"] else "—"] for q in data.questions]
        story.append(_table(["Question", "Key", "Correct", "Incorrect", "Blank", "Multiple", "% correct",
                             "Most chosen wrong"], rows,
                            [20 * mm, 14 * mm, 20 * mm, 20 * mm, 18 * mm, 20 * mm, 22 * mm, 30 * mm], 2))
    deco = _decorate(data, "EXAMINATION SUMMARY")
    doc.build(story, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()


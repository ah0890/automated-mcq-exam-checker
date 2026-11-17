"""Excel reports (OpenPyXL)."""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from apps.papers.models import Paper
from apps.processing.models import Correction

from .context import ReportData, gather

NAVY = "13294B"
HEAD_FILL = PatternFill("solid", fgColor=NAVY)
HEAD_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="D9DEE7")
BORDER = Border(bottom=THIN)
PASS_FILL = PatternFill("solid", fgColor="E8F1FC")
FAIL_FILL = PatternFill("solid", fgColor="FDECEC")
TWO_DP = "0.00"


def _title_block(ws, data: ReportData, title: str) -> int:
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=15, color=NAVY)
    ws["A2"] = f"{data.exam.name} · {data.exam.class_section} · {data.exam.exam_date:%d %B %Y}"
    ws["A2"].font = Font(size=11, color="475569")
    ws["A3"] = f"Generated {data.generated_at:%d %B %Y %H:%M} · {data.status_label}"
    ws["A3"].font = Font(size=10, bold=not data.finalized, color="B42318" if not data.finalized else "067647")
    return 5


def _header(ws, row: int, labels: list[str]) -> None:
    for col, label in enumerate(labels, start=1):
        c = ws.cell(row=row, column=col, value=label)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def _widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _results_sheet(ws, data: ReportData) -> None:
    ws.title = "Class results"
    r = _title_block(ws, data, "Class Result Sheet")
    labels = ["Rank", "Roll No.", "Student Name", "Correct", "Incorrect", "Unanswered", "Multiple", "Marks",
              "Max", "Percentage", "Status", "Paper"]
    _header(ws, r, labels)
    first = r + 1
    for i, row in enumerate(data.rows):
        res = row["result"]
        values = [row["rank"], row["roll"] or "", row["name"] or "",
                  res.correct if res else None, res.incorrect if res else None, res.unanswered if res else None,
                  res.multiple if res else None, float(res.obtained) if res else None,
                  float(res.max_marks) if res else None, float(res.percentage) / 100 if res else None,
                  ("Pass" if res.passed else "Fail") if res else f"Pending ({row['pending_reason']})",
                  row["paper"].sequence]
        for col, v in enumerate(values, start=1):
            c = ws.cell(row=first + i, column=col, value=v)
            c.border = BORDER
            if col in (8, 9):
                c.number_format = TWO_DP
            if col == 10:
                c.number_format = "0.00%"
    last = first + max(len(data.rows), 1) - 1
    ws.conditional_formatting.add(f"K{first}:K{last}", CellIsRule(operator="equal", formula=['"Pass"'], fill=PASS_FILL))
    ws.conditional_formatting.add(f"K{first}:K{last}", CellIsRule(operator="equal", formula=['"Fail"'], fill=FAIL_FILL))
    ws.freeze_panes = ws.cell(row=first, column=1)
    ws.auto_filter.ref = f"A{r}:L{last}"
    _widths(ws, [7, 12, 28, 9, 10, 12, 9, 9, 7, 12, 22, 7])

    s = data.stats
    r = last + 3
    ws.cell(row=r, column=1, value="Summary").font = Font(bold=True, size=12, color=NAVY)
    summary = [
        ("Students (active papers)", s["students"]), ("Papers uploaded", s["uploaded"]),
        ("Evaluated", s["evaluated"]), ("Pending verification", s["pending"]),
        ("Class average (marks)", float(s["average"]) if s["average"] is not None else None),
        ("Class average (%)", float(s["average_pct"]) / 100 if s["average_pct"] is not None else None),
        ("Highest", float(s["highest"]) if s["highest"] is not None else None),
        ("Lowest", float(s["lowest"]) if s["lowest"] is not None else None),
        ("Passed", s["passed"]), ("Failed", s["failed"]),
    ]
    for i, (label, value) in enumerate(summary, start=1):
        ws.cell(row=r + i, column=1, value=label)
        c = ws.cell(row=r + i, column=3, value=value)
        if label.endswith("(%)"):
            c.number_format = "0.00%"
        elif isinstance(value, float):
            c.number_format = TWO_DP
    ws.print_title_rows = "5:5"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0


def _details_sheet(wb, data: ReportData) -> None:
    ws = wb.create_sheet("Examination details")
    r = _title_block(ws, data, "Examination Details")
    for i, (k, v) in enumerate(data.details):
        ws.cell(row=r + i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=r + i, column=2, value=v)
    _widths(ws, [26, 60])


def _questions_sheet(wb, data: ReportData, title="Question analysis") -> None:
    ws = wb.create_sheet(title) if wb.active.title != "Sheet" else wb.active
    ws.title = title
    r = _title_block(ws, data, "Question-wise Analysis")
    labels = ["Question", "Correct answer", "Correct", "Incorrect", "Unanswered", "Multiple", "% correct",
              "Most chosen wrong option", "Chosen by"] + [f"Chose {lab}" for lab in data.exam.option_labels] + ["Flag"]
    _header(ws, r, labels)
    for i, q in enumerate(data.questions, start=1):
        flag = "High error rate" if q["high_error"] else ("Answered well" if q["mostly_correct"] else "")
        values = [q["q"], q["key"], q["correct"], q["incorrect"], q["unanswered"], q["multiple"],
                  float(q["correct_pct"]) / 100, q["top_wrong"] or "", q["top_wrong_count"] or None] + \
                 [o["count"] for o in q["options"]] + [flag]
        for col, v in enumerate(values, start=1):
            c = ws.cell(row=r + i, column=col, value=v)
            c.border = BORDER
            if col == 7:
                c.number_format = "0.0%"
    ws.freeze_panes = ws.cell(row=r + 1, column=2)
    _widths(ws, [10, 10, 9, 10, 12, 10, 11, 14, 10] + [9] * len(data.exam.option_labels) + [16])
    if data.questions:
        base = r + len(data.questions) + 2
        ws.cell(row=base, column=1, value="Most difficult questions").font = Font(bold=True, color=NAVY)
        ws.cell(row=base, column=3, value=", ".join(f"Q{q['q']} ({q['correct_pct']}%)"
                                                   for q in data.difficulty["hardest"]))


def _answers_sheet(wb, data: ReportData) -> None:
    ws = wb.create_sheet("Student answers")
    r = _title_block(ws, data, "Student Answers (as verified)")
    n = data.exam.num_questions
    _header(ws, r, ["Rank", "Roll No.", "Name"] + [f"Q{q}" for q in range(1, n + 1)])
    ws.cell(row=r + 1, column=3, value="Correct answer").font = Font(bold=True)
    key = data.exam.answer_key_version.answers if data.exam.answer_key_version_id else {}
    for q in range(1, n + 1):
        c = ws.cell(row=r + 1, column=3 + q, value=key.get(str(q), ""))
        c.font = Font(bold=True, color=NAVY)
    wrong = PatternFill("solid", fgColor="FDECEC")
    for i, row in enumerate([x for x in data.rows if x["result"]], start=2):
        ws.cell(row=r + i, column=1, value=row["rank"])
        ws.cell(row=r + i, column=2, value=row["roll"])
        ws.cell(row=r + i, column=3, value=row["name"])
        for item in row["result"].questions:
            c = ws.cell(row=r + i, column=3 + item["q"],
                        value={"unanswered": "–", "multiple": f"{item['response'] or ''}*"}.get(item["outcome"],
                                                                                           item["response"]))
            if item["outcome"] != "correct":
                c.fill = wrong
            c.alignment = Alignment(horizontal="center")
    ws.cell(row=r + len(data.rows) + 3, column=1,
            value="Red cells: incorrect, unanswered (–) or multiple-marked (*) responses.").font = Font(italic=True)
    ws.freeze_panes = ws.cell(row=r + 2, column=4)
    _widths(ws, [7, 12, 26] + [5] * n)


def class_workbook(exam) -> bytes:
    data = gather(exam)
    wb = Workbook()
    _results_sheet(wb.active, data)
    _questions_sheet(wb, data)
    _answers_sheet(wb, data)
    _details_sheet(wb, data)
    wb.properties.title = f"Class results — {exam.name}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def question_workbook(exam) -> bytes:
    data = gather(exam)
    wb = Workbook()
    _questions_sheet(wb, data)
    _details_sheet(wb, data)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def error_workbook(exam) -> bytes:
    """Processing error report: failed papers, review flags, exclusions and every correction."""
    data = gather(exam)
    wb = Workbook()
    ws = wb.active
    ws.title = "Processing issues"
    r = _title_block(ws, data, "Processing Error Report")
    _header(ws, r, ["Paper", "Source file", "Pages", "Status", "Roll No.", "Issue", "Blocking", "Detail"])
    line = r + 1
    papers = Paper.objects.filter(exam=exam).select_related("source").order_by("sequence")
    for p in papers:
        issues = []
        if p.status == Paper.Status.FAILED:
            issues.append(("Could not be read", True, p.status_note or p.error_code))
        if p.status == Paper.Status.EXCLUDED:
            issues.append(("Excluded", False, p.status_note))
        if p.status in (Paper.Status.UPLOADED, Paper.Status.QUEUED):
            issues.append(("Not processed", True, "Awaiting processing"))
        if p.is_incomplete:
            issues.append(("Incomplete", False, "Fewer pages than expected"))
        for f in p.review_flags:
            if f["code"] in ("markers_not_found", "template_mismatch", "internal_error", "missing_page",
                             "unreadable_pdf", "unreadable_image"):
                continue  # already listed as "Could not be read"
            issues.append((f["code"].replace("_", " ").capitalize(), f.get("blocking", False), f["message"]))
        for label, blocking, detail in issues:
            for col, v in enumerate([p.sequence, p.source.original_name, p.page_label, p.get_status_display(),
                                     p.roll_number or "", label, "Yes" if blocking else "No", detail], start=1):
                ws.cell(row=line, column=col, value=v).border = BORDER
            line += 1
    if line == r + 1:
        ws.cell(row=line, column=1, value="No processing issues.")
    _widths(ws, [7, 30, 16, 20, 11, 22, 9, 70])
    ws.freeze_panes = ws.cell(row=r + 1, column=1)

    ws2 = wb.create_sheet("Manual corrections")
    r2 = _title_block(ws2, data, "Manual Corrections (audit log)")
    _header(ws2, r2, ["When", "Paper", "Roll No.", "Item", "Read as", "Changed to", "By"])
    corrections = Correction.objects.filter(paper__exam=exam).select_related("paper", "user").order_by("created_at")
    for i, c in enumerate(corrections, start=1):
        from django.utils import timezone

        values = [timezone.localtime(c.created_at).strftime("%Y-%m-%d %H:%M"), c.paper.sequence, c.paper.roll_number,
                  "Roll number" if c.field == "roll_number" else c.field.upper(), c.old_value, c.new_value,
                  c.user.get_username() if c.user else ""]
        for col, v in enumerate(values, start=1):
            ws2.cell(row=r2 + i, column=col, value=v).border = BORDER
    if not corrections:
        ws2.cell(row=r2 + 1, column=1, value="No manual corrections.")
    _widths(ws2, [17, 7, 11, 13, 14, 14, 14])
    _details_sheet(wb, data)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

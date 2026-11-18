from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.files import data_path
from apps.core.previews import image_file_response

from apps.papers.services import paper_stats
from apps.results import services as result_services

from . import services
from .forms import AssignKeyForm, ExaminationForm, QuickSetupForm, SheetLayoutForm
from .models import Examination, SheetLayout


# --- examinations ----------------------------------------------------------------------

def exam_list(request):
    query = (request.GET.get("q") or "").strip()
    exams = Examination.objects.select_related("sheet_layout", "answer_key_version__key")
    if query:
        exams = exams.filter(Q(name__icontains=query) | Q(subject__icontains=query) |
                             Q(class_section__icontains=query) | Q(exam_code__icontains=query))
    return render(request, "exams/list.html", {"exams": exams, "query": query,
                                               "has_layouts": SheetLayout.objects.exists()})


def exam_create(request):
    if not SheetLayout.objects.exists():
        messages.info(request, "First add your answer-sheet layout, so the system knows where the bubbles are.")
        return redirect("exams:layout_create")
    form = ExaminationForm(request.POST or None, initial={"sheet_layout": SheetLayout.objects.first()})
    if request.method == "POST" and form.is_valid():
        exam = form.save(commit=False)
        exam.created_by = request.user
        exam.save()
        messages.success(request, f"Examination “{exam.name}” created. Next, attach its answer key.")
        return redirect(exam.get_absolute_url())
    return render(request, "exams/form.html", {"form": form, "layouts": SheetLayout.objects.all()})


def exam_edit(request, uid):
    exam = get_object_or_404(Examination, uid=uid)
    if exam.is_finalized:
        messages.error(request, "Finalized examinations cannot be edited.")
        return redirect(exam.get_absolute_url())
    rules_before = result_services.rules_for(exam).as_dict()  # before the form mutates the instance
    form = ExaminationForm(request.POST or None, instance=exam)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Examination details updated.")
        result_services.update_ranks(exam)
        if exam.results.exists() and result_services.rules_for(exam).as_dict() != rules_before:
            messages.warning(request, "Marking rules changed. Existing results are kept and shown as out of date "
                                      "until you re-evaluate the examination.")
        return redirect(exam.get_absolute_url())
    return render(request, "exams/form.html", {"form": form, "exam": exam, "layouts": SheetLayout.objects.all()})


def exam_detail(request, uid):
    exam = get_object_or_404(
        Examination.objects.select_related("sheet_layout", "answer_key_version__key", "key_assigned_by"), uid=uid)
    key_form = AssignKeyForm(exam=exam)
    papers = list(exam.papers.select_related("source", "result", "result__key_version").order_by("sequence"))
    for p in papers:
        r = getattr(p, "result", None)
        p.result_stale = result_services.stale_reasons(r) if r else []
    return render(request, "exams/detail.html", {
        "exam": exam, "key_form": key_form, "has_compatible_keys": key_form.fields["version"].queryset.exists(),
        "papers": papers, "paper_stats": paper_stats(exam),
        "active_job": exam.processing_jobs.filter(status__in=["queued", "running"]).first(),
        "last_job": exam.processing_jobs.first(),
        "can_reprocess": not exam.is_finalized and bool(exam.answer_key_version_id),
        "results": result_services.summary(exam),
        "last_run": exam.evaluation_runs.first(),
    })


@require_POST
def exam_assign_key(request, uid):
    exam = get_object_or_404(Examination, uid=uid)
    form = AssignKeyForm(request.POST, exam=exam)
    if form.is_valid():
        try:
            services.assign_key(exam, form.cleaned_data["version"], request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            v = form.cleaned_data["version"]
            messages.success(request, f"Answer key “{v.key.title}” (version {v.number}) attached.")
            if exam.results.exists():
                messages.warning(request, "Existing results were calculated with the previous key. They are kept "
                                          "and shown as out of date until you re-evaluate the examination.")
    else:
        messages.error(request, "Select a verified answer key with the same number of questions.")
    return redirect(exam.get_absolute_url())


# --- sheet layouts ------------------------------------------------------------------------

def layout_list(request):
    return render(request, "exams/layouts.html", {"layouts": SheetLayout.objects.all()})


def layout_create(request):
    form = SheetLayoutForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            layout = services.create_layout(
                name=d["name"], code=d["code"], option_labels=d["option_labels"], roll_digits=d["roll_digits"],
                question_order=d["question_order"], uploaded=d["blank_sheet"], user=request.user)
        except ValidationError as exc:
            form.add_error("blank_sheet", exc)
        else:
            messages.success(request, f"Sheet calibrated: {layout.capacity} questions"
                                      f"{f' and a {layout.roll_digits}-digit roll number' if layout.roll_digits else ''}"
                                      " detected. Check the highlighted bubbles below.")
            return redirect(layout.get_absolute_url())
    return render(request, "exams/layout_form.html", {"form": form})


def layout_detail(request, uid):
    layout = get_object_or_404(SheetLayout, uid=uid)
    return render(request, "exams/layout_detail.html", {"layout": layout,
                                                        "exams": layout.examinations.all()})


def layout_preview(request, uid):
    layout = get_object_or_404(SheetLayout, uid=uid)
    rel = layout.blank_path.rsplit("/", 1)[0] + "/preview.png"
    if not data_path(rel).exists():
        raise Http404
    return image_file_response(rel)


# --- one-step setup from the dashboard ----------------------------------------------------

PENDING_EXAM = "quick_setup_exam"


def quick_setup(request):
    from django.utils import timezone

    from .quick_setup import quick_setup as run_quick_setup

    form = QuickSetupForm(request.POST or None, request.FILES or None,
                          initial={"exam_date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            exam, version = run_quick_setup(
                name=d["name"], subject=d["subject"], class_section=d["class_section"], exam_date=d["exam_date"],
                marks_per_question=d["marks_per_question"], negative_marking=d["negative_marking"],
                negative_marks=d["negative_marks"], passing_percentage=d["passing_percentage"],
                multiple_mark_policy=d["multiple_mark_policy"], option_labels=d["option_labels"],
                blank_sheet=d["blank_sheet"], answer_key=d["answer_key"], user=request.user)
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            request.session[PENDING_EXAM] = str(exam.uid)
            layout = exam.sheet_layout
            messages.success(request, f"Answer sheet ready: {layout.capacity} questions"
                                      f"{f', {layout.roll_digits}-digit roll number' if layout.roll_digits else ''}. "
                                      f"Answer key read: {version.extraction.get('found', 0)} of "
                                      f"{version.num_questions} answers. Check the answers below and click "
                                      "“Verify & save key” — then you can upload the students' papers.")
            return redirect("answerkeys:verify", version.uid)
    return render(request, "exams/quick_setup.html", {"form": form})


# --- permanent deletion -------------------------------------------------------------------

CONFIRM_WORD = "DELETE"


def exam_delete(request):
    """Confirm and permanently delete one or more examinations (uids from ?uid=… or the form)."""
    from . import deletion

    source = request.POST if request.method == "POST" else request.GET
    uids = [u for u in source.getlist("uid") if _is_uuid(u)]
    exams = list(Examination.objects.filter(uid__in=uids).select_related("sheet_layout", "answer_key_version__key"))
    if not exams:
        messages.info(request, "Select at least one examination to delete.")
        return redirect("exams:list")

    rows = [{"exam": e, "impact": deletion.impact(e), "blockers": deletion.deletion_blockers(e, request.user)}
            for e in exams]
    blocked = any(r["blockers"] for r in rows)
    error = ""
    if request.method == "POST" and not blocked:
        if (request.POST.get("confirm") or "").strip().upper() != CONFIRM_WORD:
            error = f"Type {CONFIRM_WORD} to confirm."
        else:
            try:
                result = deletion.delete_examinations(exams, request.user,
                                                      remove_unused_setup=bool(request.POST.get("remove_setup")))
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("exams:list")
            extra = []
            if result.keys_removed:
                extra.append(f"{result.keys_removed} answer key{'s' if result.keys_removed != 1 else ''}")
            if result.layouts_removed:
                extra.append(f"{result.layouts_removed} sheet layout{'s' if result.layouts_removed != 1 else ''}")
            n = result.examinations
            messages.success(request, f"Deleted {n} examination{'s' if n != 1 else ''} with {result.papers} "
                                      f"paper{'s' if result.papers != 1 else ''} and {result.results} "
                                      f"result{'s' if result.results != 1 else ''}"
                                      f"{' (also removed ' + ' and '.join(extra) + ')' if extra else ''}.")
            return redirect("exams:list")
    return render(request, "exams/delete.html", {
        "rows": rows, "blocked": blocked, "error": error, "confirm_word": CONFIRM_WORD,
        "totals": {k: sum(r["impact"][k] for r in rows) for k in ("papers", "files", "results")},
        "remove_setup": bool(request.POST.get("remove_setup")),
    })


def _is_uuid(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import RosterImportForm, StudentForm
from .models import Student
from .services import import_roster


def student_list(request):
    query = (request.GET.get("q") or "").strip()
    klass = (request.GET.get("class") or "").strip()
    students = Student.objects.all()
    if klass:
        students = students.filter(class_section=klass)
    if query:
        students = students.filter(Q(roll_number__icontains=query) | Q(name__icontains=query))
    classes = Student.objects.values("class_section").annotate(n=Count("id")).order_by("class_section")
    return render(request, "students/list.html", {"students": students[:1000], "query": query, "klass": klass,
                                                  "classes": classes, "total": Student.objects.count()})


def student_create(request):
    form = StudentForm(request.POST or None, initial={"class_section": request.GET.get("class", "")})
    if request.method == "POST" and form.is_valid():
        student = form.save(commit=False)
        student.created_by = request.user
        student.save()
        messages.success(request, f"Student {student.roll_number} added.")
        return redirect(f"{request.path}?class={student.class_section}" if "add_another" in request.POST
                        else "students:list")
    return render(request, "students/form.html", {"form": form})


def student_edit(request, uid):
    student = get_object_or_404(Student, uid=uid)
    form = StudentForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Student updated.")
        return redirect("students:list")
    return render(request, "students/form.html", {"form": form, "student": student})


def student_import(request):
    form = RosterImportForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            report = import_roster(d["roster"], d["class_section"], d["update_existing"])
        except ValidationError as exc:
            form.add_error("roster", exc)
        else:
            messages.success(request, f"Import complete: {report.created} added, {report.updated} updated, "
                                      f"{report.unchanged} unchanged.")
    return render(request, "students/import.html", {"form": form, "report": report})

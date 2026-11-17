from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.index, name="index"),
    path("exam/<uuid:exam_uid>/class-results.xlsx", views.class_excel, name="class_excel"),
    path("exam/<uuid:exam_uid>/question-analysis.xlsx", views.questions_excel, name="questions_excel"),
    path("exam/<uuid:exam_uid>/processing-report.xlsx", views.errors_excel, name="errors_excel"),
    path("exam/<uuid:exam_uid>/result-cards.pdf", views.cards_pdf, name="cards_pdf"),
    path("exam/<uuid:exam_uid>/summary.pdf", views.summary_pdf, name="summary_pdf"),
    path("exam/<uuid:exam_uid>/backup/", views.backup_exam, name="backup_exam"),
    path("backup/full/", views.backup_full, name="backup_full"),
    path("backup/restore/", views.restore, name="restore"),
    path("backup/download/<str:name>", views.download_backup, name="download_backup"),
]

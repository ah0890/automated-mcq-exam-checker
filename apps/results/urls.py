from django.urls import path

from . import views

app_name = "results"

urlpatterns = [
    path("", views.index, name="index"),
    path("questions/", views.index, {"view": "questions"}, name="questions_index"),
    path("exam/<uuid:exam_uid>/", views.class_results, name="class"),
    path("exam/<uuid:exam_uid>/questions/", views.question_analysis, name="questions"),
    path("exam/<uuid:exam_uid>/evaluate/", views.evaluate, name="evaluate"),
    path("exam/<uuid:exam_uid>/finalize/", views.finalize, name="finalize"),
    path("exam/<uuid:exam_uid>/reopen/", views.reopen, name="reopen"),
]

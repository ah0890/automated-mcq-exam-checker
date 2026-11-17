from django.urls import path

from . import views

app_name = "processing"

urlpatterns = [
    path("", views.center, name="center"),
    path("review/", views.queue, name="queue"),
    path("exam/<uuid:exam_uid>/start/", views.start, name="start"),
    path("exam/<uuid:exam_uid>/reprocess/", views.reprocess, name="reprocess"),
    path("exam/<uuid:exam_uid>/cancel/", views.cancel, name="cancel"),
    path("exam/<uuid:exam_uid>/status/", views.status, name="status"),
    path("paper/<uuid:uid>/review/", views.review_paper, name="review"),
    path("paper/<uuid:uid>/aligned.png", views.processed_image, name="processed_image"),
    path("paper/<uuid:uid>/question/<int:q>.png", views.question_image, name="question_image"),
    path("paper/<uuid:uid>/roll.png", views.roll_image, name="roll_image"),
]

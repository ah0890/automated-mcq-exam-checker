from django.urls import path

from . import views

app_name = "results"

urlpatterns = [
    path("exam/<uuid:exam_uid>/evaluate/", views.evaluate, name="evaluate"),
]

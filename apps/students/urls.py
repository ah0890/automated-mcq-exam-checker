from django.urls import path

from . import views

app_name = "students"

urlpatterns = [
    path("", views.student_list, name="list"),
    path("new/", views.student_create, name="create"),
    path("import/", views.student_import, name="import"),
    path("<uuid:uid>/edit/", views.student_edit, name="edit"),
]

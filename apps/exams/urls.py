from django.urls import path

from . import views

app_name = "exams"

urlpatterns = [
    path("", views.exam_list, name="list"),
    path("new/", views.exam_create, name="create"),
    path("<uuid:uid>/", views.exam_detail, name="detail"),
    path("<uuid:uid>/edit/", views.exam_edit, name="edit"),
    path("<uuid:uid>/answer-key/", views.exam_assign_key, name="assign_key"),
    path("sheet-layouts/", views.layout_list, name="layouts"),
    path("sheet-layouts/new/", views.layout_create, name="layout_create"),
    path("sheet-layouts/<uuid:uid>/", views.layout_detail, name="layout_detail"),
    path("sheet-layouts/<uuid:uid>/preview.png", views.layout_preview, name="layout_preview"),
]

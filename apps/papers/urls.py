from django.urls import path

from . import views

app_name = "papers"

urlpatterns = [
    path("upload/", views.upload_page, name="upload_page"),
    path("upload/<uuid:exam_uid>/", views.upload, name="upload"),
    path("<uuid:uid>/", views.detail, name="detail"),
    path("<uuid:uid>/page/<int:page>.png", views.page_image, name="page_image"),
    path("<uuid:uid>/original/", views.source_download, name="source"),
    path("<uuid:uid>/exclude/", views.set_excluded, name="set_excluded"),
]

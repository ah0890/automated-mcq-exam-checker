from django.urls import path

from . import views

app_name = "answerkeys"

urlpatterns = [
    path("", views.library, name="library"),
    path("upload/", views.upload, name="upload"),
    path("manual/", views.manual, name="manual"),
    path("<uuid:uid>/", views.detail, name="detail"),
    path("version/<uuid:uid>/verify/", views.verify, name="verify"),
    path("version/<uuid:uid>/revise/", views.revise, name="revise"),
    path("version/<uuid:uid>/export/<str:fmt>/", views.export, name="export"),
    path("version/<uuid:uid>/source/", views.source_download, name="source"),
    path("version/<uuid:uid>/source/page/<int:page>.png", views.source_preview, name="source_preview"),
]

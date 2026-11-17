from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "MCQ Examination Checker — Administration"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("examinations/", include("apps.exams.urls")),
    path("answer-keys/", include("apps.answerkeys.urls")),
    path("students/", include("apps.students.urls")),
    path("papers/", include("apps.papers.urls")),
    path("processing/", include("apps.processing.urls")),
    path("results/", include("apps.results.urls")),
    path("", include("apps.core.urls")),
]

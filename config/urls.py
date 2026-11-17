from django.contrib import admin
from django.contrib.auth import views as auth_views  # noqa: F401  (logout)
from django.urls import include, path

from apps.core.auth import ThrottledLoginView

admin.site.site_header = "MCQ Examination Checker — Administration"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", ThrottledLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("examinations/", include("apps.exams.urls")),
    path("answer-keys/", include("apps.answerkeys.urls")),
    path("students/", include("apps.students.urls")),
    path("papers/", include("apps.papers.urls")),
    path("processing/", include("apps.processing.urls")),
    path("results/", include("apps.results.urls")),
    path("reports/", include("apps.reports.urls")),
    path("", include("apps.core.urls")),
]

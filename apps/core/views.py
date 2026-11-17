from django.shortcuts import render

from .system_checks import run_system_checks


def dashboard(request):
    return render(request, "core/dashboard.html", {"checks": run_system_checks()})


def system_settings(request):
    return render(request, "core/settings.html", {"checks": run_system_checks()})

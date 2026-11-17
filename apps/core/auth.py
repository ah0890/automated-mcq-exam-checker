"""Sign-in with throttling of repeated failures."""

import logging

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.core.cache import cache

log = logging.getLogger("apps.core")


def _key(request) -> str:
    ip = request.META.get("REMOTE_ADDR", "unknown")
    user = (request.POST.get("username") or "").strip().lower()[:150]
    return f"login-failures:{ip}:{user}"


class ThrottledLoginView(auth_views.LoginView):
    """After LOGIN_MAX_FAILURES failed attempts for a username from one address,
    further attempts are refused for LOGIN_LOCKOUT_SECONDS — even with the right
    password — so passwords cannot be guessed by trying many times."""

    def post(self, request, *args, **kwargs):
        key = _key(request)
        if cache.get(key, 0) >= settings.LOGIN_MAX_FAILURES:
            log.warning("Sign-in temporarily blocked after repeated failures")
            form = self.get_form()
            minutes = max(1, settings.LOGIN_LOCKOUT_SECONDS // 60)
            return self.render_to_response(self.get_context_data(
                form=form, locked_out=True, lockout_minutes=minutes))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = _key(self.request)
        failures = cache.get(key, 0) + 1
        cache.set(key, failures, settings.LOGIN_LOCKOUT_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_key(self.request))
        return super().form_valid(form)

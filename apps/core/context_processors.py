from django.urls import NoReverseMatch, reverse

from .navigation import NAV_SECTIONS


def _resolve(url_name):
    if not url_name:
        return None
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return None


def navigation(request):
    current = getattr(request, "path", "")
    sections = []
    for title, entries in NAV_SECTIONS:
        items = []
        for label, url_name, icon, milestone in entries:
            url = _resolve(url_name)
            active = bool(url) and (current == url if url == "/" else current.startswith(url))
            items.append({"label": label, "url": url, "icon": icon, "milestone": milestone, "active": active})
        sections.append({"title": title, "items": items})

    user = getattr(request, "user", None)
    initials = ""
    if user is not None and user.is_authenticated:
        full = (user.get_full_name() or user.get_username()).strip()
        initials = "".join(part[0] for part in full.split()[:2]).upper() or full[:2].upper()
    return {"nav_sections": sections, "user_initials": initials}

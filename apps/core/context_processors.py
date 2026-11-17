from django.urls import NoReverseMatch, reverse

from .navigation import NAV_ITEMS


def navigation(request):
    items = []
    current = getattr(request, "path", "")
    for label, url_name, icon, milestone in NAV_ITEMS:
        url = None
        if url_name:
            try:
                url = reverse(url_name)
            except NoReverseMatch:
                url = None
        items.append({
            "label": label,
            "url": url,
            "icon": icon,
            "milestone": milestone,
            "active": bool(url) and (current == url if url == "/" else current.startswith(url)),
        })
    return {"nav_items": items}

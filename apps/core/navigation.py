"""Main navigation. Modules not yet built are shown with the milestone that
delivers them, so the interface never pretends a feature exists."""

NAV_ITEMS = [
    # (label, url name or None, bootstrap icon, milestone delivering it)
    ("Dashboard", "core:dashboard", "speedometer2", None),
    ("Students", None, "people", 2),
    ("Examinations", None, "journal-text", 2),
    ("Answer Key Library", None, "key", 2),
    ("Upload Student Papers", None, "cloud-arrow-up", 3),
    ("Processing Center", None, "cpu", 6),
    ("Manual Verification", None, "check2-square", 4),
    ("Class Results", None, "table", 6),
    ("Question Analysis", None, "bar-chart", 6),
    ("Reports", None, "file-earmark-text", 7),
    ("Settings", "core:settings", "gear", None),
]

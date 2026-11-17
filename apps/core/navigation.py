"""Main navigation, grouped by workflow stage.

Modules not yet built are shown with the milestone that delivers them, so the
interface never pretends a feature exists.
"""

# (section, [(label, url name or None, bootstrap icon, milestone delivering it)])
NAV_SECTIONS = [
    ("Overview", [
        ("Dashboard", "core:dashboard", "grid-1x2", None),
    ]),
    ("Setup", [
        ("Examinations", "exams:list", "journal-text", None),
        ("Answer Key Library", "answerkeys:library", "key", None),
        ("Students", "students:list", "people", None),
        ("Sheet Layouts", "exams:layouts", "grid-3x3-gap", None),
    ]),
    ("Processing", [
        ("Upload Student Papers", "papers:upload_page", "cloud-arrow-up", None),
        ("Processing Center", "processing:center", "cpu", None),
        ("Manual Verification", "processing:queue", "patch-check", None),
    ]),
    ("Results", [
        ("Class Results", "results:index", "trophy", None),
        ("Question Analysis", "results:questions_index", "bar-chart-line", None),
        ("Reports", "reports:index", "file-earmark-text", None),
    ]),
    ("System", [
        ("Settings", "core:settings", "sliders", None),
    ]),
]

# Flat list kept for callers that do not need grouping.
NAV_ITEMS = [item for _, items in NAV_SECTIONS for item in items]

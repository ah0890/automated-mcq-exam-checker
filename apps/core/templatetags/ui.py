from django import forms, template
from django.utils.html import format_html

register = template.Library()


@register.filter
def widget_class(field):
    """Render a bound field's widget with the design-system CSS classes."""
    w = field.field.widget
    if isinstance(w, (forms.CheckboxInput,)):
        css = "form-check-input"
    elif isinstance(w, (forms.Select, forms.SelectMultiple)):
        css = "form-select"
    else:
        css = "form-control"
    if field.errors:
        css += " is-invalid"
    attrs = {"class": css}
    if field.field.required and not isinstance(w, forms.CheckboxInput):
        attrs["required"] = True
    return field.as_widget(attrs=attrs)


@register.filter
def is_checkbox(field):
    return isinstance(field.field.widget, forms.CheckboxInput)


@register.simple_tag
def stage_pill(exam):
    labels = {
        "needs_key": ("pill-warning", "Needs answer key"),
        "ready_for_papers": ("pill-success", "Ready for papers"),
        "finalized": ("pill-neutral", "Finalized"),
    }
    css, text = labels.get(exam.stage, ("pill-neutral", exam.stage))
    return format_html('<span class="pill {}">{}</span>', css, text)

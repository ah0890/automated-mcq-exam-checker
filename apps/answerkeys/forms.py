import re

from django import forms

LABEL_CHOICES = [("ABCD", "A – D (4 options)"), ("ABCDE", "A – E (5 options)"), ("ABC", "A – C (3 options)"),
                 ("AB", "A – B (2 options)")]


class KeyInfoForm(forms.Form):
    title = forms.CharField(max_length=150, label="Key title",
                            widget=forms.TextInput(attrs={"placeholder": "e.g. General Knowledge — Term 1"}))
    subject = forms.CharField(max_length=100, required=False)
    option_labels = forms.ChoiceField(choices=LABEL_CHOICES, initial="ABCD", label="Answer options")


class KeyUploadForm(KeyInfoForm):
    num_questions = forms.IntegerField(
        min_value=1, max_value=500, required=False, label="Number of questions",
        help_text="Leave blank to detect it from the document.")
    document = forms.FileField(label="Answer-key document")


class KeyManualForm(KeyInfoForm):
    num_questions = forms.IntegerField(min_value=1, max_value=500, label="Number of questions")


def parse_answer_grid(post, num_questions: int, labels: str) -> tuple[dict[str, str], list[str]]:
    """Read q_1..q_N fields from a verification form."""
    answers, errors = {}, []
    for q in range(1, num_questions + 1):
        value = (post.get(f"q_{q}") or "").strip().upper()
        if not value:
            continue
        if len(value) != 1 or value not in labels:
            errors.append(f"Question {q}: “{value}” is not one of {', '.join(labels)}.")
            continue
        answers[str(q)] = value
    return answers, errors


def clean_note(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:255]

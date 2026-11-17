from django import forms

from apps.answerkeys.forms import LABEL_CHOICES

from .models import Examination, SheetLayout


class SheetLayoutForm(forms.Form):
    name = forms.CharField(max_length=120, label="Layout name",
                           widget=forms.TextInput(attrs={"placeholder": "e.g. 50-question A4 sheet"}))
    code = forms.CharField(max_length=80, required=False, label="Printed template ID",
                           help_text="Optional — the identifier printed on the sheet.")
    option_labels = forms.ChoiceField(choices=LABEL_CHOICES, initial="ABCD", label="Options per question")
    roll_digits = forms.IntegerField(min_value=1, max_value=12, required=False, label="Roll-number digits",
                                     help_text="Number of digit columns in the roll-number bubble grid. "
                                               "Leave blank if the sheet has none.")
    question_order = forms.ChoiceField(choices=SheetLayout.QuestionOrder.choices,
                                       initial=SheetLayout.QuestionOrder.COLUMN, label="Question numbering")
    blank_sheet = forms.FileField(label="Blank answer sheet", help_text="PDF, PNG or JPG of an unmarked sheet.")


class ExaminationForm(forms.ModelForm):
    class Meta:
        model = Examination
        fields = ["name", "subject", "class_section", "exam_date", "exam_code", "sheet_layout", "num_questions",
                  "marks_per_question", "negative_marking", "negative_marks", "multiple_mark_policy",
                  "passing_percentage"]
        widgets = {
            "exam_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "name": forms.TextInput(attrs={"placeholder": "e.g. General Knowledge Test"}),
            "subject": forms.TextInput(attrs={"placeholder": "e.g. General Science"}),
            "class_section": forms.TextInput(attrs={"placeholder": "e.g. Grade 10 – B"}),
            "marks_per_question": forms.NumberInput(attrs={"step": "0.25", "min": "0.25"}),
            "negative_marks": forms.NumberInput(attrs={"step": "0.25", "min": "0"}),
            "passing_percentage": forms.NumberInput(attrs={"step": "1", "min": "0", "max": "100"}),
        }
        labels = {"sheet_layout": "Answer sheet layout", "passing_percentage": "Passing percentage (%)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sheet_layout"].queryset = SheetLayout.objects.all()
        self.fields["sheet_layout"].empty_label = "Select a calibrated sheet layout"
        if self.instance.pk and self.instance.answer_key_version_id:
            # Structure is fixed once a key is attached.
            for name in ("sheet_layout", "num_questions"):
                self.fields[name].disabled = True
                self.fields[name].help_text = "Locked because an answer key is attached."


class AssignKeyForm(forms.Form):
    version = forms.ModelChoiceField(queryset=None, label="Verified answer key", empty_label="Select a key")

    def __init__(self, *args, exam: Examination, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.answerkeys.models import AnswerKeyVersion

        self.fields["version"].queryset = (
            AnswerKeyVersion.objects.filter(status=AnswerKeyVersion.Status.VERIFIED,
                                            num_questions=exam.num_questions)
            .select_related("key").order_by("key__title", "-number"))
        self.fields["version"].label_from_instance = (
            lambda v: f"{v.key.title} · version {v.number} · {v.num_questions} questions")

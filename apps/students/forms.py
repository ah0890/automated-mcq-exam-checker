from django import forms

from .models import Student


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["roll_number", "name", "class_section", "is_active"]
        widgets = {
            "roll_number": forms.TextInput(attrs={"placeholder": "e.g. 26001"}),
            "class_section": forms.TextInput(attrs={"placeholder": "e.g. Grade 10 – B"}),
        }


class RosterImportForm(forms.Form):
    class_section = forms.CharField(max_length=60, label="Class / section",
                                    help_text="Used for rows that do not have a class column.")
    roster = forms.FileField(label="Class list", help_text="Excel (.xlsx) or CSV with roll number and name columns.")
    update_existing = forms.BooleanField(required=False, initial=True,
                                         label="Update names of students who already exist")

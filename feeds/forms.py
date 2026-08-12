from django import forms

from .models import Feed, UserPreference
from .services import OPML_MAX_BYTES


class FeedForm(forms.ModelForm):
    title = forms.CharField(max_length=255, required=False)

    class Meta:
        model = Feed
        fields = [  # noqa: RUF012 - declarative Django form metadata.
            "feed_url",
            "title",
            "site_url",
            "description",
            "category",
            "is_active",
        ]
        widgets = {  # noqa: RUF012 - declarative Django form metadata.
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class OPMLImportForm(forms.Form):
    opml_file = forms.FileField(label="OPML file")

    def clean_opml_file(self):
        uploaded = self.cleaned_data["opml_file"]
        if uploaded.size > OPML_MAX_BYTES:
            raise forms.ValidationError("Upload a valid OPML file (maximum 1 MiB).")
        return uploaded


class ThemeForm(forms.ModelForm):
    focus_mode = forms.BooleanField(
        label="Focus mode",
        required=False,
        help_text="Use a centered, distraction-light reading layout while keeping your selected theme.",
    )

    class Meta:
        model = UserPreference
        fields = ("theme", "compact", "focus_mode")

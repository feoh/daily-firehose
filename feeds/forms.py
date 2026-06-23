from django import forms

from .models import Feed, UserPreference


class FeedForm(forms.ModelForm):
    class Meta:
        model = Feed
        fields = [
            "feed_url",
            "title",
            "site_url",
            "description",
            "category",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class OPMLImportForm(forms.Form):
    opml_file = forms.FileField(label="OPML file")


class ThemeForm(forms.ModelForm):
    focus_mode = forms.BooleanField(
        label="Focus mode",
        required=False,
        help_text="Use a centered, distraction-light reading layout while keeping your selected theme.",
    )

    class Meta:
        model = UserPreference
        fields = ("theme", "compact", "focus_mode")

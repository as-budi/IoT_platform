from django import forms
from django.contrib.auth import get_user_model

from accounts.forms import BootstrapFormMixin
from devices.views import visible_devices

from .models import ActivityEvent


class ActivityFilterForm(BootstrapFormMixin, forms.Form):
    """Filter halaman log (GET). Semua isian opsional."""

    q = forms.CharField(label="Search", required=False,
                        widget=forms.TextInput(attrs={"placeholder": "pump, offline, budi, …", "type": "search"}))
    category = forms.ChoiceField(label="Category", required=False,
                                 choices=[("", "All categories")] + list(ActivityEvent.Category.choices))
    level = forms.ChoiceField(label="Level", required=False, choices=[
        ("", "All levels"), ("WARNING", "Warnings and errors"), ("ERROR", "Errors only"),
    ])
    device = forms.ModelChoiceField(label="Device", required=False, queryset=None, empty_label="All devices")
    owner = forms.ModelChoiceField(label="User", required=False, queryset=None, empty_label="All users")
    start = forms.DateField(label="From", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="To", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = visible_devices(user).order_by("name")
        if user.is_admin:
            self.fields["owner"].queryset = get_user_model().objects.order_by("username")
        else:
            del self.fields["owner"]    # pengguna biasa hanya punya log-nya sendiri

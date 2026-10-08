import json
from datetime import timedelta

from django import forms

from accounts.forms import BootstrapFormMixin

from .models import Device, Location, Variable, key_validator


class DeviceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Device
        fields = (
            "name",
            "device_type",
            "location",
            "description",
            "is_active",
            "owner",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user=None, **kwargs):
        """`user` = pengguna yang sedang login (dikirim oleh view)."""
        super().__init__(*args, **kwargs)
        # Hanya Admin yang boleh menentukan pemilik device. Untuk pengguna
        # biasa, kolom ini dibuang dan pemilik diisi otomatis oleh view.
        if user is None or not user.is_admin:
            del self.fields["owner"]

        # Field `location` otomatis menjadi ModelChoiceField (dropdown) karena
        # di model berupa ForeignKey. Dropdown hanya berisi lokasi yang ada di
        # database, dan Django menolak nilai lain yang dikirim — jadi pengguna
        # memang tidak bisa membuat lokasi baru dari form ini.
        self.fields["location"].empty_label = "— No location —"


class VariableForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Variable
        fields = ("key", "label", "unit", "kind", "value_type", "min_value", "max_value")
        help_texts = {
            "kind": "Actuators can be controlled from the dashboard.",
            "min_value": "Lowest value a user may request (number actuators only).",
            "max_value": "Highest value a user may request (number actuators only).",
        }

    def __init__(self, *args, device=None, **kwargs):
        """`device` = device pemilik variabel ini (dikirim oleh view)."""
        super().__init__(*args, **kwargs)
        self.device = device

    def clean_key(self):
        """Tolak key yang sudah dipakai variabel lain di device yang sama.

        Model punya UniqueConstraint (device, key), tetapi field `device`
        tidak ada di form, sehingga Django tidak bisa memeriksanya sendiri.
        Tanpa pemeriksaan ini, key kembar baru ketahuan sebagai error database.
        """
        key = self.cleaned_data["key"]
        duplicates = Variable.objects.filter(device=self.device, key=key)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("This device already has a variable with this key.")
        return key

    def clean(self):
        """Validasi yang melibatkan beberapa field sekaligus."""
        cleaned = super().clean()
        low, high = cleaned.get("min_value"), cleaned.get("max_value")
        if low is not None and high is not None and low > high:
            self.add_error("max_value", "Maximum must be greater than or equal to minimum.")
        # Batas min/max tidak bermakna untuk nilai ON/OFF.
        if cleaned.get("value_type") == Variable.ValueType.BOOLEAN:
            cleaned["min_value"] = cleaned["max_value"] = None
        return cleaned


class SetDesiredForm(forms.Form):
    """Form untuk mengubah desired state sebuah aktuator.

    Bukan ModelForm, karena yang disimpan bukan sekadar field: perubahan
    dilakukan lewat Variable.set_desired() yang juga menulis log.
    """

    value = forms.FloatField()

    def __init__(self, *args, variable, **kwargs):
        super().__init__(*args, **kwargs)
        self.variable = variable

    def clean_value(self):
        value = self.cleaned_data["value"]
        v = self.variable
        if v.is_boolean:
            if value not in (0, 1):
                raise forms.ValidationError("Value must be ON (1) or OFF (0).")
            return value
        # Batas dicek di server, walaupun <input min max> di browser sudah
        # membatasi: isian browser mudah diakali, server tidak.
        if v.min_value is not None and value < v.min_value:
            raise forms.ValidationError(f"Value must be at least {v.min_value:g}.")
        if v.max_value is not None and value > v.max_value:
            raise forms.ValidationError(f"Value must be at most {v.max_value:g}.")
        return value


class CommandForm(BootstrapFormMixin, forms.Form):
    """Form untuk mengirim command (aksi sekali jalan) ke device."""

    TTL_CHOICES = [
        (60, "1 minute"),
        (300, "5 minutes"),
        (3600, "1 hour"),
        (86400, "1 day"),
    ]

    method = forms.CharField(
        max_length=50,
        validators=[key_validator],
        widget=forms.TextInput(attrs={"placeholder": "e.g. reboot"}),
    )
    params = forms.CharField(
        required=False,
        initial="{}",
        widget=forms.Textarea(attrs={"rows": 1, "placeholder": '{"seconds": 5}'}),
        help_text="Optional JSON object.",
    )
    # TypedChoiceField: pilihan dikirim browser sebagai teks, lalu diubah ke int.
    ttl = forms.TypedChoiceField(
        label="Expires after", choices=TTL_CHOICES, coerce=int, initial=300
    )

    def clean_params(self):
        raw = (self.cleaned_data.get("params") or "").strip()
        if not raw:
            return {}
        try:
            params = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Not valid JSON: {exc.msg}.")
        if not isinstance(params, dict):
            raise forms.ValidationError('Parameters must be a JSON object, e.g. {"seconds": 5}.')
        return params

    @property
    def ttl_delta(self):
        return timedelta(seconds=self.cleaned_data["ttl"])


class LocationForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Location
        fields = ("name", "latitude", "longitude", "description")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            # step="any": browser menerima angka desimal sebanyak apa pun.
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }

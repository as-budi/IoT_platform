import json
from datetime import timedelta

from django import forms

from accounts.forms import BootstrapFormMixin

from .charts import CUSTOM, DEFAULT_PERIOD, PERIODS
from .models import Device, Location, Variable, key_validator


class DeviceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Device
        fields = (
            "name",
            "device_type",
            "location",
            "board",
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
        self.is_admin = user is not None and user.is_admin
        if not self.is_admin:
            del self.fields["owner"]
            # Device baru milik pengguna ini (diisi di sini, sebelum validasi,
            # supaya clean() di bawah tahu siapa pemiliknya).
            if self.instance.owner_id is None and user is not None:
                self.instance.owner = user

        # Field `location` otomatis menjadi ModelChoiceField (dropdown) karena
        # di model berupa ForeignKey. Dropdown hanya berisi lokasi dari
        # queryset di bawah, dan Django menolak nilai lain yang dikirim —
        # termasuk id lokasi milik orang lain yang diketik manual.
        location = self.fields["location"]
        location.empty_label = "— No location —"
        if self.is_admin:
            # Admin melihat semua lokasi; nama pemilik ditampilkan karena nama
            # lokasi bisa sama di antara pengguna.
            location.queryset = Location.objects.select_related("owner")
            location.label_from_instance = lambda loc: f"{loc.name} ({loc.owner.username})"
        else:
            location.queryset = Location.objects.filter(owner=user) if user else Location.objects.none()

    def clean(self):
        """Lokasi device harus milik pemilik device (penting saat Admin mengganti pemilik)."""
        cleaned = super().clean()
        owner = cleaned.get("owner") if self.is_admin else self.instance.owner if self.instance.owner_id else None
        location = cleaned.get("location")
        if owner and location and location.owner_id != owner.pk:
            self.add_error("location", f"This location belongs to {location.owner.username}, not to {owner.username}.")
        return cleaned


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
    """Tambah/ubah lokasi.

    Pengguna biasa: lokasi selalu miliknya (kolom owner tidak ditampilkan).
    Admin: boleh memilih pemilik, misalnya membuatkan lokasi untuk pengguna lain.
    """

    class Meta:
        model = Location
        fields = ("name", "latitude", "longitude", "description", "owner")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            # step="any": browser menerima angka desimal sebanyak apa pun.
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_admin = user is not None and user.is_admin
        if self.instance.owner_id is None and user is not None:
            self.instance.owner = user
            if self.is_admin:
                self.fields["owner"].initial = user.pk
        if not self.is_admin:
            del self.fields["owner"]

    def clean(self):
        """Nama unik per pemilik, huruf besar/kecil dianggap sama.

        Constraint database (owner, name) tidak diperiksa otomatis oleh form
        bila kolom owner tidak ada di form, jadi diperiksa di sini. Hasilnya
        pesan yang jelas, bukan IntegrityError (halaman error 500).
        """
        cleaned = super().clean()
        owner = cleaned.get("owner") if self.is_admin else self.instance.owner
        name = cleaned.get("name")
        if owner and name:
            same = Location.objects.filter(owner=owner, name__iexact=name).exclude(pk=self.instance.pk)
            if same.exists():
                self.add_error("name", f"{owner.username} already has a location called {name}.")
        # Admin memindahkan lokasi ke pemilik lain: device di lokasi itu harus milik pemilik baru juga.
        if self.is_admin and owner and self.instance.pk and self.instance.owner_id != owner.pk:
            if self.instance.devices.exclude(owner=owner).exists():
                self.add_error("owner", "Devices of another user use this location. Move them first.")
        return cleaned


# Format nilai <input type="datetime-local">, mis. "2026-10-07T14:30".
DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


class ChartPeriodForm(forms.Form):
    """Pilihan periode chart, dibaca dari parameter URL (form GET).

    Contoh alamat:
        ?period=5m
        ?period=custom&start=2026-10-07T08:00&end=2026-10-07T12:00
    Form GET dipakai (bukan POST) karena hanya MEMBACA data; alamatnya bisa
    disimpan sebagai bookmark atau dibagikan.
    """

    period = forms.ChoiceField(
        choices=[(key, label) for key, (label, _) in PERIODS.items()], required=False
    )
    # Waktu dibaca dalam zona waktu aktif (TIME_ZONE = Asia/Jakarta), lalu
    # Django mengubahnya menjadi datetime "aware" yang dibandingkan dalam UTC.
    start = forms.DateTimeField(required=False, input_formats=[DATETIME_LOCAL])
    end = forms.DateTimeField(required=False, input_formats=[DATETIME_LOCAL])

    def clean(self):
        data = super().clean()
        start, end = data.get("start"), data.get("end")
        if data.get("period") == CUSTOM and start and end and start >= end:
            raise forms.ValidationError("Start must be earlier than end.")
        return data

    def get_window(self, now):
        """Kembalikan (period, start, end) yang dipakai chart.

        Input yang salah tidak membuat halaman error: chart kembali ke
        periode bawaan, dan pesan kesalahannya ditampilkan di form.
        """
        data = self.cleaned_data if self.is_valid() else {}
        period = data.get("period") or DEFAULT_PERIOD
        if period == CUSTOM:
            # End kosong = "sampai sekarang", sehingga data baru tetap masuk chart.
            end = data.get("end") or now
            start = data.get("start") or end - timedelta(days=1)
            if start >= end:
                start = end - timedelta(days=1)
            return period, start, end
        return period, now - PERIODS[period][1], now

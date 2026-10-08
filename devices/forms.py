from django import forms

from accounts.forms import BootstrapFormMixin

from .models import Device, Variable


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


class VariableForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Variable
        fields = ("key", "label", "unit")

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

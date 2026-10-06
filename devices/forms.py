from django import forms

from accounts.forms import BootstrapFormMixin

from .models import Device


class DeviceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Device
        fields = (
            "name",
            "device_type",
            "variable",
            "unit",
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

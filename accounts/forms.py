"""
Form = class yang mengurus input pengguna: menghasilkan elemen HTML,
memvalidasi data yang dikirim, dan (untuk ModelForm) menyimpannya ke database.
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


class BootstrapFormMixin:
    """Menambahkan class CSS Bootstrap ke semua field form.

    "Mixin" adalah class kecil yang dicampurkan ke class lain untuk memberi
    satu kemampuan tambahan. Mixin ini juga dipakai oleh devices/forms.py.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "form-check-input"
            elif isinstance(widget, forms.Select):
                widget.attrs["class"] = "form-select"
            else:
                widget.attrs["class"] = "form-control"


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    """Form login bawaan Django, hanya diberi tampilan Bootstrap."""


class UserCreateForm(BootstrapFormMixin, UserCreationForm):
    """Form tambah pengguna.

    UserCreationForm bawaan sudah menyediakan dua kolom password, memeriksa
    keduanya sama, dan menyimpan password dalam bentuk hash (bukan teks asli).
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "role")


class UserUpdateForm(BootstrapFormMixin, forms.ModelForm):
    """Form ubah pengguna (tanpa password)."""

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "role", "is_active")

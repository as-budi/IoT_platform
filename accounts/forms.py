"""
Form = class yang mengurus input pengguna: menghasilkan elemen HTML,
memvalidasi data yang dikirim, dan (untuk ModelForm) menyimpannya ke database.
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm

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


class RegisterForm(BootstrapFormMixin, UserCreationForm):
    """Form pendaftaran mandiri untuk pengunjung yang belum punya akun.

    Perhatikan `role` TIDAK ada di daftar field. Django hanya memproses field
    yang terdaftar di form, jadi walaupun seseorang menyisipkan role=ADMIN ke
    dalam data yang dikirim, nilainya diabaikan dan akun baru selalu mendapat
    role default dari model, yaitu User.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")


class UserUpdateForm(BootstrapFormMixin, forms.ModelForm):
    """Form ubah pengguna (tanpa password)."""

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "role", "is_active")


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    """Form "My profile": data diri yang boleh diubah pemilik akun sendiri.

    Sama seperti RegisterForm, yang menentukan keamanan adalah DAFTAR FIELD:
    username, role, is_active, is_staff, dan is_superuser tidak tercantum,
    sehingga tidak bisa diubah lewat form ini walaupun disisipkan ke data POST.
    """

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")


class PasswordChangeBootstrapForm(BootstrapFormMixin, PasswordChangeForm):
    """Form ubah password bawaan Django (password lama + dua kali password baru).

    PasswordChangeForm sudah memeriksa password lama, menjalankan aturan
    AUTH_PASSWORD_VALIDATORS di settings.py untuk password baru, dan menyimpan
    hasilnya sebagai hash. Kita hanya menambahkan tampilan Bootstrap.
    """

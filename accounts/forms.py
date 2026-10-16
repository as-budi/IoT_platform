"""
Form = class yang mengurus input pengguna: menghasilkan elemen HTML,
memvalidasi data yang dikirim, dan (untuk ModelForm) menyimpannya ke database.
"""
from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.core.exceptions import ValidationError

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


def email_taken(email, exclude=None):
    """True bila email sudah dipakai akun lain (huruf besar/kecil dianggap sama).

    Email harus unik karena dipakai untuk reset password: satu alamat = satu akun.
    Pemeriksaan ini di form, bukan constraint database, karena data lama boleh
    berisi email kosong atau ganda (MySQL tidak mendukung unique bersyarat).
    """
    users = User.objects.filter(email__iexact=email)
    if exclude is not None:
        users = users.exclude(pk=exclude.pk)
    return users.exists()


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    """Form login bawaan Django, ditambah pesan khusus untuk akun yang belum diaktivasi.

    Django menolak akun is_active=False dengan pesan umum "username atau password
    salah". Untuk akun hasil Sign up yang belum mengklik tautan aktivasi, pesan
    itu membingungkan. Pesan khusus hanya diberikan bila password-nya BENAR,
    supaya orang lain tidak bisa mengetahui status akun hanya dari username.
    """

    error_messages = {
        **AuthenticationForm.error_messages,
        "activation_pending": (
            "This account is not activated yet. Open the link we emailed you, "
            "or request a new one below."
        ),
    }

    def clean(self):
        try:
            return super().clean()
        except ValidationError:
            username = self.cleaned_data.get("username")
            password = self.cleaned_data.get("password")
            user = User.objects.filter(username=username).first() if username else None
            if user and user.activation_pending and not user.is_active and user.check_password(password):
                self.activation_pending = True
                raise ValidationError(self.error_messages["activation_pending"], code="activation_pending")
            raise


class UserCreateForm(BootstrapFormMixin, UserCreationForm):
    """Form tambah pengguna.

    UserCreationForm bawaan sudah menyediakan dua kolom password, memeriksa
    keduanya sama, dan menyimpan password dalam bentuk hash (bukan teks asli).
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "role")

    def clean_email(self):
        email = self.cleaned_data["email"]
        if email and email_taken(email):
            raise ValidationError("Another account already uses this email.")
        return email


class RegisterForm(BootstrapFormMixin, UserCreationForm):
    """Form pendaftaran mandiri untuk pengunjung yang belum punya akun.

    Perhatikan `role` TIDAK ada di daftar field. Django hanya memproses field
    yang terdaftar di form, jadi walaupun seseorang menyisipkan role=ADMIN ke
    dalam data yang dikirim, nilainya diabaikan dan akun baru selalu mendapat
    role default dari model, yaitu User.

    Sejak tahap 11 email WAJIB dan unik: tautan aktivasi dikirim ke sana.
    """

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["email"].help_text = "We will send an activation link to this address."

    def clean_email(self):
        email = self.cleaned_data["email"]
        if email_taken(email):
            raise ValidationError("An account with this email already exists.")
        return email


class UserUpdateForm(BootstrapFormMixin, forms.ModelForm):
    """Form ubah pengguna (tanpa password)."""

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "role", "is_active")

    def clean_email(self):
        email = self.cleaned_data["email"]
        if email and email_taken(email, exclude=self.instance):
            raise ValidationError("Another account already uses this email.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        # Admin mengaktifkan akun yang belum mengklik tautan aktivasi: anggap selesai.
        if user.is_active:
            user.activation_pending = False
        if commit:
            user.save()
        return user


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    """Form "My profile": data diri yang boleh diubah pemilik akun sendiri.

    Sama seperti RegisterForm, yang menentukan keamanan adalah DAFTAR FIELD:
    username, role, is_active, is_staff, dan is_superuser tidak tercantum,
    sehingga tidak bisa diubah lewat form ini walaupun disisipkan ke data POST.

    Email (tahap 11): alamat baru TIDAK langsung disimpan ke kolom `email`.
    save() menaruhnya di `pending_email`; ProfileView lalu mengirim tautan
    konfirmasi ke alamat baru itu. Salah ketik alamat tidak membuat pemilik
    akun kehilangan akses ke fitur lupa password.
    """

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Catat email lama SEKARANG: setelah validasi, ModelForm menyalin isian
        # form ke self.instance sehingga instance.email sudah berisi alamat baru.
        self.current_email = self.instance.email
        self.fields["email"].required = True

    def clean_email(self):
        email = self.cleaned_data["email"]
        if email.lower() != (self.current_email or "").lower() and email_taken(email, exclude=self.instance):
            raise ValidationError("An account with this email already exists.")
        return email

    @property
    def email_changed(self):
        return self.cleaned_data["email"].lower() != (self.current_email or "").lower()

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.email_changed:
            user.pending_email = self.cleaned_data["email"]
            user.email = self.current_email   # tetap alamat lama sampai dikonfirmasi
        elif self.cleaned_data["email"] != self.current_email:
            user.email = self.cleaned_data["email"]   # hanya beda huruf besar/kecil
        if commit:
            user.save()
        return user


class PasswordChangeBootstrapForm(BootstrapFormMixin, PasswordChangeForm):
    """Form ubah password bawaan Django (password lama + dua kali password baru).

    PasswordChangeForm sudah memeriksa password lama, menjalankan aturan
    AUTH_PASSWORD_VALIDATORS di settings.py untuk password baru, dan menyimpan
    hasilnya sebagai hash. Kita hanya menambahkan tampilan Bootstrap.
    """


class PasswordResetBootstrapForm(BootstrapFormMixin, PasswordResetForm):
    """Form "Forgot password": satu isian email.

    PasswordResetForm bawaan mengirim email HANYA ke akun yang aktif, punya
    password, dan emailnya cocok. Bila tidak ada yang cocok, form tetap
    "berhasil" tanpa mengirim apa pun, sehingga halaman ini tidak bisa dipakai
    untuk menebak email siapa yang terdaftar.
    """


class SetPasswordBootstrapForm(BootstrapFormMixin, SetPasswordForm):
    """Form password baru (setelah membuka tautan reset password)."""


class ResendActivationForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(label="Email")

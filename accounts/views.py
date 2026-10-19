"""
View = kode yang menerima request dan mengembalikan response (halaman HTML).

Di sini kita memakai "class-based generic views": Django sudah menyediakan
view siap pakai untuk pola umum (daftar, tambah, ubah, hapus). Kita tinggal
menyebutkan model, form, dan template-nya.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views import View
from django.views.generic import CreateView, DeleteView, FormView, ListView, TemplateView, UpdateView

from .emails import send_activation_email, send_email_change_email
from .forms import (
    PasswordChangeBootstrapForm,
    PasswordResetBootstrapForm,
    ProfileForm,
    RegisterForm,
    ResendActivationForm,
    SetPasswordBootstrapForm,
    UserCreateForm,
    UserUpdateForm,
    email_taken,
)
from .mixins import AdminRequiredMixin
from .models import User
from .tokens import activation_token, email_change_token

# Lama berlaku tautan di email (aktivasi, ganti email, reset password), dalam jam.
LINK_HOURS = settings.PASSWORD_RESET_TIMEOUT // 3600


def user_from_uidb64(uidb64):
    """Pengguna dari bagian <uidb64> tautan email, atau None bila tidak valid."""
    try:
        pk = int(force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError):
        return None
    return User.objects.filter(pk=pk).first()


class LoginView(auth_views.LoginView):
    """Login bawaan Django, ditambah informasi apakah tautan "Sign up" ditampilkan."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["allow_signup"] = settings.ALLOW_SIGNUP
        return context


class RegisterView(CreateView):
    """Pendaftaran mandiri. Sengaja TANPA mixin login: halaman ini publik.

    Sejak tahap 11 akun baru belum bisa dipakai: is_active=False dan
    activation_pending=True sampai pemiliknya mengklik tautan di email.
    """

    form_class = RegisterForm
    template_name = "accounts/register.html"
    success_url = reverse_lazy("accounts:register_done")

    def dispatch(self, request, *args, **kwargs):
        # dispatch() adalah method pertama yang dijalankan sebuah view.
        # Pengguna yang sudah login tidak perlu mendaftar lagi.
        if request.user.is_authenticated:
            return redirect("dashboard:index")
        # Di server publik pendaftaran bisa ditutup (DJANGO_ALLOW_SIGNUP=False):
        # akun hanya dibuat oleh Admin lewat halaman Users.
        if not settings.ALLOW_SIGNUP:
            raise Http404("Sign-up is closed.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # form_valid() dipanggil bila semua isian lolos validasi.
        # commit=False: buat objeknya dulu tanpa menyimpan, supaya status
        # aktivasi bisa diatur sebelum baris pertama masuk database.
        user = form.save(commit=False)
        user.is_active = False
        user.activation_pending = True
        user.save()
        self.object = user
        if not send_activation_email(self.request, user):
            messages.error(
                self.request,
                "Your account was created, but we could not send the activation email. "
                "Try again in a few minutes with \"Resend activation link\".",
            )
        return redirect(self.success_url)


class RegisterDoneView(TemplateView):
    """Halaman "Check your email" setelah Sign up atau kirim ulang tautan."""

    template_name = "accounts/register_done.html"
    extra_context = {"hours": LINK_HOURS}


class ActivateView(View):
    """GET/POST /accounts/activate/<uidb64>/<token>/.

    GET hanya menampilkan tombol "Activate account"; aktivasi terjadi saat POST.
    Alasannya: sebagian layanan email (pemindai tautan anti-phishing) membuka
    setiap tautan di email secara otomatis. Bila GET langsung mengaktifkan dan
    me-login-kan, pemindai itulah yang mendapat sesi login, bukan pemiliknya.
    """

    template_name = "accounts/activate.html"

    def get_user(self, uidb64, token):
        user = user_from_uidb64(uidb64)
        if user and user.activation_pending and not user.is_active and activation_token.check_token(user, token):
            return user
        return None

    def get(self, request, uidb64, token):
        user = self.get_user(uidb64, token)
        return render(request, self.template_name, {"target_user": user, "valid": user is not None})

    def post(self, request, uidb64, token):
        user = self.get_user(uidb64, token)
        if user is None:
            return render(request, self.template_name, {"valid": False})
        user.is_active = True
        user.activation_pending = False
        user.save(update_fields=["is_active", "activation_pending"])
        login(request, user)
        messages.success(request, f"Welcome, {user.username}! Your account is active.")
        return redirect("dashboard:index")


class ResendActivationView(FormView):
    """Kirim ulang tautan aktivasi ke email yang diketik.

    Jawabannya SELALU sama ("bila ada akun yang menunggu aktivasi, emailnya
    sudah dikirim"), baik email terdaftar maupun tidak, supaya halaman ini
    tidak bisa dipakai untuk menebak email siapa yang terdaftar.
    """

    form_class = ResendActivationForm
    template_name = "accounts/activation_resend.html"
    success_url = reverse_lazy("accounts:register_done")

    def form_valid(self, form):
        pending = User.objects.filter(
            email__iexact=form.cleaned_data["email"], activation_pending=True, is_active=False
        )
        for user in pending:
            send_activation_email(self.request, user)
        return super().form_valid(form)


class UserListView(AdminRequiredMixin, ListView):
    template_name = "accounts/user_list.html"
    # Nama variabel di template. Sengaja bukan "user"/"user_list" agar tidak
    # tertukar dengan `user` (pengguna yang sedang login).
    context_object_name = "users"

    def get_queryset(self):
        # annotate() menambahkan kolom hitungan: jumlah device tiap pengguna.
        return User.objects.annotate(device_count=Count("devices")).order_by("username")


class UserCreateView(AdminRequiredMixin, SuccessMessageMixin, CreateView):
    model = User
    form_class = UserCreateForm
    template_name = "accounts/user_form.html"
    # reverse_lazy mengubah NAMA url menjadi alamat ("/accounts/users/").
    success_url = reverse_lazy("accounts:user_list")
    success_message = "User %(username)s was created."


class UserUpdateView(AdminRequiredMixin, SuccessMessageMixin, UpdateView):
    model = User
    form_class = UserUpdateForm
    template_name = "accounts/user_form.html"
    context_object_name = "target_user"
    success_url = reverse_lazy("accounts:user_list")
    success_message = "User %(username)s was updated."


class UserDeleteView(AdminRequiredMixin, SuccessMessageMixin, DeleteView):
    template_name = "accounts/user_confirm_delete.html"
    context_object_name = "target_user"
    success_url = reverse_lazy("accounts:user_list")
    success_message = "User was deleted."

    def get_queryset(self):
        # Admin tidak boleh menghapus akunnya sendiri: akun itu dikeluarkan
        # dari queryset, sehingga mencoba menghapusnya menghasilkan 404.
        return User.objects.exclude(pk=self.request.user.pk)


# --- Profil milik sendiri (semua pengguna yang login) ----------------------------


class ProfileView(LoginRequiredMixin, UpdateView):
    """GET/POST /accounts/profile/: ubah nama dan email akun SENDIRI.

    Tidak ada <pk> di URL. Objek yang diubah selalu diambil dari
    request.user (lihat get_object), jadi pengguna mustahil membuka atau
    mengubah profil orang lain hanya dengan mengganti angka di address bar.
    """

    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.email_changed:
            messages.success(self.request, "Your profile was updated.")
        elif send_email_change_email(self.request, self.object):
            messages.info(
                self.request,
                f"We sent a confirmation link to {self.object.pending_email}. "
                "Your email changes after you open it.",
            )
        else:
            messages.error(self.request, "We could not send the confirmation email. Please try again later.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["device_count"] = self.request.user.devices.count()
        # Kartu Telegram (tahap 17, app notifications).
        from notifications import telegram
        from notifications.models import TelegramLink

        context["telegram_enabled"] = telegram.enabled()
        context["telegram_link"] = TelegramLink.objects.filter(user=self.request.user).first()
        return context


class PasswordChangeView(SuccessMessageMixin, auth_views.PasswordChangeView):
    """GET/POST /accounts/profile/password/: ubah password sendiri.

    View bawaan Django ini sudah:
      - mewajibkan login (sudah memakai login_required di dalamnya),
      - meminta password LAMA, supaya orang yang kebetulan memakai komputer
        yang masih login tidak bisa mengambil alih akun,
      - memanggil update_session_auth_hash(): sesi di browser ini tetap login,
        sedangkan sesi di browser/perangkat LAIN otomatis keluar.
    """

    form_class = PasswordChangeBootstrapForm
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")
    success_message = "Your password was changed. Other browsers where you were logged in have been logged out."


class EmailChangeCancelView(LoginRequiredMixin, View):
    """POST /accounts/profile/email/cancel/: batalkan perubahan email yang belum dikonfirmasi."""

    def post(self, request):
        request.user.pending_email = ""
        request.user.save(update_fields=["pending_email"])
        messages.info(request, "The email change was canceled.")
        return redirect("accounts:profile")


class EmailChangeConfirmView(View):
    """GET/POST /accounts/profile/email/<uidb64>/<token>/: konfirmasi email baru.

    Tidak mewajibkan login: tautan bisa dibuka di HP yang belum login.
    Yang membuktikan kepemilikan adalah token, yang hanya dikirim ke alamat baru.
    Seperti aktivasi, perubahan baru terjadi saat POST (tombol "Confirm").
    """

    template_name = "accounts/email_confirm.html"

    def get_user(self, uidb64, token):
        user = user_from_uidb64(uidb64)
        if user and user.pending_email and email_change_token.check_token(user, token):
            return user
        return None

    def get(self, request, uidb64, token):
        user = self.get_user(uidb64, token)
        return render(request, self.template_name, {"target_user": user, "valid": user is not None})

    def post(self, request, uidb64, token):
        user = self.get_user(uidb64, token)
        if user is None:
            return render(request, self.template_name, {"valid": False})
        # Periksa ulang: alamat itu bisa saja dipakai akun lain sejak tautan dikirim.
        if email_taken(user.pending_email, exclude=user):
            user.pending_email = ""
            user.save(update_fields=["pending_email"])
            return render(request, self.template_name, {"valid": False, "taken": True})
        user.email, user.pending_email = user.pending_email, ""
        user.save(update_fields=["email", "pending_email"])
        messages.success(request, f"Your email is now {user.email}.")
        if request.user == user:
            return redirect("accounts:profile")
        return redirect("accounts:login")


# --- Lupa password: view bawaan Django, hanya diberi template dan form sendiri ----
# Alurnya empat halaman:
#   1. PasswordResetView         isi email -> Django mengirim tautan (bila emailnya cocok)
#   2. PasswordResetDoneView     "periksa email Anda"
#   3. PasswordResetConfirmView  tautan dari email -> isi password baru
#   4. PasswordResetCompleteView "password sudah diganti, silakan login"
# Token reset dibuat dari hash password dan waktu login terakhir, sehingga tautan
# otomatis hangus begitu password diganti (hanya bisa dipakai sekali).


class PasswordResetView(auth_views.PasswordResetView):
    form_class = PasswordResetBootstrapForm
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/email/password_reset_body.txt"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")
    extra_email_context = {"hours": LINK_HOURS}


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"
    extra_context = {"hours": LINK_HOURS}


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    form_class = SetPasswordBootstrapForm
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"

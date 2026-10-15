"""
View = kode yang menerima request dan mengembalikan response (halaman HTML).

Di sini kita memakai "class-based generic views": Django sudah menyediakan
view siap pakai untuk pola umum (daftar, tambah, ubah, hapus). Kita tinggal
menyebutkan model, form, dan template-nya.
"""
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.http import Http404
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .forms import RegisterForm, UserCreateForm, UserUpdateForm
from .mixins import AdminRequiredMixin
from .models import User


class LoginView(auth_views.LoginView):
    """Login bawaan Django, ditambah informasi apakah tautan "Sign up" ditampilkan."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["allow_signup"] = settings.ALLOW_SIGNUP
        return context


class RegisterView(SuccessMessageMixin, CreateView):
    """Pendaftaran mandiri. Sengaja TANPA mixin login: halaman ini publik."""

    form_class = RegisterForm
    template_name = "accounts/register.html"
    success_url = reverse_lazy("dashboard:index")
    success_message = "Welcome, %(username)s! Your account is ready."

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
        # super() menyimpan akun baru ke database (hasilnya di self.object),
        # lalu login() langsung membuatkan sesi, sehingga pengguna tidak
        # perlu mengetik ulang username dan password di halaman login.
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


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

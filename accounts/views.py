"""
View = kode yang menerima request dan mengembalikan response (halaman HTML).

Di sini kita memakai "class-based generic views": Django sudah menyediakan
view siap pakai untuk pola umum (daftar, tambah, ubah, hapus). Kita tinggal
menyebutkan model, form, dan template-nya.
"""
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .forms import UserCreateForm, UserUpdateForm
from .mixins import AdminRequiredMixin
from .models import User


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
    success_message = "Pengguna %(username)s berhasil dibuat."


class UserUpdateView(AdminRequiredMixin, SuccessMessageMixin, UpdateView):
    model = User
    form_class = UserUpdateForm
    template_name = "accounts/user_form.html"
    context_object_name = "target_user"
    success_url = reverse_lazy("accounts:user_list")
    success_message = "Pengguna %(username)s berhasil diperbarui."


class UserDeleteView(AdminRequiredMixin, SuccessMessageMixin, DeleteView):
    template_name = "accounts/user_confirm_delete.html"
    context_object_name = "target_user"
    success_url = reverse_lazy("accounts:user_list")
    success_message = "Pengguna berhasil dihapus."

    def get_queryset(self):
        # Admin tidak boleh menghapus akunnya sendiri: akun itu dikeluarkan
        # dari queryset, sehingga mencoba menghapusnya menghasilkan 404.
        return User.objects.exclude(pk=self.request.user.pk)

"""URL milik app accounts. Semua diawali /accounts/ (lihat config/urls.py)."""
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

# app_name memberi "namespace": url di bawah dipanggil sebagai
# "accounts:login", "accounts:user_list", dst.
app_name = "accounts"

urlpatterns = [
    # Login & logout memakai view bawaan Django.
    path(
        "login/",
        auth_views.LoginView.as_view(authentication_form=LoginForm),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Pendaftaran mandiri (terbuka untuk siapa saja yang belum login).
    path("register/", views.RegisterView.as_view(), name="register"),
    # Manajemen pengguna (khusus Admin).
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/add/", views.UserCreateView.as_view(), name="user_add"),
    # <int:pk> menangkap angka di URL dan mengirimnya ke view sebagai `pk`
    # (primary key pengguna yang dimaksud).
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_edit"),
    path("users/<int:pk>/delete/", views.UserDeleteView.as_view(), name="user_delete"),
]

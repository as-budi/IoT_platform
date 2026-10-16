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
        views.LoginView.as_view(authentication_form=LoginForm),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Pendaftaran mandiri (terbuka untuk siapa saja yang belum login).
    path("register/", views.RegisterView.as_view(), name="register"),
    # Verifikasi email saat Sign up (tahap 11). <uidb64> = id pengguna dalam
    # base64, <token> = tanda tangan HMAC (lihat accounts/tokens.py).
    path("register/done/", views.RegisterDoneView.as_view(), name="register_done"),
    path("activate/resend/", views.ResendActivationView.as_view(), name="activation_resend"),
    path("activate/<uidb64>/<token>/", views.ActivateView.as_view(), name="activate"),
    # Lupa password (view bawaan Django dengan template sendiri).
    path("password/reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password/reset/done/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password/reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("password/reset/complete/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    # Profil dan password milik pengguna yang sedang login (tanpa <pk>).
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("profile/password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("profile/email/cancel/", views.EmailChangeCancelView.as_view(), name="email_cancel"),
    path("profile/email/<uidb64>/<token>/", views.EmailChangeConfirmView.as_view(), name="email_confirm"),
    # Manajemen pengguna (khusus Admin).
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/add/", views.UserCreateView.as_view(), name="user_add"),
    # <int:pk> menangkap angka di URL dan mengirimnya ke view sebagai `pk`
    # (primary key pengguna yang dimaksud).
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_edit"),
    path("users/<int:pk>/delete/", views.UserDeleteView.as_view(), name="user_delete"),
]

"""
URL utama project.

Alur sebuah request: browser meminta alamat -> Django mencocokkannya dengan
daftar `urlpatterns` di bawah, dari atas ke bawah -> view yang cocok dijalankan.

`include()` mendelegasikan sisa alamat ke urls.py milik sebuah app, sehingga
setiap app mengurus URL-nya sendiri.
"""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    # Situs admin bawaan Django (alat internal untuk mengelola data mentah).
    path("admin/", admin.site.urls),
    # /accounts/...  -> login, logout, manajemen pengguna
    path("accounts/", include("accounts.urls")),
    # /dashboard/... -> ringkasan semua variabel, dikelompokkan per lokasi & device
    path("dashboard/", include("dashboard.urls")),
    # /devices/...   -> daftar & detail device
    path("devices/", include("devices.urls")),
    # /rules/...    -> rule engine (aturan otomatis) dan alarm
    path("rules/", include("rules.urls")),
    # /firmware/...  -> paket firmware dan pembaruan OTA (tahap 9)
    path("firmware/", include("ota.urls")),
    # /api/...       -> API untuk device sungguhan (mis. ESP32), lihat api/urls.py
    path("api/", include("api.urls")),
    # Menyambungkan akun dengan Telegram (tahap 17).
    path("notifications/", include("notifications.urls")),
    # Log aktivitas (tahap 18).
    path("activity/", include("activity.urls")),
    # Halaman depan langsung diarahkan ke dashboard.
    path("", RedirectView.as_view(pattern_name="dashboard:index"), name="home"),
]

"""URL dashboard. Semua diawali /dashboard/ (lihat config/urls.py)."""
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="index"),
    path("customize/", views.DashboardSettingsView.as_view(), name="settings"),
    # JSON kecil untuk pemeriksaan cadangan pembaruan langsung.
    path("updates/", views.dashboard_updates, name="updates"),
]

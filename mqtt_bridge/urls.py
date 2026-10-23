"""Akun MQTT sandbox (tahap 23). Diawali /accounts/sandbox/ (lihat config/urls.py)."""
from django.urls import path

from . import views

app_name = "sandbox"

urlpatterns = [
    path("enable/", views.enable, name="enable"),
    path("reset/", views.reset, name="reset"),
    path("disable/", views.disable, name="disable"),
]

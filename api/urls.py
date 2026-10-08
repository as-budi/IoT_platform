"""URL API untuk device. Semua diawali /api/ (lihat config/urls.py)."""
from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    # Tanpa garis miring di akhir, sama seperti ThingsBoard. Versi dengan
    # garis miring juga diterima, karena Django tidak bisa mengalihkan
    # (redirect) request POST secara otomatis.
    path("v1/<str:token>/telemetry", views.telemetry, name="telemetry"),
    path("v1/<str:token>/telemetry/", views.telemetry),
]

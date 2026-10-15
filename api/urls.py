"""URL API untuk device. Semua diawali /api/ (lihat config/urls.py)."""
from django.urls import path

from ota import views as ota_views

from . import views

app_name = "api"

urlpatterns = [
    # Tanpa garis miring di akhir, sama seperti ThingsBoard. Versi dengan
    # garis miring juga diterima, karena Django tidak bisa mengalihkan
    # (redirect) request POST secara otomatis.
    path("v1/<str:token>/telemetry", views.telemetry, name="telemetry"),
    path("v1/<str:token>/telemetry/", views.telemetry),
    # Tahap 9 (OTA): device melaporkan versi firmware dan progres pembaruan.
    path("v1/<str:token>/firmware", views.firmware, name="firmware"),
    path("v1/<str:token>/firmware/", views.firmware),
    # Unduhan file firmware. Token di sini BUKAN access token device, melainkan
    # token unduhan sekali pakai milik satu pembaruan (lihat ota/services.py).
    path("v1/ota/<str:token>.bin", ota_views.download, name="ota_download"),
]

"""URL API. Semua diawali /api/ (lihat config/urls.py).

    /api/v1/...  untuk DEVICE (access token device di URL)
    /api/v2/...  untuk APLIKASI LUAR yang login atas nama pengguna (token OAuth, tahap 22)
"""
from django.urls import path

from ota import views as ota_views

from . import v2, views

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
    # Tahap 22: aplikasi luar (mis. MQTT client web), lihat api/v2.py.
    path("v2/me", v2.me, name="v2_me"),
    path("v2/me/devices", v2.my_devices, name="v2_devices"),
]

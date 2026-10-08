"""Konfigurasi ASGI — pintu masuk server web asinkron (Daphne).

Dengan `daphne` di INSTALLED_APPS, perintah `python manage.py runserver`
memakai file ini (bukan wsgi.py), sehingga satu server bisa melayani:

    http://...   -> view Django biasa (halaman, API)
    ws://...     -> consumer WebSocket (realtime/consumers.py)

ProtocolTypeRouter memilah koneksi berdasarkan jenis protokolnya.
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Harus dipanggil SEBELUM mengimpor kode yang memakai model (consumer),
# karena baris ini yang menyiapkan Django (django.setup()).
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from realtime.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # AllowedHostsOriginValidator: tolak WebSocket yang dibuka dari situs lain.
    #   Proteksi CSRF Django TIDAK berlaku untuk WebSocket; tanpa pemeriksaan
    #   Origin ini, situs jahat bisa membuka koneksi atas nama pengguna yang
    #   sedang login (cookie sesi ikut terkirim otomatis).
    # AuthMiddlewareStack: baca cookie sesi -> scope["user"].
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    ),
})

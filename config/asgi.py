"""Konfigurasi ASGI — pintu masuk server web asinkron (mis. uvicorn/daphne).

Belum dipakai di tahap ini, tetapi nanti berguna untuk WebSocket
(update nilai sensor secara real-time di dashboard).
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()

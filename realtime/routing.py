"""URL WebSocket. Mirip urls.py, tetapi untuk protokol ws:// (lihat config/asgi.py)."""
from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/devices/<int:pk>/", consumers.DeviceConsumer.as_asgi()),
    path("ws/dashboard/", consumers.DashboardConsumer.as_asgi()),
]

# Alamat WebSocket dashboard (semua device milik pengguna).
DASHBOARD_WS_PATH = "/ws/dashboard/"


def device_ws_path(device_id):
    """Alamat WebSocket sebuah device. Django tidak bisa reverse() URL WebSocket."""
    return f"/ws/devices/{device_id}/"

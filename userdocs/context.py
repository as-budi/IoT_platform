"""
Fakta server yang ditulis di halaman dokumentasi (tahap 25): alamat web, broker MQTT,
server CoAP, batas-batas, dsb. Semuanya dari settings, sehingga dokumentasi selalu
cocok dengan server tempat ia dibaca.

docs_context() tidak bergantung pada pengguna, sehingga hasilnya sama untuk
tampilan dan untuk indeks pencarian. Bagian yang bergantung pada pengguna (device
yang dipilih dan access token-nya) ditambahkan oleh device_context().
"""
from django.conf import settings

from devices import connect
from devices.connect import host_reach
from devices.models import Board
from devices.views import coap_host, selected_device, visible_devices
from mqtt_bridge import topics as mqtt_topics
from sharing import access

TOKEN_PLACEHOLDER = "<token>"


def docs_context(request, token=TOKEN_PLACEHOLDER):
    coap_server, coap_reach = coap_host(request)
    values = connect.server_values(request, coap_server)
    base_url = values["http"]["SERVER"]
    prefix = settings.MQTT_TOPIC_PREFIX
    return {
        "token": token,
        "http": {
            "base_url": base_url,
            "https": base_url.startswith("https://"),
            "reach": host_reach(connect.url_host(base_url)),
            "telemetry": f"{base_url}/api/v1/{token}/telemetry",
            "firmware": f"{base_url}/api/v1/{token}/firmware",
        },
        "mqtt": {
            "host": settings.MQTT_PUBLIC_HOST,
            "port": settings.MQTT_PUBLIC_PORT,
            "tls": settings.MQTT_PUBLIC_TLS,
            "login": settings.MQTT_DEVICE_LOGIN,
            "prefix": prefix,
            "ws_url": settings.MQTT_PUBLIC_WS_URL,
            "sandbox": settings.MQTT_SANDBOX_ENABLED,
            "telemetry": mqtt_topics.telemetry(prefix, token),
            "desired": mqtt_topics.desired(prefix, token),
            "commands": mqtt_topics.command(prefix, token, "+"),
            "command_response": mqtt_topics.command_response(prefix, token, "<id>"),
            "errors": mqtt_topics.errors(prefix, token),
            "firmware": mqtt_topics.firmware(prefix, token),
            "status": mqtt_topics.status(prefix, token),
        },
        "coap": {
            "host": coap_server,
            "port": settings.COAP_PORT,
            "reach": coap_reach,
            "base": f"coap://{coap_server}:{settings.COAP_PORT}/api/v1/{token}",
        },
        "limits": {
            "offline_seconds": settings.DEVICE_OFFLINE_SECONDS,
            "firmware_max_mb": round(settings.OTA_MAX_FIRMWARE_SIZE / (1024 * 1024), 1),
            "email_link_hours": settings.PASSWORD_RESET_TIMEOUT // 3600,
            "schedule_grace_minutes": max(1, settings.RULE_SCHEDULE_GRACE_SECONDS // 60),
        },
        "time_zone": settings.TIME_ZONE,
        "telegram_enabled": bool(getattr(settings, "TELEGRAM_BOT_TOKEN", "")),
        "boards": Board.choices,
        "protocols": connect.PROTOCOLS,
        "root_ca": {
            "name": connect.ROOT_CA_NAME,
            "sha256": connect.ROOT_CA_SHA256,
            "expires": connect.ROOT_CA_EXPIRES,
        },
    }


def device_context(request):
    """Device yang dipilih (?device=<id>) di bab "Connect an ESP32": hanya untuk pengguna
    yang login dan berperan Editor ke atas, sama seperti bagian "Connect a device"."""
    if not request.user.is_authenticated:
        return {"devices": [], "device": None}
    devices = visible_devices(request.user, access.EDITOR)
    device = selected_device(request, devices)
    context = {"devices": devices, "device": device}
    if device is not None:
        context.update(docs_context(request, token=device.access_token))
    return context

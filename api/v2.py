"""
API v2: untuk APLIKASI LUAR yang login atas nama pengguna (tahap 22).

    GET /api/v2/me            scope "profile"       profil pengguna pemilik token
    GET /api/v2/me/devices    scope "devices:read"  device milik + yang dibagikan kepadanya
    GET /api/v2/me/sandbox    scope "mqtt:sandbox"  akun MQTT sandbox (tahap 23)

Bedanya dengan API v1 (untuk device):

    v1  device          access token DEVICE di URL     mengirim data sebagai device
    v2  aplikasi luar   access token OAuth di header   membaca data atas nama PENGGUNA

Contoh (token didapat aplikasi klien dari /o/token/):

    curl -H "Authorization: Bearer <token>" https://iot.domain-anda/api/v2/me/devices

Daftar device SENGAJA hanya berisi device milik pengguna dan yang dibagikan kepadanya,
juga untuk Admin: Admin memang bisa melihat semua device di halaman web, tetapi aplikasi
luar tidak perlu ikut memegang token SEMUA device platform.

Access token device hanya disertakan untuk peran Editor ke atas, sama dengan halaman
device (kartu "Connect a device"). Viewer/Operator mendapat "access_token": null.
"""
from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from devices.models import Device
from devices.views import mqtt_info
from mqtt_bridge import topics as mqtt_topics
from oauth.auth import oauth_required
from sharing import access


def _iso(value):
    return value.isoformat() if value else None


def _topics(token):
    prefix = settings.MQTT_TOPIC_PREFIX
    return {
        "telemetry": mqtt_topics.telemetry(prefix, token),
        "desired": mqtt_topics.desired(prefix, token),
        "commands": mqtt_topics.command(prefix, token, "+"),
        "command_response": mqtt_topics.command_response(prefix, token, "{id}"),
        "errors": mqtt_topics.errors(prefix, token),
        "firmware": mqtt_topics.firmware(prefix, token),
        "status": mqtt_topics.status(prefix, token),
    }


def broker_info():
    """Alamat broker untuk device dan browser (sama dengan tab MQTT halaman device)."""
    info = mqtt_info(Device(access_token=""))
    return {
        "mqtt": {"host": info["host"], "port": info["port"], "tls": info["tls"]},
        "websocket": info["ws_url"] or None,
        # True = username = password = access token device.
        "device_login": info["login"],
        "topic_prefix": settings.MQTT_TOPIC_PREFIX,
    }


@require_GET
@oauth_required("profile")
def me(request):
    user = request.oauth_user
    data = {
        "id": user.pk,
        "username": user.username,
        "name": user.get_full_name() or user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": "admin" if user.is_admin else "user",
    }
    if request.oauth_token.allow_scopes(["email"]):
        data["email"] = user.email
    return JsonResponse(data)


@require_GET
@oauth_required("devices:read")
def my_devices(request):
    user = request.oauth_user
    devices = list(
        Device.objects.filter(Q(owner=user) | Q(pk__in=access.shares_of(user).values("device_id")))
        .select_related("owner", "location")
        .prefetch_related("variables")
        .order_by("name", "pk")
    )
    # Peran per device: device sendiri selalu Owner (juga untuk Admin); sisanya dari share.
    # Satu device bisa dibagikan lewat beberapa jalan (langsung + grup): ambil yang tertinggi.
    shared_roles = {}
    shares = access.shares_of(user).filter(device_id__in=[d.pk for d in devices])
    for device_id, role in shares.values_list("device_id", "role"):
        if access.RANK[role] > access.RANK.get(shared_roles.get(device_id), 0):
            shared_roles[device_id] = role

    items = []
    for device in devices:
        role = access.OWNER if device.owner_id == user.pk else shared_roles.get(device.pk, access.VIEWER)
        can_connect = access.has(role, access.EDITOR)
        items.append({
            "id": device.pk,
            "name": device.name,
            "description": device.description,
            "location": device.location.name if device.location_id else None,
            "owner": device.owner.username,
            "role": access.LABEL[role].lower(),
            "is_active": device.is_active,
            "is_online": device.is_online,
            "last_seen_at": _iso(device.last_seen_at),
            "last_protocol": device.last_protocol or None,
            "board": device.board or None,
            "firmware_version": device.firmware_version or None,
            "url": request.build_absolute_uri(reverse("devices:detail", args=[device.pk])),
            # Token dan topik (topik memuat token) hanya untuk Editor ke atas.
            "access_token": device.access_token if can_connect else None,
            "topics": _topics(device.access_token) if can_connect else None,
            "variables": [
                {
                    "key": variable.key,
                    "name": variable.display_name,
                    "kind": variable.kind.lower(),
                    "value_type": variable.value_type.lower(),
                    "unit": variable.unit,
                    "min": variable.min_value,
                    "max": variable.max_value,
                }
                for variable in sorted(device.variables.all(), key=lambda v: v.key)
            ],
        })
    return JsonResponse({"broker": broker_info(), "devices": items})


@require_GET
@oauth_required("mqtt:sandbox")
def my_sandbox(request):
    """Akun MQTT sandbox pengguna (tahap 23). Akun dibuat/di-reset di halaman My profile platform."""
    from mqtt_bridge.models import SHARED_ROOT, SandboxAccount

    data = {"available": settings.MQTT_SANDBOX_ENABLED, "enabled": False, "broker": broker_info(),
            "manage_url": request.build_absolute_uri(reverse("accounts:profile")) + "#mqtt-sandbox"}
    account = SandboxAccount.objects.filter(user=request.oauth_user).first() if data["available"] else None
    if account:
        data.update(enabled=True, username=account.username, password=account.password,
                    topic_root=account.topic_root, shared_root=SHARED_ROOT,
                    password_changed_at=_iso(account.password_changed_at))
    return JsonResponse(data)

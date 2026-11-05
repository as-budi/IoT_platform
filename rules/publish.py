"""
Publish JSON aturan ke device (percobaan sebelum tahap 27).

Tombol "Publish to devices" di kartu JSON halaman aturan mengirim JSON aturan, tanpa
dekomposisi, ke SEMUA device yang dipakai aturan:

    <prefix>/<token>/rules/<rule id>        platform -> device, QoS 1, retained
    <prefix>/<token>/rules/<rule id>/ack    device -> platform, tanda terima

Pesannya SAMA untuk semua device: JSON aturan dengan satu kunci tambahan "publish" di depan.

  Full     JSON di halaman aturan (tanpa exported_at):
             {"publish": {"id": 3, "rev": "9f3a1c07"}, "format": "iot-platform.rule", "version": 1,
              "model": "ECA", "name": ..., "devices": [{"name": "Pump", "mac": "AA:.."}], "eca": {...}, ...}
  Compact  sama, tetapi tanpa "format", "description", dan nilai kosong (null, "", [], {}),
           dan nama device di kondisi/aksi diganti indeksnya di "devices" ("device": 0).
           Kira-kira 25% lebih kecil daripada Full.

Device mengenali dirinya lewat MAC-nya di "devices"; topik ack = topik pesan + "/ack".

Tanda terima dari device:  {"publish": 3, "ok": true}
                    atau   {"publish": 3, "ok": false, "error": "payload too large"}

Retained: device yang sedang mati menerima JSON terakhir begitu tersambung dan subscribe.
"Withdraw" (dan menghapus aturan) mengirim pesan kosong retained, yang menghapus JSON itu
dari broker. Pengiriman dilakukan mqtt_bridge (pola outbox, mqtt_bridge/outbox.py).
"""
import json

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from mqtt_bridge import topics

from .edge.ir import digest
from .models import Rule, RuleDelivery, RulePublication
from .portable import export_rule, rule_devices

Status = RuleDelivery.Status
WAITING = (Status.QUEUED, Status.SENT)
# PubSubClient (ESP32) menolak pesan yang lebih besar dari buffernya tanpa pesan apa pun;
# template firmware MQTT memakai 8192 byte.
DEVICE_BUFFER_BYTES = 8192


def rule_body(rule):
    """JSON aturan tanpa waktu ekspor (supaya rev hanya berubah bila isinya berubah)."""
    data = export_rule(rule)
    data.pop("exported_at", None)
    return data


def rule_rev(body):
    return digest(body)


# Kunci yang berisi nama device (diganti indeks di pesan Compact).
DEVICE_KEYS = ("device", "target_device")
EMPTY = (None, "", [], {})


def _prune(value):
    """Buang nilai kosong (null, "", [], {}) dari object, berulang ke dalam."""
    if isinstance(value, dict):
        pruned = {k: _prune(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if v not in EMPTY}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def _index_devices(value, index):
    """Nama device di "device"/"target_device" -> posisinya di daftar "devices"."""
    if isinstance(value, dict):
        return {k: (index[v] if k in DEVICE_KEYS and isinstance(v, str) and v in index else _index_devices(v, index))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_index_devices(v, index) for v in value]
    return value


def compact_body(body):
    """JSON aturan -> bentuk Compact (lihat docstring modul)."""
    data = {k: v for k, v in body.items() if k not in ("format", "description", "devices")}
    names = [d["name"] for d in body.get("devices", [])]
    # Indeks hanya bila nama device unik (device milik orang lain bisa bernama sama).
    if len(set(names)) == len(names):
        data = _index_devices(data, {name: i for i, name in enumerate(names)})
    data = _prune(data)
    devices = [{k: v for k, v in d.items() if v not in EMPTY} for d in body.get("devices", [])]
    # "devices" tetap di tempat semula (setelah header) supaya mudah dibaca.
    out = {}
    for key in body:
        if key == "devices":
            out["devices"] = devices
        elif key in data:
            out[key] = data[key]
    return out


def rule_message(body, rev, number, compact=False):
    """Pesan untuk device: {"publish": {"id", "rev"}} + JSON aturan (Full atau Compact)."""
    return {"publish": {"id": number, "rev": rev}, **(compact_body(body) if compact else body)}


def message_text(message):
    """Teks JSON pesan, persis seperti yang dikirim mqtt_bridge (ringkas, tanpa spasi)."""
    from mqtt_bridge.outbox import encode

    return encode(message)


def message_size(message):
    """Ukuran pesan yang dikirim, dalam byte."""
    return len(message_text(message).encode("utf-8"))


def skip_reason(rule, device):
    from sharing import access

    if not device.is_active:
        return "The device is inactive."
    if not access.can(rule.owner, device, access.OPERATOR):
        return "The rule owner needs Operator access to this device."
    return ""


def _next_number(rule):
    return (RulePublication.objects.filter(rule=rule).aggregate(n=Max("number"))["n"] or 0) + 1


def publish_rule(rule, user=None, compact=False):
    """Antrekan JSON aturan untuk semua device-nya. Mengembalikan RulePublication."""
    prefix = settings.MQTT_TOPIC_PREFIX
    body = rule_body(rule)
    rev = rule_rev(body)
    with transaction.atomic():
        number = _next_number(rule)
        # Disimpan sebagai teks yang siap dikirim (urutan kunci tidak boleh berubah, lihat RuleDelivery).
        payload = message_text(rule_message(body, rev, number, compact))
        publication = RulePublication.objects.create(
            rule=rule, number=number, action=RulePublication.Action.PUBLISH, rev=rev, compact=compact,
            size_bytes=len(payload.encode("utf-8")),
            created_by=user if user is not None and user.is_authenticated else None,
        )
        # Kiriman lama yang belum terkirim tidak perlu dikirim lagi.
        RuleDelivery.objects.filter(rule=rule, status=Status.QUEUED).update(status=Status.SUPERSEDED)
        for device in rule_devices(rule):
            reason = skip_reason(rule, device)
            RuleDelivery.objects.create(
                publication=publication, rule=rule, rule_number=rule.pk, device=device,
                mac=device.mac_address or "", topic=topics.rules(prefix, device.access_token, rule.pk),
                payload=None if reason else payload,
                status=Status.SKIPPED if reason else Status.QUEUED, detail=reason,
            )
    return publication


def _published_devices(rule):
    """Device yang saat ini masih menyimpan JSON aturan ini di broker (publish terakhir)."""
    last = RulePublication.objects.filter(rule=rule).order_by("-number").first()
    if last is None or last.action != RulePublication.Action.PUBLISH:
        return []
    return [d for d in last.deliveries.select_related("device") if d.status not in (Status.SKIPPED, Status.SUPERSEDED)]


def withdraw_rule(rule, user=None):
    """Hapus JSON aturan dari topik retained semua device yang pernah menerimanya."""
    targets = _published_devices(rule)
    with transaction.atomic():
        publication = RulePublication.objects.create(
            rule=rule, number=_next_number(rule), action=RulePublication.Action.WITHDRAW,
            created_by=user if user is not None and user.is_authenticated else None,
        )
        RuleDelivery.objects.filter(rule=rule, status=Status.QUEUED).update(status=Status.SUPERSEDED)
        for old in targets:
            RuleDelivery.objects.create(publication=publication, rule=rule, rule_number=rule.pk,
                                        device=old.device, mac=old.device.mac_address or "", topic=old.topic,
                                        payload=None)
    return publication


@receiver(pre_delete, sender=Rule)
def clear_on_delete(sender, instance, **kwargs):
    """Aturan dihapus: hapus juga JSON-nya dari broker (baris outbox tetap, rule -> NULL)."""
    for old in _published_devices(instance):
        RuleDelivery.objects.create(rule=None, rule_number=instance.pk, device=old.device,
                                    mac=old.device.mac_address or "", topic=old.topic, payload=None)


def handle_ack(device, rule_id, data):
    """Tanda terima dari device (dipanggil mqtt_bridge). Mengembalikan teks untuk log."""
    from api.payload import PayloadError

    if not isinstance(data, dict):
        raise PayloadError("rule acknowledgement must be a JSON object, e.g. {\"publish\": 3, \"ok\": true}.")
    deliveries = RuleDelivery.objects.filter(rule_number=rule_id, device=device, payload__isnull=False,
                                             publication__isnull=False).select_related("publication")
    number = data.get("publish")
    if isinstance(number, int) and not isinstance(number, bool):
        delivery = deliveries.filter(publication__number=number).first()
    else:
        delivery = deliveries.order_by("-pk").first()
    if delivery is None:
        raise PayloadError(f"no published rule {rule_id} for this device" + (f" with publish {number}" if number else "") + ".")
    ok = data.get("ok", True) is not False
    detail = str(data.get("error") or data.get("message") or "")[:200]
    if data.get("size") is not None:
        detail = (detail + " " if detail else "") + f"(device read {data.get('size')} bytes)"
    delivery.status = Status.RECEIVED if ok else Status.REJECTED
    delivery.acked_at = timezone.now()
    delivery.detail = detail[:255]
    delivery.save(update_fields=["status", "acked_at", "detail"])
    return f"rule {rule_id} publish {delivery.publication.number}: {'received' if ok else 'rejected'}" + (
        f" ({detail})" if detail else "")


def status_context(rule):
    """Data untuk bagian publish di kartu JSON (json_card.html, publish_card.html, publish_badge.html)."""
    publications = list(RulePublication.objects.filter(rule=rule).order_by("-number")[:5])
    last = publications[0] if publications else None
    last_publish = next((p for p in publications if p.action == RulePublication.Action.PUBLISH), None)
    body = rule_body(rule)
    current_rev = rule_rev(body)
    devices = rule_devices(rule)
    rows = []
    deliveries = {d.device_id: d for d in last.deliveries.select_related("device")} if last else {}
    for device in devices:
        rows.append({"device": device, "delivery": deliveries.get(device.pk),
                     "protocol": device.get_last_protocol_display() if device.last_protocol else "",
                     "mqtt": device.last_protocol == "MQTT" or device.mqtt_connected})
    waiting = any(d.status in WAITING for d in deliveries.values())
    message = None
    if last_publish is not None and last is last_publish:
        message = next((d.message for d in deliveries.values() if d.payload is not None), None)
    # Ringkasan untuk badge di judul kartu (publish terakhir; SKIPPED tidak dihitung).
    counted = [d for d in deliveries.values() if d.status not in (Status.SKIPPED, Status.SUPERSEDED)]
    summary = {
        "total": len(counted),
        "received": sum(d.status == Status.RECEIVED for d in counted),
        "rejected": sum(d.status == Status.REJECTED for d in counted),
        "waiting": sum(d.status in WAITING for d in counted),
    }
    counts = {}
    for d in deliveries.values():
        counts[d.status] = counts.get(d.status, 0) + 1
    return {
        "last": last,
        "history": publications,
        "rows": rows,
        "waiting": waiting,
        "counts": counts,
        "summary": summary,
        "current_rev": current_rev,
        "changed": bool(last_publish and last_publish.rev != current_rev and last and
                        last.action == RulePublication.Action.PUBLISH),
        "published": bool(last and last.action == RulePublication.Action.PUBLISH),
        # Perkiraan ukuran publish berikutnya untuk kedua bentuk pesan.
        "size": message_size(rule_message(body, current_rev, 0)),
        "size_compact": message_size(rule_message(body, current_rev, 0, compact=True)),
        "compact": bool(last_publish and last_publish.compact),
        # Pesan yang dikirim pada publish terakhir (sama untuk semua device), untuk tab Devices.
        "message": message,
        "message_json": json.dumps(message, indent=2, ensure_ascii=False) if message else "",
        "buffer": DEVICE_BUFFER_BYTES,
        "topic_example": topics.rules(settings.MQTT_TOPIC_PREFIX, "<token>", rule.pk),
    }

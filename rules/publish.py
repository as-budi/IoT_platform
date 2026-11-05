"""
Publish JSON aturan ke device (percobaan sebelum tahap 27).

Tombol "Publish to devices" di halaman aturan mengirim JSON aturan UTUH (bentuk yang sama
dengan kartu JSON / Export, termasuk daftar device dan MAC address-nya), tanpa
dekomposisi, ke SEMUA device yang dipakai aturan:

    <prefix>/<token>/rules/<rule id>        platform -> device, QoS 1, retained
    <prefix>/<token>/rules/<rule id>/ack    device -> platform, tanda terima

Isi pesan ke device:

    {"type": "rule",
     "publish": {"id": 3, "rev": "9f3a1c07", "at": "2026-11-04T20:15:00+07:00"},
     "device": {"name": "Pump node", "mac": "AA:BB:CC:00:00:03"},
     "ack": "<prefix>/<token>/rules/12/ack",
     "rule": { ...JSON aturan... }}

Tanda terima dari device:  {"publish": 3, "ok": true}
                    atau   {"publish": 3, "ok": false, "error": "payload too large"}

Retained: device yang sedang mati menerima JSON terakhir begitu tersambung dan subscribe.
"Withdraw" (dan menghapus aturan) mengirim pesan kosong retained, yang menghapus JSON itu
dari broker. Pengiriman dilakukan mqtt_bridge (pola outbox, mqtt_bridge/outbox.py).
"""
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from mqtt_bridge import topics

from .edge.ir import canonical, digest
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


def device_payload(rule, body, rev, publication, device):
    prefix = settings.MQTT_TOPIC_PREFIX
    return {
        "type": "rule",
        "publish": {"id": publication.number, "rev": rev,
                    "at": timezone.localtime(publication.created_at).isoformat(timespec="seconds")},
        "device": {"name": device.name, "mac": device.mac_address or None},
        "ack": topics.rule_ack(prefix, device.access_token, rule.pk),
        "rule": body,
    }


def skip_reason(rule, device):
    from sharing import access

    if not device.is_active:
        return "The device is inactive."
    if not access.can(rule.owner, device, access.OPERATOR):
        return "The rule owner needs Operator access to this device."
    return ""


def _next_number(rule):
    return (RulePublication.objects.filter(rule=rule).aggregate(n=Max("number"))["n"] or 0) + 1


def publish_rule(rule, user=None):
    """Antrekan JSON aturan untuk semua device-nya. Mengembalikan RulePublication."""
    prefix = settings.MQTT_TOPIC_PREFIX
    body = rule_body(rule)
    rev = rule_rev(body)
    with transaction.atomic():
        publication = RulePublication.objects.create(
            rule=rule, number=_next_number(rule), action=RulePublication.Action.PUBLISH, rev=rev,
            created_by=user if user is not None and user.is_authenticated else None,
        )
        # Kiriman lama yang belum terkirim tidak perlu dikirim lagi.
        RuleDelivery.objects.filter(rule=rule, status=Status.QUEUED).update(status=Status.SUPERSEDED)
        size = 0
        for device in rule_devices(rule):
            payload = device_payload(rule, body, rev, publication, device)
            size = max(size, len(canonical(payload).encode("utf-8")))
            reason = skip_reason(rule, device)
            RuleDelivery.objects.create(
                publication=publication, rule=rule, rule_number=rule.pk, device=device,
                mac=device.mac_address or "", topic=topics.rules(prefix, device.access_token, rule.pk),
                payload=None if reason else payload,
                status=Status.SKIPPED if reason else Status.QUEUED, detail=reason,
            )
        publication.size_bytes = size
        publication.save(update_fields=["size_bytes"])
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
    """Data untuk rules/includes/publish_card.html."""
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
    counts = {}
    for d in deliveries.values():
        counts[d.status] = counts.get(d.status, 0) + 1
    return {
        "last": last,
        "history": publications,
        "rows": rows,
        "waiting": waiting,
        "counts": counts,
        "current_rev": current_rev,
        "changed": bool(last_publish and last_publish.rev != current_rev and last and
                        last.action == RulePublication.Action.PUBLISH),
        "published": bool(last and last.action == RulePublication.Action.PUBLISH),
        "size": len(canonical(device_payload(rule, body, current_rev, RulePublication(number=0,
                    created_at=timezone.now()), devices[0])).encode()) if devices else 0,
        "buffer": DEVICE_BUFFER_BYTES,
        "topic_example": topics.rules(settings.MQTT_TOPIC_PREFIX, "<token>", rule.pk),
    }

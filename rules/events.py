"""
Kabar perubahan aturan untuk browser (WebSocket).

Kabar dikirim ke group DEVICE asal aturan (device pemilik variabel pemicu, atau
device yang diamati untuk event "device diam"). Dengan begitu tidak perlu
consumer baru: halaman aturan cukup berlangganan ws/devices/<id>/ milik device
itu, dan dashboard yang sudah berlangganan semua device ikut menerimanya.

    {"type": "rule", "rule": 4, "status": "ACTIVE", "transition": "ACTIVATE", "value": 18.0}
    {"type": "alarm", "id": 9, "rule": 4, "active": true, ...}      (lihat models.alarm_changed)
"""
from realtime import events


def originator_device_id(rule):
    """Device asal sebuah aturan ECA, atau None."""
    spec = getattr(rule, "eca", None)
    if spec is None:
        return None
    if spec.variable_id:
        return spec.variable.device_id
    return spec.device_id


def rule_changed(rule, state, transition=None, value=None):
    device_id = originator_device_id(rule)
    if device_id is None:
        return
    events.emit(device_id, {
        "type": "rule",
        "rule": rule.pk,
        "status": state.status,
        "transition": transition,
        "value": value,
    })

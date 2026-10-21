"""
Kabar perubahan aturan untuk browser (WebSocket).

Kabar dikirim ke group DEVICE asal aturan (ECA: device milik variabel di kondisi
PERTAMA, atau device yang diamati untuk event "device diam"; rule chain: device
yang pesannya baru saja melewati graf). Dengan begitu tidak perlu
consumer baru: halaman aturan cukup berlangganan ws/devices/<id>/ milik device
itu, dan dashboard yang sudah berlangganan semua device ikut menerimanya.

    {"type": "rule", "rule": 4, "status": "ACTIVE", "transition": "ACTIVATE", "value": 18.0}
    {"type": "rule_eval", "rule": 4}      dinilai ulang, status tetap (hanya dari pesan baru)
    {"type": "alarm", "id": 9, "rule": 4, "active": true, ...}      (lihat models.alarm_changed)
"""
from realtime import events


def originator_device_id(rule):
    """Device asal sebuah aturan, atau None.

    Rule chain yang mendengarkan SEMUA device tidak punya satu device asal;
    halamannya berlangganan kabar dashboard (semua device) sebagai gantinya.
    """
    flow = getattr(rule, "flow", None) if rule.model == "FLOW" else None
    if flow is not None:
        return flow.device_id
    if rule.model == "STATE_MACHINE":       # tahap 20: device yang diamati, atau None (semua device)
        machine = getattr(rule, "machine", None)
        return machine.device_id if machine else None
    spec = getattr(rule, "eca", None)
    if spec is None:
        return None
    if spec.event == spec.Event.INACTIVITY:
        return spec.device_id
    if spec.event == spec.Event.SCHEDULE:
        from .engines.eca import schedule_device

        device = schedule_device(rule)
        return device.pk if device else None
    primary = spec.primary_condition()
    return primary.variable.device_id if primary else None


def rule_changed(rule, state, transition=None, value=None, device_id=None):
    device_id = device_id or originator_device_id(rule)
    if device_id is None:
        return
    events.emit(device_id, {
        "type": "rule",
        "rule": rule.pk,
        "status": state.status,
        "transition": transition,
        "value": value,
    })


def rule_evaluated(rule, device_id=None):
    """Aturan baru saja dinilai tanpa berganti status. Halaman detail aturan
    memakai kabar ini untuk memuat ulang kotak "Last evaluation" saja."""
    device_id = device_id or originator_device_id(rule)
    if device_id is not None:
        events.emit(device_id, {"type": "rule_eval", "rule": rule.pk})

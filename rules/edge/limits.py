"""Batas micro rule. Nilai bawaan di sini, dapat diubah lewat settings.MICRO_RULES."""

VISIBILITIES = ("all", "admin", "off")

DEFAULTS = {
    "VISIBILITY": "off",         # all | admin | off (settings.MICRO_RULES, .env MICRO_RULES)
    "MAX_SIGNALS": 8,            # variabel berbeda dalam satu aturan
    "MAX_GROUPS": 4,             # grup OR per ekspresi (WHEN atau CLEAR)
    "MAX_CONDITIONS": 16,        # kondisi WHEN + CLEAR
    "MAX_ACTIONS": 4,            # aksi per on_activate / on_clear di satu device
    "MAX_SOURCES": 16,           # device sumber yang dilacak satu evaluator, lintas aturan
    "MAX_PUBLISHED_SIGNALS": 8,  # variabel yang dipancarkan satu device, lintas aturan
    "MAX_RULES_PER_DEVICE": 8,
    "MAX_FRAGMENT_BYTES": 1024,
    "MAX_FRAME_BYTES": 250,      # payload ESP-NOW v1
    "MAX_AGE_LIMIT": 6500,       # kolom umur frame: uint16 x 100 ms
    "TICK_SECONDS": 1,
    # Strategi SE (evaluator di sensor, tahap 26b)
    "SE_POLICY": "primary",      # primary | most_signals | min_traffic
    "SE_HEARTBEAT": 10,          # detik antar frame STATE bila tidak ada transisi
    "SE_REPEAT": 3,              # frame STATE dikirim sekian kali saat transisi
    "SE_REPEAT_GAP_MS": 100,
}


def limits():
    try:
        from django.conf import settings

        custom = getattr(settings, "MICRO_RULES", {}) if settings.configured else {}
    except Exception:  # pragma: no cover - dipakai tanpa Django
        custom = {}
    return {**DEFAULTS, **custom}


def visibility():
    """all / admin / off. Bentuk lama {"ENABLED": True/False} tetap diterima."""
    try:
        from django.conf import settings

        custom = getattr(settings, "MICRO_RULES", {}) if settings.configured else {}
    except Exception:  # pragma: no cover
        custom = {}
    value = custom.get("VISIBILITY")
    if value is None and "ENABLED" in custom:
        value = "all" if custom["ENABLED"] else "off"
    value = str(value or "off").lower()
    return value if value in VISIBILITIES else "off"


def enabled():
    """Fitur aktif untuk setidaknya sebagian pengguna (rencana disimpan, perintah berjalan)."""
    return visibility() != "off"


def visible_to(user):
    """Apakah `user` melihat fitur ini di halaman web."""
    value = visibility()
    if value == "all":
        return True
    if value == "admin":
        return bool(user is not None and user.is_authenticated and getattr(user, "is_admin", False))
    return False

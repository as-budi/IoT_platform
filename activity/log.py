"""
Menulis satu baris log aktivitas: record(...).

Pelaku (actor) diambil otomatis dari request yang sedang berjalan
(activity/middleware.py menyimpannya di contextvar). Di proses tanpa request
(mqtt_bridge, rule_engine, housekeeping) pelaku kosong dan actor_name diisi
pemanggil, mis. "rule: Irrigation" atau "system".

Log tidak boleh menggagalkan pekerjaan utamanya: kesalahan menulis log hanya
dicatat di logging Python.
"""
import contextvars
import logging

from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import ActivityEvent

logger = logging.getLogger(__name__)

# REQUEST yang sedang berjalan (diisi ActorMiddleware), bukan request.user.
# request.user adalah SimpleLazyObject: setiap kali disentuh (termasuk oleh
# isinstance()), ia memuat sesi dari database. asgiref memeriksa SEMUA contextvar
# dengan isinstance() saat berpindah antara kode async dan sync (Daphne/ASGI),
# sehingga objek lazy di contextvar memicu query di konteks async dan gagal
# (SynchronousOnlyOperation). Objek HttpRequest biasa aman diperiksa.
current_request = contextvars.ContextVar("activity_current_request", default=None)

Category = ActivityEvent.Category
Level = ActivityEvent.Level


def _user():
    """Pengguna request saat ini; dibaca hanya ketika record() dipanggil (kode sync)."""
    request = current_request.get()
    user = getattr(request, "user", None) if request is not None else None
    return user if user is not None and getattr(user, "is_authenticated", False) else None


def record(verb, message, *, category, owner=None, level=Level.INFO, device=None, rule=None,
           actor=None, actor_name="", at=None):
    """Simpan satu kejadian. `actor` bawaan = pengguna request saat ini (bila ada).
    `at` bawaan = sekarang; diisi waktu asli kejadian (mis. oleh activity_backfill)."""
    actor = actor if actor is not None else _user()
    if actor is not None and not actor_name:
        actor_name = actor.username
    try:
        with transaction.atomic():      # savepoint: gagal di sini tidak membatalkan transaksi pemanggil
            return ActivityEvent.objects.create(
                at=at or timezone.now(), verb=verb, message=message[:300], category=category, level=level,
                owner=owner, actor=actor, actor_name=(actor_name or "system")[:100],
                device=device, device_name=device.name[:100] if device else "",
                rule=rule, rule_name=rule.name[:100] if rule else "",
            )
    except DatabaseError:
        logger.exception("Could not write activity event %s", verb)
        return None


# Kejadian device BERSAMA yang terlihat oleh penerima share (tahap 19). Kategori
# Rule dan Account sengaja tidak: itu urusan pribadi pemilik device.
SHARED_CATEGORIES = [Category.DEVICE, Category.CONTROL, Category.ALARM, Category.FIRMWARE, Category.SHARING]


def visible_events(user):
    """Admin: semua baris. Pengguna lain: baris yang owner-nya dia sendiri, ditambah
    kejadian device/kontrol/alarm/firmware/berbagi pada device yang dibagikan kepadanya."""
    from sharing import access

    events = ActivityEvent.objects.select_related("device", "rule")
    if user.is_admin:
        return events.select_related("owner")
    shared = access.shares_of(user).values("device_id")
    return events.filter(Q(owner=user) | Q(device_id__in=shared, category__in=SHARED_CATEGORIES))

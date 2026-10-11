"""Variabel tambahan untuk SEMUA template (didaftarkan di settings.TEMPLATES)."""
from .models import Alarm


def alarms(request):
    """Jumlah alarm aktif yang belum di-acknowledge, untuk lencana di side menu."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    from devices.views import visible_devices

    count = Alarm.objects.active().filter(
        acknowledged_at__isnull=True, device__in=visible_devices(user)
    ).count()
    return {"active_alarm_count": count}

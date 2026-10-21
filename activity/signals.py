"""
Penerima sinyal Django yang menulis log aktivitas (tahap 18).

Mengapa sinyal, bukan memanggil record() di setiap view? Kejadian yang sama bisa
berasal dari banyak tempat: aktuator diubah lewat halaman device, dashboard,
aturan ECA, atau node rule chain; command dibuat lewat halaman, aturan, atau
OTA. Dengan sinyal post_save pada model (DesiredStateLog, Command, ...), semua
jalur itu tercatat tanpa mengubah kodenya satu per satu.

Yang SENGAJA tidak dicatat: setiap baris telemetri (bisa jutaan per hari; datanya
sudah ada di tabel Telemetry) dan pemeriksaan aturan yang tidak mengubah apa-apa.

Penghapusan berantai (mis. pengguna dihapus -> device-nya ikut terhapus) tidak
dicatat per baris: sinyal post_delete memberi `origin`, dan hanya penghapusan
yang dimulai dari objek itu sendiri yang dicatat. Ini juga mencegah baris log
baru yang menunjuk ke pengguna yang sedang dihapus.
"""
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models import QuerySet
from django.db.models.signals import post_delete, post_init, post_save
from django.dispatch import receiver

from devices.models import Command, CommandStatus, DesiredStateLog, Device, DeviceStatusEvent, Location, Variable
from notifications.models import TelegramLink
from ota.models import OtaEvent, OtaStatus
from rules.models import Alarm, Rule, RuleExecution

from .log import Category, Level, _user as current_user, record


def _own_delete(instance, origin):
    """True bila penghapusan dimulai dari objek ini, atau dari queryset model yang sama
    (bukan bagian dari penghapusan berantai, mis. device ikut terhapus bersama pemiliknya)."""
    if isinstance(origin, QuerySet):
        return origin.model is type(instance)
    return origin is instance or (type(origin) is type(instance) and getattr(origin, "pk", None) == instance.pk)


def _fmt(number):
    return "—" if number is None else f"{number:g}"


# --- Device, lokasi, variabel --------------------------------------------------------

@receiver(post_save, sender=Device)
def device_saved(sender, instance, created, update_fields=None, raw=False, **kwargs):
    if raw:
        return
    if created:
        record("device.created", f"Device \"{instance.name}\" created", category=Category.DEVICE,
               owner=instance.owner, device=instance, at=instance.created_at)
    elif update_fields is None:
        record("device.updated", f"Device \"{instance.name}\" settings changed", category=Category.DEVICE,
               owner=instance.owner, device=instance)
    elif set(update_fields) == {"access_token"}:
        record("device.token", f"New access token for \"{instance.name}\" (the old one stopped working)",
               category=Category.DEVICE, level=Level.WARNING, owner=instance.owner, device=instance)


@receiver(post_delete, sender=Device)
def device_deleted(sender, instance, origin=None, **kwargs):
    if _own_delete(instance, origin):
        record("device.deleted", f"Device \"{instance.name}\" deleted with its data", category=Category.DEVICE,
               level=Level.WARNING, owner=instance.owner)


@receiver(post_save, sender=DeviceStatusEvent)
def device_status(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        device = instance.device
        state = "online" if instance.online else "offline"
        record(f"device.{state}", f"{device.name} is {state}: {instance.reason}", category=Category.DEVICE,
               level=Level.INFO if instance.online else Level.WARNING, owner=device.owner, device=device,
               actor_name="system", at=instance.at)


@receiver(post_save, sender=Location)
def location_saved(sender, instance, created, raw=False, **kwargs):
    if not raw:
        verb, text = ("location.created", "created") if created else ("location.updated", "changed")
        record(verb, f"Location \"{instance.name}\" {text}", category=Category.DEVICE, owner=instance.owner)


@receiver(post_delete, sender=Location)
def location_deleted(sender, instance, origin=None, **kwargs):
    if _own_delete(instance, origin):
        record("location.deleted", f"Location \"{instance.name}\" deleted", category=Category.DEVICE,
               owner=instance.owner)


@receiver(post_save, sender=Variable)
def variable_saved(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        device = instance.device
        record("variable.created", f"Variable \"{instance.key}\" added to {device.name}", category=Category.DEVICE,
               owner=device.owner, device=device, actor_name="device")


@receiver(post_delete, sender=Variable)
def variable_deleted(sender, instance, origin=None, **kwargs):
    if _own_delete(instance, origin):
        device = instance.device
        record("variable.deleted", f"Variable \"{instance.key}\" removed from {device.name} with its data",
               category=Category.DEVICE, level=Level.WARNING, owner=device.owner, device=device)


# --- Kontrol: aktuator dan command ----------------------------------------------------

@receiver(post_save, sender=DesiredStateLog)
def desired_changed(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    variable = instance.variable
    device = variable.device
    shown = ("ON" if instance.value >= 0.5 else "OFF") if variable.is_boolean else _fmt(instance.value)
    # changed_by kosong = bukan orang, melainkan aturan (lihat Variable.set_desired).
    record("actuator.set", f"{variable.display_name} on {device.name} set to {shown}", category=Category.CONTROL,
           owner=device.owner, device=device, actor=instance.changed_by,
           actor_name="" if instance.changed_by else "rule engine", at=instance.created_at)


@receiver(post_init, sender=Command)
def command_loaded(sender, instance, **kwargs):
    instance._activity_status = instance.status


@receiver(post_save, sender=Command)
def command_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    device = instance.device
    if created:
        record("command.created", f"Command \"{instance.method}\" queued for {device.name}",
               category=Category.CONTROL, owner=device.owner, device=device, actor=instance.created_by,
               actor_name="" if instance.created_by else "system", at=instance.created_at)
    elif instance.is_final and instance._activity_status != instance.status:
        failed = instance.status != CommandStatus.SUCCEEDED
        record(f"command.{instance.status.lower()}",
               f"Command \"{instance.method}\" on {device.name}: {instance.get_status_display().lower()}",
               category=Category.CONTROL, level=Level.WARNING if failed else Level.INFO,
               owner=device.owner, device=device, at=instance.completed_at,
               actor_name="system" if instance.status == CommandStatus.EXPIRED else "device")
    instance._activity_status = instance.status


# --- Aturan dan alarm --------------------------------------------------------------------

@receiver(post_save, sender=Rule)
def rule_saved(sender, instance, created, update_fields=None, raw=False, **kwargs):
    if raw:
        return
    if created:
        record("rule.created", f"Rule \"{instance.name}\" created ({instance.model})", category=Category.RULE,
               owner=instance.owner, rule=instance, at=instance.created_at)
    elif update_fields and "is_enabled" in update_fields:
        state = "enabled" if instance.is_enabled else "disabled"
        record(f"rule.{state}", f"Rule \"{instance.name}\" {state}", category=Category.RULE,
               owner=instance.owner, rule=instance)
    elif update_fields is None:
        record("rule.updated", f"Rule \"{instance.name}\" changed", category=Category.RULE,
               owner=instance.owner, rule=instance)


@receiver(post_delete, sender=Rule)
def rule_deleted(sender, instance, origin=None, **kwargs):
    if _own_delete(instance, origin):
        record("rule.deleted", f"Rule \"{instance.name}\" deleted", category=Category.RULE, owner=instance.owner)


TRANSITION_TEXT = {"ACTIVATE": "activated", "CLEAR": "cleared", "RUN": "ran its actions", "SKIPPED": "skipped"}


@receiver(post_save, sender=RuleExecution)
def rule_executed(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    rule = instance.rule
    results = "; ".join(f"{r.get('action')}: {r.get('detail')}" for r in instance.results or [])
    if instance.transition == "MOVE":       # state machine (tahap 20)
        device = instance.device
        name = device.name if device else "device"
        text = f"Rule \"{rule.name}\": {name} {instance.from_state} → {instance.to_state}"
        record("rule.move", text + (f" — {results}" if results else ""), category=Category.RULE,
               level=Level.INFO if instance.ok else Level.ERROR, owner=rule.owner, rule=rule, device=device,
               # Tombol manual: pelakunya pengguna yang menekan (dari request); lainnya mesin aturan.
               actor_name="" if current_user() else "rule engine", at=instance.created_at)
        return
    text = f"Rule \"{rule.name}\" {TRANSITION_TEXT.get(instance.transition, instance.transition.lower())}"
    if instance.value is not None:
        text += f" (value {_fmt(instance.value)})"
    level = Level.INFO if instance.ok else Level.ERROR
    if instance.transition == "SKIPPED":
        level = Level.WARNING
    from rules.events import originator_device_id

    device = Device.objects.filter(pk=originator_device_id(rule)).first() if rule.model == "ECA" else None
    record(f"rule.{instance.transition.lower()}", text + (f" — {results}" if results else ""),
           category=Category.RULE, level=level, owner=rule.owner, rule=rule, device=device,
           actor_name="rule engine", at=instance.created_at)


SEVERE = {Alarm.Severity.CRITICAL, Alarm.Severity.MAJOR}


@receiver(post_save, sender=Alarm)
def alarm_saved(sender, instance, created, update_fields=None, raw=False, **kwargs):
    if raw:
        return
    device = instance.device
    common = {"category": Category.ALARM, "owner": device.owner, "device": device, "rule": instance.rule}
    if created:
        record("alarm.raised", f"{instance.get_severity_display()} alarm: {instance.message}",
               level=Level.ERROR if instance.severity in SEVERE else Level.WARNING,
               actor_name="rule engine", at=instance.created_at, **common)
    elif update_fields and "acknowledged_at" in update_fields:
        record("alarm.acknowledged", f"Alarm acknowledged: {instance.message}", actor=instance.acknowledged_by,
               at=instance.acknowledged_at, **common)
    elif update_fields and "cleared_at" in update_fields:
        record("alarm.cleared", f"Alarm cleared: {instance.message}", actor=instance.cleared_by,
               actor_name="" if instance.cleared_by else "rule engine", at=instance.cleared_at, **common)


# --- Firmware ----------------------------------------------------------------------------

@receiver(post_save, sender=OtaEvent)
def ota_event(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    update = instance.update
    device = update.device
    package = update.package
    failed = instance.status == OtaStatus.FAILED
    text = f"Firmware {package.version} on {device.name}: {instance.get_status_display().lower()}"
    if instance.message:
        text += f" ({instance.message})"
    record(f"firmware.{instance.status.lower()}", text, category=Category.FIRMWARE,
           level=Level.ERROR if failed else Level.INFO, owner=device.owner, device=device,
           actor_name=instance.source or "system", at=instance.created_at)


# --- Akun ----------------------------------------------------------------------------------

def _ip(request):
    return request.META.get("REMOTE_ADDR", "") if request is not None else ""


@receiver(user_logged_in)
def logged_in(sender, request, user, **kwargs):
    record("account.login", f"{user.username} signed in from {_ip(request) or 'unknown address'}",
           category=Category.ACCOUNT, owner=user, actor=user)


@receiver(user_logged_out)
def logged_out(sender, request, user, **kwargs):
    if user is not None:
        record("account.logout", f"{user.username} signed out", category=Category.ACCOUNT, owner=user, actor=user)


@receiver(user_login_failed)
def login_failed(sender, credentials, request=None, **kwargs):
    from django.contrib.auth import get_user_model

    username = (credentials or {}).get("username", "")
    owner = get_user_model().objects.filter(username=username).first()
    # Pemilik akun melihat percobaan gagal ke akunnya; nama yang tidak dikenal hanya untuk Admin.
    record("account.login_failed", f"Failed sign-in as \"{username[:40]}\" from {_ip(request) or 'unknown address'}",
           category=Category.ACCOUNT, level=Level.WARNING, owner=owner, actor_name="anonymous")


@receiver(post_save, sender=TelegramLink)
def telegram_linked(sender, instance, raw=False, **kwargs):
    if not raw:
        record("account.telegram", f"Telegram connected ({instance.chat_name or instance.chat_id})",
               category=Category.ACCOUNT, owner=instance.user, actor=instance.user)


@receiver(post_delete, sender=TelegramLink)
def telegram_unlinked(sender, instance, origin=None, **kwargs):
    if _own_delete(instance, origin):
        record("account.telegram", "Telegram disconnected", category=Category.ACCOUNT, owner=instance.user)

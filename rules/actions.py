"""
Menjalankan aksi sebuah aturan.

Setiap jenis aksi adalah satu fungsi kecil yang mengembalikan teks hasil
(untuk log) atau melempar ActionError. Kegagalan satu aksi TIDAK menghentikan
aksi lain; semua hasil dicatat di RuleExecution.results:

    [{"action": "Set actuator", "ok": true,  "detail": "Water pump = ON"},
     {"action": "Send email",   "ok": false, "detail": "SMTP connection refused"}]

Aksi dirancang IDEMPOTEN bila memungkinkan (aman bila terulang):
- Set actuator dilewati bila desired state sudah bernilai sama.
- Raise alarm tidak membuat alarm kedua bila alarm aturan ini masih aktif.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from devices.models import Command, Variable
from realtime import events

from .models import Alarm, RuleAction, RuleExecution, alarm_changed

logger = logging.getLogger(__name__)


# Akhiran hasil "Set actuator" bila desired state sudah bernilai sama (tidak ada perubahan).
ALREADY_REQUESTED = "(already requested)"


class ActionError(Exception):
    """Aksi tidak bisa dijalankan; pesannya ditampilkan di log aturan."""


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render_message(template, context):
    """"{device}: {variable} = {value}" -> "Greenhouse Node: Soil moisture = 18.5"."""
    values = {key: ("—" if value is None else value) for key, value in context.items()}
    if isinstance(values.get("value"), float):
        values["value"] = f"{values['value']:g}"
    try:
        return template.format_map(SafeDict(values))
    except (ValueError, IndexError):
        return template  # placeholder tidak valid: tampilkan apa adanya


def show_value(variable, value):
    if variable.is_boolean:
        return "ON" if value >= 0.5 else "OFF"
    return f"{value:g}{(' ' + variable.unit) if variable.unit else ''}"


def allowed_devices(rule):
    """Device yang boleh DIKONTROL aturan ini = device milik pemiliknya, atau yang
    dibagikan kepadanya dengan peran Operator/Editor (tahap 19).

    Diperiksa ulang SAAT aksi dijalankan: kepemilikan atau share bisa berubah
    (dicabut, diturunkan ke Viewer) setelah aturan dibuat.
    """
    from devices.views import visible_devices
    from sharing import access

    return visible_devices(rule.owner, access.OPERATOR)


def note_foreign_action(rule, device, text):
    """Aturan orang lain mengontrol device ini (lewat share): catat di log pemilik device,
    supaya ia tahu SIAPA yang menyalakan pompanya, bukan hanya "rule engine"."""
    if device.owner_id == rule.owner_id:
        return
    from activity.log import Category, record

    record("rule.shared_action", f"Rule \"{rule.name}\" of {rule.owner.username}: {text}",
           category=Category.CONTROL, owner=device.owner, device=device, actor=rule.owner)


# --- jenis aksi --------------------------------------------------------------------

def set_actuator(action, rule, context):
    variable = action.target_variable
    if variable is None or not variable.is_actuator:
        raise ActionError("The target is not an actuator.")
    if not allowed_devices(rule).filter(pk=variable.device_id).exists():
        raise ActionError(f"The rule owner has no access to {variable.device}.")
    value = action.value
    if variable.is_boolean and value not in (0, 1):
        raise ActionError("On/off actuators accept 0 or 1.")
    if variable.min_value is not None and value < variable.min_value:
        raise ActionError(f"Value must be at least {variable.min_value:g}.")
    if variable.max_value is not None and value > variable.max_value:
        raise ActionError(f"Value must be at most {variable.max_value:g}.")

    # Baca ulang dari database: desired bisa saja baru diubah orang lain.
    variable = Variable.objects.get(pk=variable.pk)
    label = f"{variable.display_name} = {show_value(variable, value)}"
    if variable.desired_value is not None and abs(variable.desired_value - value) < 1e-9:
        return f"{label} {ALREADY_REQUESTED}"
    variable.set_desired(value, None)  # None = bukan pengguna, melainkan aturan
    note_foreign_action(rule, variable.device, label)
    return label


def send_command(action, rule, context):
    device = action.target_device
    if device is None:
        raise ActionError("No device selected.")
    if not allowed_devices(rule).filter(pk=device.pk).exists():
        raise ActionError(f"The rule owner has no access to {device}.")
    command = Command.objects.create(
        device=device,
        method=action.method,
        params=action.params or {},
        expires_at=timezone.now() + timedelta(seconds=action.ttl_seconds or 300),
    )
    events.command_changed(command)
    note_foreign_action(rule, device, f"command {command.method}")
    return f"{device.name}: {command.method} (command #{command.pk})"


def raise_alarm(action, rule, context):
    device = context.get("originator")
    if device is None:
        # Aturan berjadwal tanpa kondisi dan tanpa aksi ke device (tahap 14).
        raise ActionError("No device to attach the alarm to: add a condition or a device action.")
    # Tahap 19: alarm ikut tampil di daftar alarm pemilik device, jadi pada device
    # yang dibagikan butuh peran Operator. Viewer cukup memakai aksi email/Telegram.
    if not allowed_devices(rule).filter(pk=device.pk).exists():
        raise ActionError(f"An alarm on {device.name} needs Operator access to it; "
                          "use an email or Telegram action instead.")
    existing = Alarm.objects.active().filter(rule=rule, device=device).first()
    if existing:
        return f"Alarm #{existing.pk} is still active"
    alarm = Alarm.objects.create(
        rule=rule,
        device=device,
        severity=action.severity or Alarm.Severity.MAJOR,
        message=render_message(action.message or "{rule}: {variable} = {value}", context),
        value=context.get("value"),
    )
    alarm_changed(alarm)
    return f"{alarm.get_severity_display()} alarm #{alarm.pk}"


def send_email(action, rule, context):
    recipients = [r.strip() for r in action.recipients.split(",") if r.strip()]
    if not recipients:
        if not rule.owner.email:
            raise ActionError("No recipients, and the rule owner has no email address.")
        recipients = [rule.owner.email]
    transition = context.get("transition", "activated")
    subject = f"[IoT] {rule.name} {transition}"
    body = render_message(
        action.message or "Rule \"{rule}\" {transition}.\nValues: {values}.",
        context,
    )
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients)
    except Exception as exc:  # SMTP salah, server mati, ...
        raise ActionError(f"Email not sent: {exc}") from exc
    return "Email to " + ", ".join(recipients)


def telegram_chats(action, rule):
    """Id chat tujuan: isian "recipients", atau chat yang disambungkan pemilik aturan."""
    chats = [r.strip() for r in action.recipients.split(",") if r.strip()]
    if chats:
        return chats
    from notifications.models import TelegramLink

    link = TelegramLink.objects.filter(user=rule.owner).first()
    if link is None:
        raise ActionError("The rule owner has not connected Telegram (My profile → Telegram).")
    return [link.chat_id]


def send_telegram(action, rule, context):
    """Kirim pesan Telegram (tahap 17, notifications/telegram.py)."""
    from notifications import telegram

    if not telegram.enabled():
        raise ActionError("Telegram is not configured on this server (TELEGRAM_BOT_TOKEN).")
    text = render_message(action.message or "Rule \"{rule}\" {transition}.\nValues: {values}.", context)
    sent, failed = [], []
    for chat in telegram_chats(action, rule):
        try:
            telegram.send_message(chat, text)
            sent.append(chat)
        except telegram.TelegramError as exc:
            failed.append(f"{chat}: {exc}")
    if failed:
        raise ActionError("; ".join(failed) + (f" (sent to {', '.join(sent)})" if sent else ""))
    return "Telegram to " + ("the owner" if not action.recipients.strip() else ", ".join(sent))


HANDLERS = {
    RuleAction.Kind.SET_ACTUATOR: set_actuator,
    RuleAction.Kind.SEND_COMMAND: send_command,
    RuleAction.Kind.ALARM: raise_alarm,
    RuleAction.Kind.EMAIL: send_email,
    RuleAction.Kind.TELEGRAM: send_telegram,
}


def clear_alarms(rule):
    """Saat aturan pulih (CLEAR), alarm yang dibuatnya ikut di-clear otomatis."""
    cleared = []
    for alarm in Alarm.objects.active().filter(rule=rule):
        alarm.clear(user=None)
        cleared.append(f"#{alarm.pk}")
    return cleared


def run_actions(rule, transition, value, context):
    """Jalankan aksi yang sesuai `transition` (ACTIVATE/CLEAR). Mengembalikan daftar hasil."""
    context = {
        **context,
        "transition": "activated" if transition == RuleExecution.Transition.ACTIVATE else "cleared",
    }
    results = []
    for action in rule.actions.filter(run_on=transition).select_related(
        "target_variable__device", "target_device"
    ):
        try:
            detail = HANDLERS[action.kind](action, rule, context)
            results.append({"action": action.get_kind_display(), "ok": True, "detail": detail})
        except Exception as exc:
            if not isinstance(exc, ActionError):
                logger.exception("Action %s of rule %s failed", action.pk, rule.pk)
            results.append({"action": action.get_kind_display(), "ok": False, "detail": str(exc)})

    if transition == RuleExecution.Transition.CLEAR:
        cleared = clear_alarms(rule)
        if cleared:
            results.append({"action": "Clear alarm", "ok": True, "detail": "Cleared " + ", ".join(cleared)})
    return results

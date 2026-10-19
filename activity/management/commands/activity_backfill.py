"""
Perintah kustom:  python manage.py activity_backfill [--dry-run]

Mengisi log aktivitas dari riwayat yang SUDAH ADA sebelum tahap 18, supaya halaman
Activity log langsung menampilkan kejadian lama: device & aturan dibuat, perubahan
aktuator, command, eksekusi aturan, alarm, status online/offline, dan pembaruan
firmware, masing-masing dengan waktu aslinya.

Hanya kejadian yang LEBIH LAMA dari baris log pertama yang ditambahkan, jadi aman
dijalankan dua kali (yang kedua tidak menambah apa pun). Login lama tidak bisa
dipulihkan karena memang tidak pernah disimpan.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from activity import signals
from activity.models import ActivityEvent
from devices.models import Command as DeviceCommand
from devices.models import DesiredStateLog, Device, DeviceStatusEvent
from ota.models import OtaEvent
from rules.models import Alarm, Rule, RuleExecution


class Command(BaseCommand):
    help = "Fill the activity log from history recorded before the log existed."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count only, write nothing.")

    def handle(self, *args, **options):
        first = ActivityEvent.objects.order_by("at").values_list("at", flat=True).first()
        before = first or timezone.now()
        self.stdout.write(f"Adding events older than {timezone.localtime(before):%Y-%m-%d %H:%M:%S}.")
        jobs = [
            ("devices", Device.objects.filter(created_at__lt=before).select_related("owner"),
             lambda o: signals.device_saved(Device, o, created=True)),
            ("rules", Rule.objects.filter(created_at__lt=before).select_related("owner"),
             lambda o: signals.rule_saved(Rule, o, created=True)),
            ("device status changes", DeviceStatusEvent.objects.filter(at__lt=before).select_related("device__owner"),
             lambda o: signals.device_status(DeviceStatusEvent, o, created=True)),
            ("actuator changes",
             DesiredStateLog.objects.filter(created_at__lt=before).select_related(
                 "variable__device__owner", "changed_by"),
             lambda o: signals.desired_changed(DesiredStateLog, o, created=True)),
            ("commands", self.commands(before), self.command),
            ("rule executions", RuleExecution.objects.filter(created_at__lt=before).select_related("rule__owner"),
             lambda o: signals.rule_executed(RuleExecution, o, created=True)),
            ("alarms", Alarm.objects.filter(created_at__lt=before).select_related("device__owner", "rule"),
             lambda o: self.alarm(o, before)),
            ("firmware events", OtaEvent.objects.filter(created_at__lt=before).select_related(
                "update__device__owner", "update__package"),
             lambda o: signals.ota_event(OtaEvent, o, created=True)),
        ]
        total = 0
        with transaction.atomic():
            for label, objects, handler in jobs:
                count = 0
                for obj in objects.order_by("pk").iterator(chunk_size=500):
                    if not options["dry_run"]:
                        handler(obj)
                    count += 1
                total += count
                self.stdout.write(f"  {label}: {count}")
            if options["dry_run"]:
                transaction.set_rollback(True)
        verb = "Would add" if options["dry_run"] else "Added"
        self.stdout.write(self.style.SUCCESS(f"{verb} events from {total} records."))

    def commands(self, before):
        return DeviceCommand.objects.filter(created_at__lt=before).select_related("device__owner", "created_by")

    def command(self, command):
        signals.command_saved(DeviceCommand, command, created=True)
        if command.is_final and command.completed_at:
            command._activity_status = None          # supaya dianggap "baru saja selesai"
            signals.command_saved(DeviceCommand, command, created=False)

    def alarm(self, alarm, before):
        signals.alarm_saved(Alarm, alarm, created=True)
        if alarm.acknowledged_at and alarm.acknowledged_at < before:
            signals.alarm_saved(Alarm, alarm, created=False, update_fields=["acknowledged_at"])
        if alarm.cleared_at and alarm.cleared_at < before:
            signals.alarm_saved(Alarm, alarm, created=False, update_fields=["cleared_at"])

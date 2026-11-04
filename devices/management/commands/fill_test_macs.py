"""
python manage.py fill_test_macs [--owner siti] [--device "Pump node"] [--all] [--random] [--dry-run]

Mengisi MAC address TIRUAN untuk device yang belum melaporkan MAC (tahap 24), supaya
fitur yang membutuhkan MAC, mis. micro rule (tahap 26, bagian "Run on devices"), bisa
dicoba tanpa board ESP32 sungguhan.

- Bawaan: MAC diturunkan dari access token, sama dengan device tiruan
  (scripts/fake_mqtt_device.py) dan MQTT client: 02 + 10 hex pertama SHA-256(token).
  Jadi bila device tiruan itu kemudian melaporkan firmware, MAC-nya tidak berubah.
- --random: MAC acak (secrets), dijamin unik di database.
- Byte pertama 02 = "locally administered": tidak bentrok dengan MAC kartu jaringan asli.
- mac_reported_at dibiarkan kosong (bukan laporan device). Laporan firmware dari device
  sungguhan otomatis menimpa MAC tiruan.
- Setelah itu, rencana micro rule aturan yang memakai device tersebut dibuat ulang.

Di server produksi (DEBUG=False) perintah ini meminta --yes.
"""
import hashlib
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from devices.models import Device


def token_mac(token):
    digest = hashlib.sha256(token.encode()).hexdigest()[:10].upper()
    return ":".join(["02"] + [digest[i:i + 2] for i in range(0, 10, 2)])


def random_mac():
    return ":".join(["02"] + [f"{secrets.randbelow(256):02X}" for _ in range(5)])


class Command(BaseCommand):
    help = "Fill test MAC addresses for devices that have not reported one."

    def add_arguments(self, parser):
        parser.add_argument("--owner", help="Only devices of this username.")
        parser.add_argument("--device", action="append", default=[], help="Device name or id (repeatable).")
        parser.add_argument("--all", action="store_true", help="Also replace MAC addresses that are already set.")
        parser.add_argument("--random", action="store_true", help="Random MAC instead of one derived from the token.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving.")
        parser.add_argument("--yes", action="store_true", help="Required when DEBUG is off (production).")

    def handle(self, *args, **o):
        if not settings.DEBUG and not o["yes"] and not o["dry_run"]:
            raise CommandError("DEBUG is off: this looks like a production server. Add --yes to fill test MAC "
                               "addresses anyway (real device reports will replace them).")
        devices = Device.objects.select_related("owner").order_by("pk")
        if o.get("owner"):
            devices = devices.filter(owner__username=o["owner"])
        if o["device"]:
            query = Q()
            for value in o["device"]:
                query |= Q(name=value) | (Q(pk=int(value)) if value.isdigit() else Q())
            devices = devices.filter(query)
        if not o["all"]:
            devices = devices.filter(mac_address="")
        devices = list(devices)
        if not devices:
            self.stdout.write("No devices to change.")
            return

        used = set(Device.objects.exclude(mac_address="").values_list("mac_address", flat=True))
        changed = []
        for device in devices:
            mac = random_mac() if o["random"] else token_mac(device.access_token)
            while mac in used and mac != device.mac_address:
                mac = random_mac()
            used.add(mac)
            self.stdout.write(f"{device.owner.username:<12} {device.name:<30} {device.mac_address or '-':<17} -> {mac}")
            if not o["dry_run"]:
                device.mac_address = mac
                device.mac_reported_at = None
                device.save(update_fields=["mac_address", "mac_reported_at"])
                changed.append(device)
        if o["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Dry run: {len(devices)} device(s) would change."))
            return
        rules = self.recompile(changed)
        self.stdout.write(self.style.SUCCESS(
            f"{len(changed)} device(s) updated; device plans updated for {rules} rule(s)."))

    def recompile(self, devices):
        """Perbarui rencana micro rule aturan yang memakai device ini (bila fiturnya aktif)."""
        from rules.edge.limits import enabled
        from rules.edge.services import compile_and_store
        from rules.models import Rule

        if not enabled():
            return 0
        ids = [d.pk for d in devices]
        rules = Rule.objects.filter(edge_enabled=True).filter(
            Q(eca__conditions__variable__device_id__in=ids)
            | Q(actions__target_variable__device_id__in=ids)
            | Q(actions__target_device_id__in=ids)
        ).distinct()
        for rule in rules:
            compile_and_store(rule)
        return len(rules)

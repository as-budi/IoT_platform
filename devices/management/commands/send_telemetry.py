"""
Perintah kustom:  python manage.py send_telemetry "<nama device>" key=nilai [key=nilai ...]

Berpura-pura menjadi device dan mengirim SATU paket telemetri, tanpa perlu
access token, curl, atau MQTT. Datanya disimpan lewat api.services.save_telemetry(),
jalur yang SAMA dengan HTTP/MQTT/CoAP. Akibatnya dashboard ikut berubah lewat
WebSocket dan rule engine ikut menilai aturan.

Contoh:
    python manage.py send_telemetry "Poultry House Node" temperature=34 humidity=75
    python manage.py send_telemetry "Fish Pond Node" dissolved_oxygen=3.5 --owner andi
    python manage.py send_telemetry 7 pump=1            # device id juga boleh

Nilai on/off ditulis 1 atau 0 (juga diterima: on/off, true/false).
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from api.services import save_telemetry
from devices.models import Device

WORDS = {"on": 1.0, "true": 1.0, "off": 0.0, "false": 0.0}


def parse_pair(text):
    if "=" not in text:
        raise CommandError(f"Expected key=value, got {text!r}.")
    key, raw = text.split("=", 1)
    key, raw = key.strip(), raw.strip().lower()
    if raw in WORDS:
        return key, WORDS[raw]
    try:
        return key, float(raw)
    except ValueError:
        raise CommandError(f"{key}: {raw!r} is not a number.")


class Command(BaseCommand):
    help = "Send one telemetry message as if it came from a device (for demos and testing rules)."

    def add_arguments(self, parser):
        parser.add_argument("device", help="Device name (in quotes if it has spaces) or id.")
        parser.add_argument("values", nargs="+", help="key=value pairs, e.g. temperature=34")
        parser.add_argument("--owner", help="Owner username, when several users have a device with this name.")

    def handle(self, *args, **options):
        device = self.find_device(options["device"], options["owner"])
        pairs = [parse_pair(text) for text in options["values"]]
        now = timezone.now()
        created = save_telemetry(device, [(key, value, now) for key, value in pairs])
        shown = ", ".join(f"{key}={value:g}" for key, value in pairs)
        self.stdout.write(self.style.SUCCESS(f"Sent to {device.name} ({device.owner.username}): {shown}"))
        if created:
            self.stdout.write(self.style.WARNING(
                f"New variable(s) created: {', '.join(created)}. Check the key spelling if this was not intended."
            ))

    def find_device(self, text, owner):
        devices = Device.objects.select_related("owner")
        if text.isdigit():
            matches = devices.filter(pk=int(text))
        else:
            matches = devices.filter(name=text)
        if owner:
            matches = matches.filter(owner__username=owner)
        matches = list(matches)
        if not matches:
            raise CommandError(f"No device {text!r}" + (f" owned by {owner}" if owner else "") + ".")
        if len(matches) > 1:
            names = ", ".join(d.owner.username for d in matches)
            raise CommandError(f"Several devices are called {text!r} (owners: {names}). Add --owner.")
        return matches[0]

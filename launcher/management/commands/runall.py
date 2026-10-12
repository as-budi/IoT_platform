"""
Perintah kustom:  python manage.py runall

Menjalankan SEMUA layanan platform dari satu terminal:

    web    runserver      halaman web + WebSocket (Daphne)
    mqtt   mqtt_bridge    device lewat MQTT
    coap   coap_server    device lewat CoAP
    rules  rule_engine    worker rule engine

Setiap layanan tetap proses tersendiri (lihat launcher/supervisor.py); runall
hanya "pengasuh"-nya: log digabung dengan awalan nama, layanan yang crash
dinyalakan ulang otomatis, Ctrl+C mematikan semuanya.

    python manage.py runall                          # semua, web di 127.0.0.1:8000
    python manage.py runall 0.0.0.0:8000             # web bisa dibuka dari jaringan (hati-hati)
    python manage.py runall --skip coap              # tanpa CoAP
    python manage.py runall --only web,rules         # hanya web dan rule engine

Untuk PENGEMBANGAN dan praktikum. Di server produksi pakai systemd,
supervisord, atau Docker Compose (lihat docs/menjalankan-semua-layanan.md).
"""
import hashlib
import importlib.util
import socket
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from launcher.supervisor import Service, Supervisor

try:
    import fcntl  # macOS & Linux
except ImportError:  # Windows: tanpa kunci satu-instans
    fcntl = None

# nama layanan -> (perintah manage.py, keterangan)
SERVICES = {
    "web": ("runserver", "web pages + WebSocket"),
    "mqtt": ("mqtt_bridge", "devices over MQTT"),
    "coap": ("coap_server", "devices over CoAP"),
    "rules": ("rule_engine", "rule engine worker"),
}
DEFAULT_ADDRPORT = "127.0.0.1:8000"


def parse_names(text):
    names = [n.strip() for n in (text or "").split(",") if n.strip()]
    unknown = [n for n in names if n not in SERVICES]
    if unknown:
        raise CommandError(f"Unknown service: {', '.join(unknown)}. Choose from: {', '.join(SERVICES)}.")
    return names


def split_addrport(addrport):
    if ":" in addrport:
        host, port = addrport.rsplit(":", 1)
    else:
        host, port = "127.0.0.1", addrport
    host = host.strip("[]") or "127.0.0.1"
    if not port.isdigit():
        raise CommandError(f"Invalid address:port {addrport!r}. Example: 127.0.0.1:8000")
    return host, int(port)


def port_in_use(host, port):
    """True bila port TCP sudah dipakai proses lain (mis. runserver yang masih jalan)."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return True
    return False


def installed(module):
    return importlib.util.find_spec(module) is not None


class Command(BaseCommand):
    help = "Run the web server, mqtt_bridge, coap_server and rule_engine together (development)."

    def add_arguments(self, parser):
        parser.add_argument("addrport", nargs="?", default=DEFAULT_ADDRPORT,
                            help=f"Address for the web server (default {DEFAULT_ADDRPORT}).")
        parser.add_argument("--only", help="Comma-separated services to run: " + ", ".join(SERVICES))
        parser.add_argument("--skip", help="Comma-separated services to leave out.")
        parser.add_argument("--no-restart", action="store_true", help="Do not restart services that exit.")

    def warn(self, text):
        self.stdout.write(self.style.WARNING(f"[runall] {text}"))

    def handle(self, *args, **options):
        only, skip = parse_names(options["only"]), parse_names(options["skip"])
        names = [n for n in SERVICES if (not only or n in only) and n not in skip]
        names = self.preflight(names, options["addrport"], explicit=set(only))
        if not names:
            raise CommandError("Nothing to run.")

        lock = self.single_instance_lock()  # noqa: F841 (dipegang sampai proses selesai)

        manage = str(Path(settings.BASE_DIR) / "manage.py")
        services = []
        for name in names:
            argv = [sys.executable, manage, SERVICES[name][0]]
            if name == "web":
                argv.append(options["addrport"])
            services.append(Service(name=name, argv=argv))

        self.stdout.write(self.style.SUCCESS("[runall] Starting: " + ", ".join(
            f"{n} ({SERVICES[n][1]})" for n in names)))
        host, port = split_addrport(options["addrport"])
        if "web" in names:
            shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
            self.stdout.write(f"[runall] Open http://{shown}:{port}/ · Press Ctrl+C to stop everything.")
        supervisor = Supervisor(services, out=self.stdout, restart=not options["no_restart"])
        code = supervisor.run()
        if code:
            sys.exit(code)

    # --- pemeriksaan sebelum mulai -------------------------------------------------

    def preflight(self, names, addrport, explicit):
        """Buang layanan yang pasti gagal, dengan alasan yang jelas."""
        result = []
        for name in names:
            reason = None
            if name == "web":
                host, port = split_addrport(addrport)
                if port_in_use(host, port):
                    raise CommandError(
                        f"Port {port} is already in use. Is runserver (or another runall) still running?"
                    )
                if host in ("0.0.0.0", "::"):
                    self.warn("The web server is reachable from other computers on this network. "
                              "runserver is for development only; keep DEBUG off for anything public.")
            elif name == "mqtt" and not installed("paho"):
                reason = "paho-mqtt is not installed (pip install paho-mqtt)"
            elif name == "coap" and not installed("aiocoap"):
                reason = "aiocoap is not installed (pip install aiocoap)"
            elif name == "rules" and settings.RULE_ENGINE_MODE == "off":
                reason = "RULE_ENGINE_MODE=off"
            if reason:
                if name in explicit:
                    raise CommandError(f"Cannot run {name}: {reason}.")
                self.warn(f"Skipping {name}: {reason}.")
                continue
            result.append(name)

        if "rules" in result and settings.RULE_ENGINE_MODE == "worker":
            try:
                from rules.queue import redis_client

                redis_client().ping()
            except Exception as exc:
                self.warn(f"Redis is not reachable ({exc}). The rule engine will keep retrying; "
                          "start Redis (brew services start redis).")
        if "coap" in result:
            self.warn("CoAP listens on UDP 5683 without encryption (coap://). Fine on a lab network; "
                      "do not forward this port to the internet.")
        return result

    def single_instance_lock(self):
        """Cegah dua runall bersamaan: dua mqtt_bridge akan saling berebut koneksi
        broker, dan port web/CoAP bentrok. Kunci dilepas otomatis saat proses selesai."""
        if fcntl is None:
            return None
        digest = hashlib.sha1(str(settings.BASE_DIR).encode()).hexdigest()[:10]
        path = Path(tempfile.gettempdir()) / f"iot-platform-runall-{digest}.lock"
        handle = open(path, "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise CommandError("runall is already running for this project (another terminal?).")
        return handle

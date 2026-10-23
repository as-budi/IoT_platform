"""
Perintah kustom:  python manage.py mqtt_accounts --passwd-file PATH --acl-file PATH

Menyusun akun broker Mosquitto SENDIRI (server produksi, docs/deploy-vps.md)
dari isi database:

    akun platform (mqtt_bridge)   username/password = MQTT_USERNAME / MQTT_PASSWORD (.env)
    satu akun per device AKTIF    username = password = access token device
    akun sandbox per pengguna     username sbx-<id>, password acak (tahap 23, bila
                                  MQTT_SANDBOX_ENABLED); hanya topik sandbox/...

lalu menulis dua file yang dibaca Mosquitto:

    password_file   daftar akun, password di-hash oleh `mosquitto_passwd -U`
    acl_file        siapa boleh membaca/menulis topik mana

Mengapa username = access token? Aturan ACL Mosquitto bisa memakai %u (username)
di pola topik, sehingga SATU baris berlaku untuk semua device:

    pattern write <prefix>/%u/telemetry

artinya: device hanya boleh menulis ke topik yang memuat token MILIKNYA sendiri.
Device A tidak bisa mengirim data atas nama device B, tidak bisa membaca command
device lain, dan tidak bisa berlangganan "#" untuk menyadap semuanya.

Device yang dinonaktifkan atau tokennya diganti otomatis kehilangan aksesnya
pada penyusunan berikutnya (timer systemd setiap 2 menit, lihat deploy/systemd/).

File hanya ditulis ulang bila daftar akun berubah, supaya Mosquitto tidak perlu
dimuat ulang tanpa alasan. Unit iot-mqtt-reload.path memantau file sidik jari
(*.sha256); bila berubah, iot-mqtt-reload.service (root) menyalin kedua file ke
/etc/mosquitto/iot dengan pemilik mosquitto lalu memuat ulang broker.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devices.models import Device
from mqtt_bridge.models import SHARED_ROOT, SandboxAccount


def sandbox_acl(sandboxes):
    """Aturan per akun sandbox: topik miliknya sendiri + area bersama."""
    if not sandboxes:
        return ""
    lines = ["", "# Akun sandbox pengguna (tahap 23): hanya di bawah sandbox/, tidak menyentuh topik device."]
    for account in sandboxes:
        lines += [f"user {account.username}", f"topic readwrite {account.topic_root}/#",
                  f"topic readwrite {SHARED_ROOT}/#"]
    return "\n".join(lines) + "\n"


def acl_text(prefix, bridge_user):
    """Aturan topik. Urutan arah sama dengan mqtt_bridge/topics.py."""
    return f"""# Dibuat oleh `python manage.py mqtt_accounts`. Jangan diubah manual.
# Aturan "user" berlaku untuk satu akun; aturan "pattern" untuk SEMUA akun,
# dengan %u diganti username (= access token device).

# Akun platform (mqtt_bridge): semua topik di bawah prefix.
user {bridge_user}
topic readwrite {prefix}/#

# Setiap device: hanya topik yang memuat tokennya sendiri.
pattern write {prefix}/%u/telemetry
pattern write {prefix}/%u/firmware
pattern write {prefix}/%u/status
pattern write {prefix}/%u/commands/+/response
pattern read  {prefix}/%u/desired
pattern read  {prefix}/%u/commands/+
pattern read  {prefix}/%u/errors
"""


class Command(BaseCommand):
    help = "Write Mosquitto password and ACL files: one account per active device, plus the platform account."

    def add_arguments(self, parser):
        parser.add_argument("--passwd-file", required=True, help="e.g. /srv/iot/mosquitto/passwd")
        parser.add_argument("--acl-file", required=True, help="e.g. /srv/iot/mosquitto/acl")
        parser.add_argument("--mosquitto-passwd", default="mosquitto_passwd",
                            help="Path of the mosquitto_passwd program (used to hash the passwords).")

    def handle(self, *args, **options):
        prefix = settings.MQTT_TOPIC_PREFIX
        bridge_user, bridge_password = settings.MQTT_USERNAME, settings.MQTT_PASSWORD
        if not bridge_user or not bridge_password:
            raise CommandError("Set MQTT_USERNAME and MQTT_PASSWORD in .env (the platform's own broker account).")
        if any(char in prefix for char in "+#") or not prefix:
            raise CommandError("MQTT_TOPIC_PREFIX must be set and must not contain + or #.")

        tokens = list(Device.objects.filter(is_active=True).order_by("pk").values_list("access_token", flat=True))
        if bridge_user in tokens:
            raise CommandError("MQTT_USERNAME must not equal a device access token.")
        sandboxes = []
        if settings.MQTT_SANDBOX_ENABLED:
            sandboxes = list(SandboxAccount.objects.filter(user__is_active=True).select_related("user").order_by("pk"))
        accounts = [(bridge_user, bridge_password)] + [(token, token) for token in tokens]
        accounts += [(account.username, account.password) for account in sandboxes]
        acl = acl_text(prefix, bridge_user) + sandbox_acl(sandboxes)

        passwd_path, acl_path = Path(options["passwd_file"]), Path(options["acl_file"])
        # Sidik jari isi (akun + ACL). Hash password memakai salt acak, jadi file
        # password yang sama isinya tetap berbeda byte-nya; yang dibandingkan adalah sidik jari ini.
        fingerprint = hashlib.sha256(repr((accounts, acl)).encode()).hexdigest()
        stamp = passwd_path.with_name(passwd_path.name + ".sha256")
        if passwd_path.exists() and acl_path.exists() and stamp.exists() and stamp.read_text().strip() == fingerprint:
            self.stdout.write(f"No changes ({len(tokens)} device account(s), {len(sandboxes)} sandbox account(s)).")
            return

        program = shutil.which(options["mosquitto_passwd"])
        if program is None:
            raise CommandError("mosquitto_passwd not found. Install it with: sudo apt install mosquitto")

        passwd_path.parent.mkdir(parents=True, exist_ok=True)
        # File sementara di folder yang SAMA, lalu os.replace: Mosquitto tidak
        # pernah membaca file yang setengah jadi.
        fd, temp_name = tempfile.mkstemp(dir=passwd_path.parent, prefix=".passwd-")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.writelines(f"{user}:{password}\n" for user, password in accounts)
            # -U: ubah semua password teks biasa di file menjadi hash.
            subprocess.run([program, "-U", temp_name], check=True, capture_output=True)
            os.chmod(temp_name, 0o640)
            os.replace(temp_name, passwd_path)
        except subprocess.CalledProcessError as exc:
            raise CommandError(f"mosquitto_passwd failed: {exc.stderr.decode(errors='replace').strip()}")
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

        fd, temp_name = tempfile.mkstemp(dir=acl_path.parent, prefix=".acl-")
        with os.fdopen(fd, "w") as handle:
            handle.write(acl)
        os.chmod(temp_name, 0o640)
        os.replace(temp_name, acl_path)
        stamp.write_text(fingerprint + "\n")
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {len(tokens)} device account(s), {len(sandboxes)} sandbox account(s) + platform account "
            f"'{bridge_user}' to {passwd_path}; ACL to {acl_path}."
        ))

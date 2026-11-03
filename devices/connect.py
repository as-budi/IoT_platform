"""
Tahap 24: panduan "How to connect real devices" dan template firmware ESP32.

Template firmware TIDAK ditulis ulang di sini. Sumbernya tetap sketch di folder
firmware/ (yang juga dipakai dan dijelaskan di docs/). Saat pengguna menekan tombol
unduh, sketch itu dibaca, beberapa baris pengaturannya diisi dengan nilai platform
ini (alamat server, port, prefix topik, access token device yang dipilih, sertifikat
root CA), lalu dikemas dalam file .zip yang langsung bisa dibuka Arduino IDE.

Setiap penggantian HARUS cocok tepat satu kali. Bila sketch di firmware/ diubah
sampai barisnya tidak dikenali lagi, unduhan gagal dengan pesan jelas (dan tes di
devices/tests_connect.py ikut gagal), bukan diam-diam menghasilkan sketch yang salah.
"""
import io
import ipaddress
import re
import zipfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

FIRMWARE_DIR = Path(settings.BASE_DIR) / "firmware"
ROOT_CA_FILE = FIRMWARE_DIR / "certs" / "isrg-root-x1.pem"
ROOT_CA_NAME = "ISRG Root X1"
ROOT_CA_SHA256 = (
    "96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6"
)
ROOT_CA_EXPIRES = "4 June 2035"

TOKEN_PLACEHOLDER = "paste-the-device-access-token-here"

# Satu entri per protokol. "sketch" = nama folder dan file .ino di firmware/.
PROTOCOLS = {
    "http": {
        "label": "HTTP",
        "sketch": "esp32_telemetry",
        "libraries": [],
        "summary": "Sends telemetry and its firmware/MAC report with HTTP POST requests.",
    },
    "mqtt": {
        "label": "MQTT",
        "sketch": "esp32_mqtt",
        "libraries": ["PubSubClient (Nick O'Leary)", "ArduinoJson 7 (Benoit Blanchon)"],
        "summary": "Two-way: telemetry, actuator states, commands, online/offline status and OTA updates.",
    },
    "coap": {
        "label": "CoAP",
        "sketch": "esp32_coap",
        "libraries": ["CoAP simple library (Hirotaka Niisato)", "ArduinoJson 7 (Benoit Blanchon)"],
        "summary": "Two-way over UDP: telemetry, actuator states and commands, firmware/MAC report.",
    },
}


class TemplateMismatch(Exception):
    """Baris pengaturan yang dicari tidak ditemukan tepat satu kali di sketch."""


def host_reach(host):
    """Siapa yang bisa menjangkau `host`: loopback, private, public, atau unknown (nama domain)."""
    bare = host.strip("[]")
    try:
        address = ipaddress.ip_address(bare)
    except ValueError:
        return "loopback" if bare == "localhost" else "unknown"
    return "loopback" if address.is_loopback else "private" if address.is_private else "public"


def http_base_url(request):
    """Alamat web server dilihat dari DEVICE, mis. http://192.168.1.10:8000 atau https://iot.domain.

    OTA_BASE_URL (.env) sudah berarti persis itu (alamat unduhan firmware untuk device);
    bila kosong, dipakai alamat di address bar browser.
    """
    return settings.OTA_BASE_URL or request.build_absolute_uri("/").rstrip("/")


def url_host(url):
    """Host dari sebuah URL, tanpa skema dan port."""
    rest = url.split("://", 1)[-1].split("/", 1)[0]
    if rest.startswith("["):
        return rest.split("]", 1)[0] + "]"
    return rest.rsplit(":", 1)[0] if rest.count(":") == 1 else rest


def c_string(value):
    """Teks aman untuk literal string C/C++ ("...")."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def read_root_ca():
    return ROOT_CA_FILE.read_text(encoding="ascii")


def server_values(request, coap_host):
    """Nilai platform ini untuk masing-masing protokol (sama untuk semua device)."""
    base_url = http_base_url(request)
    return {
        "http": {"SERVER": base_url},
        "mqtt": {
            "MQTT_HOST": settings.MQTT_PUBLIC_HOST,
            "MQTT_PORT": settings.MQTT_PUBLIC_PORT,
            "TOPIC_PREFIX": settings.MQTT_TOPIC_PREFIX,
            "USE_TLS": 1 if settings.MQTT_PUBLIC_TLS else 0,
        },
        "coap": {"SERVER_HOST": coap_host.strip("[]"), "SERVER_PORT": settings.COAP_PORT},
    }


# Pola baris pengaturan di sketch: grup 1 = sebelum nilai, 2 = nilai lama, 3 = sesudahnya.
def _string_setting(name):
    return re.compile(rf'^(const char\*\s+{name}\s*=\s*)("[^"\n]*")(;)', re.MULTILINE)


def _number_setting(name):
    return re.compile(rf"^(const\s+(?:int|uint16_t)\s+{name}\s*=\s*)(\d+)(;)", re.MULTILINE)


def _define_setting(name):
    return re.compile(rf"^(#define\s+{name}\s+)(\S+)()", re.MULTILINE)


ROOT_CA_BLOCK = re.compile(r'^(const char\* ROOT_CA = R"PEM\(\n)(.*?)(\)PEM";)', re.DOTALL | re.MULTILINE)


def _substitute(text, pattern, replacement, name):
    new_text, count = pattern.subn(lambda m: m.group(1) + replacement + m.group(3), text)
    if count != 1:
        raise TemplateMismatch(f"Setting {name} found {count} times in the firmware template (expected once).")
    return new_text


def render_sketch(protocol, values, token=None):
    """Isi sketch `protocol` dengan `values` (nama pengaturan -> nilai) dan token device."""
    info = PROTOCOLS[protocol]
    text = (FIRMWARE_DIR / info["sketch"] / f"{info['sketch']}.ino").read_text(encoding="utf-8")
    settings_to_fill = dict(values)
    if token:
        settings_to_fill["ACCESS_TOKEN"] = token
    for name, value in settings_to_fill.items():
        if name == "USE_TLS":
            text = _substitute(text, _define_setting(name), str(value), name)
        elif isinstance(value, int):
            text = _substitute(text, _number_setting(name), str(value), name)
        else:
            text = _substitute(text, _string_setting(name), f'"{c_string(value)}"', name)
    if "ROOT_CA" in text:
        text = _substitute(text, ROOT_CA_BLOCK, read_root_ca(), "ROOT_CA")
    return text


def build_firmware_zip(protocol, values, device=None):
    """Kembalikan (nama_file, isi_zip) untuk diunduh."""
    info = PROTOCOLS[protocol]
    sketch = info["sketch"]
    token = device.access_token if device else None
    source = render_sketch(protocol, values, token)
    header = (
        f"// Generated by IoT Platform on {timezone.localtime():%Y-%m-%d %H:%M} ({info['label']} template"
        f"{f', device: {device.name}' if device else ', no device selected'}).\n"
        "// Fill in WIFI_SSID and WIFI_PASSWORD"
        f"{'' if device else ' and ACCESS_TOKEN'} below, then upload to the ESP32.\n\n"
    )
    name = (slugify(device.name) or "device") if device else "template"
    folder = f"esp32-{protocol}-{name}"
    readme = render_readme(protocol, values, device)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # Arduino IDE mensyaratkan nama folder = nama file .ino.
        archive.writestr(f"{folder}/{sketch}/{sketch}.ino", header + source)
        archive.writestr(f"{folder}/README.txt", readme)
        archive.writestr(f"{folder}/isrg-root-x1.pem", read_root_ca())
    return f"{folder}.zip", buffer.getvalue()


def render_readme(protocol, values, device):
    info = PROTOCOLS[protocol]
    lines = [
        f"ESP32 firmware template - {info['label']}",
        "=" * 40,
        "",
        info["summary"],
        "",
        f"Device: {device.name}" if device else "Device: none selected (paste the access token yourself)",
        "Server settings already filled in:",
    ]
    lines += [f"  {name} = {value}" for name, value in values.items()]
    lines += [
        "",
        "Steps",
        f"1. Open {info['sketch']}/{info['sketch']}.ino in Arduino IDE 2.",
        "2. Tools > Board: your ESP32 board (package \"esp32\" by Espressif).",
        "3. Library Manager: " + (", ".join(info["libraries"]) if info["libraries"] else "no extra libraries needed."),
        "4. Fill in WIFI_SSID and WIFI_PASSWORD" + ("" if device else " and ACCESS_TOKEN") + ".",
        "5. Upload, open Serial Monitor at 115200 baud.",
        "6. On the device page, check 'Last data' and the Firmware card (version and MAC address).",
        "",
        "isrg-root-x1.pem is the root certificate (ISRG Root X1, Let's Encrypt) that the",
        "sketch uses to verify the server when TLS/https is on. It is already embedded",
        "in the sketch as ROOT_CA; the file is included for reference.",
        f"SHA-256 fingerprint: {ROOT_CA_SHA256}",
        "",
    ]
    return "\n".join(lines)

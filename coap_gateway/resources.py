"""
Penerjemah antara pustaka aiocoap dan coap_gateway/handlers.py.

aiocoap menyusun server sebagai pohon "resource" (Site). Semua alamat di bawah
/api/v1/ ditangani satu resource, ApiResource, karena alamatnya memuat token
yang berbeda-beda untuk setiap device:

    coap://server/api/v1/<token>/telemetry  -> handlers.handle("POST", [token, "telemetry"], ...)

Observe (RFC 7641):
    Device mengirim GET dengan opsi Observe. aiocoap memanggil add_observation();
    kita simpan "pengamat" itu per (jenis, token). Sebuah tugas latar belakang
    (watch) memeriksa database berkala; bila desired state atau daftar command
    berubah, pengamat dipicu (trigger) dan aiocoap mengirim isi terbaru ke device
    tanpa diminta.
"""
import asyncio
import ipaddress
import json
import logging
import os
import socket
import sys

import aiocoap
from aiocoap import resource
from asgiref.sync import sync_to_async
from django.db import close_old_connections

from . import handlers

logger = logging.getLogger(__name__)

try:  # aiocoap 0.4.x
    from aiocoap.numbers.contentformat import ContentFormat

    JSON_FORMAT = ContentFormat.JSON
except ImportError:  # versi lama: nomor format langsung (50 = application/json)
    JSON_FORMAT = 50

# "2.05" (dari handlers) -> kode aiocoap.
CODES = {
    handlers.CHANGED: aiocoap.CHANGED,
    handlers.CONTENT: aiocoap.CONTENT,
    handlers.BAD_REQUEST: aiocoap.BAD_REQUEST,
    handlers.UNAUTHORIZED: aiocoap.UNAUTHORIZED,
    handlers.FORBIDDEN: aiocoap.FORBIDDEN,
    handlers.NOT_FOUND: aiocoap.NOT_FOUND,
    handlers.METHOD_NOT_ALLOWED: aiocoap.METHOD_NOT_ALLOWED,
}


def run_db(function, *args):
    """Jalankan fungsi Django (sinkron) dari kode asyncio.

    ORM Django tidak boleh dipanggil langsung di event loop. sync_to_async
    menjalankannya di thread terpisah. close_old_connections() membuang koneksi
    MySQL yang sudah basi, karena server ini berjalan lama.
    """
    def wrapped():
        close_old_connections()
        try:
            return function(*args)
        finally:
            close_old_connections()

    return sync_to_async(wrapped, thread_sensitive=True)()


def to_message(reply):
    return aiocoap.Message(
        code=CODES[reply.code],
        payload=json.dumps(reply.body, separators=(",", ":")).encode(),
        content_format=JSON_FORMAT,
    )


class ApiResource(resource.ObservableResource, resource.PathCapable):
    """Menangani semua alamat di bawah /api/v1/ (lihat handlers.py).

    ObservableResource: aiocoap memanggil add_observation() untuk GET ber-Observe,
    lalu render() untuk setiap jawaban. PathCapable: Site meneruskan sisa alamat
    (token/telemetry, ...) ke resource ini apa adanya.
    """

    def __init__(self, on_request=None):
        super().__init__()
        self.on_request = on_request      # fungsi log opsional
        self.observers = {}               # (jenis, token) -> set(ServerObservation)
        self.signatures = {}              # (jenis, token) -> ringkasan isi terakhir

    async def needs_blockwise_assembly(self, request):
        # Biarkan aiocoap menyatukan payload besar yang dikirim per blok (RFC 7959).
        return True

    async def render(self, request):
        method = str(request.code)  # "GET", "POST", ...
        path = tuple(request.opt.uri_path)  # sudah tanpa "api", "v1"
        reply = await run_db(handlers.handle, method, path, request.payload)
        if self.on_request:
            self.on_request(method, path, reply, request.remote)
        return to_message(reply)

    async def add_observation(self, request, serverobservation):
        target = handlers.route(request.opt.uri_path)
        if target is None or target[0] not in handlers.OBSERVABLE:
            return  # tidak menerima Observe -> device mendapat jawaban biasa saja
        key = (target[0], target[1])
        if key not in self.signatures:
            self.signatures[key] = await run_db(handlers.observe_signature, *key)
        watchers = self.observers.setdefault(key, set())
        watchers.add(serverobservation)

        def cancelled():
            watchers.discard(serverobservation)
            if not watchers:
                self.observers.pop(key, None)
                self.signatures.pop(key, None)

        serverobservation.accept(cancelled)

    def observer_count(self):
        return sum(len(w) for w in self.observers.values())

    async def watch(self, interval):
        """Periksa berkala resource yang sedang di-Observe; kabari pengamat bila berubah."""
        while True:
            await asyncio.sleep(interval)
            for key in list(self.observers):
                try:
                    signature = await run_db(handlers.observe_signature, *key)
                except Exception:
                    logger.exception("Observe check failed for %s", key[0])
                    continue
                if signature == self.signatures.get(key):
                    continue
                self.signatures[key] = signature
                for observation in list(self.observers.get(key, ())):
                    # Tanpa argumen: aiocoap memanggil render() lagi dengan
                    # permintaan GET semula, lalu mengirim hasilnya ke device.
                    observation.trigger()


# --- Alamat yang didengarkan ---------------------------------------------------
#
# aiocoap memilih "transport" UDP sesuai sistem operasi:
#   - Linux: udp6, bisa mendengarkan SEMUA alamat ("::") sekaligus.
#   - macOS/Windows: simplesocketserver, yang TIDAK boleh memakai alamat "semua"
#     (0.0.0.0/::); ia harus diberi IP yang jelas. Karena itu kita membuka satu
#     server per alamat: 127.0.0.1 (uji di komputer sendiri) dan IP komputer di
#     jaringan (untuk ESP32).
# Hanya transport UDP yang dipakai: CoAP lewat TCP/TLS/WebSocket tidak dibuka.


def lan_ip():
    """IP komputer ini di jaringan lokal, atau None.

    connect() pada soket UDP tidak mengirim paket apa pun; sistem operasi hanya
    memilih antarmuka jaringan yang akan dipakai, dan IP-nya bisa dibaca.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # alamat dokumentasi, tidak pernah dihubungi
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith(("0.", "127.")) else address


def is_ip(text):
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def server_plan(bind="", public_host="", platform=None, detect=lan_ip):
    """Kembalikan (nama transport aiocoap, daftar alamat IP yang didengarkan)."""
    platform = platform or sys.platform
    hosts = [h.strip() for h in bind.split(",") if h.strip()]
    if platform.startswith("linux"):
        return "udp6", hosts or ["::"]
    if not hosts:
        hosts = ["127.0.0.1"]
        local = public_host if is_ip(public_host) else detect()
        if local and local not in hosts:
            hosts.append(local)
    return "simplesocketserver", hosts


async def start_servers(site, port, bind="", public_host=""):
    """Buka server CoAP untuk setiap alamat; kembalikan (daftar context, daftar alamat)."""
    import aiocoap

    transport, hosts = server_plan(bind, public_host)
    # Variabel lingkungan ini dikenal semua versi aiocoap 0.4.x.
    os.environ.setdefault("AIOCOAP_SERVER_TRANSPORT", transport)
    contexts, listening = [], []
    for host in hosts:
        try:
            contexts.append(await aiocoap.Context.create_server_context(site, bind=(host, port)))
            listening.append(host)
        except OSError as exc:
            logger.warning("Cannot listen on %s:%s (%s)", host, port, exc)
    return contexts, listening


def build_site(on_request=None):
    """Pohon resource server: /.well-known/core (penemuan) dan /api/v1/..."""
    site = resource.Site()
    api = ApiResource(on_request)
    site.add_resource(
        [".well-known", "core"], resource.WKCResource(site.get_resources_as_linkheader)
    )
    site.add_resource(["api", "v1"], api)
    return site, api

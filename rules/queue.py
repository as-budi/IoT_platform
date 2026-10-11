"""
Jalur pesan dari penyimpanan data ke rule engine.

    save_telemetry() ──publish()──► antrean Redis ──► worker `manage.py rule_engine`
                                     "rule-engine:messages"      (satu proses, berurutan)

Mengapa lewat antrean, bukan langsung dinilai di save_telemetry()?
- Penyimpanan data tetap cepat: aksi seperti mengirim email bisa makan detik.
- Satu worker memproses pesan BERURUTAN, sehingga pesan dari device yang sama
  tidak saling mendahului (penting untuk aturan "selama 60 detik").
- Worker juga yang menjalankan pemeriksaan berkala (tick): durasi dan
  "device diam", yang harus jalan walaupun tidak ada data baru.

Mode (RULE_ENGINE_MODE di .env):
    worker  pesan dimasukkan ke Redis; dinilai oleh worker (bawaan bila REDIS_URL diisi)
    inline  dinilai langsung di proses yang menyimpan data, setelah commit
            (bawaan tanpa Redis); worker tetap perlu untuk durasi & "device diam"
    off     rule engine dimatikan
"""
import json
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

QUEUE_KEY = "rule-engine:messages"
# Bila worker tidak berjalan, antrean dibatasi supaya Redis tidak penuh.
MAX_QUEUE_LENGTH = 10_000


def telemetry_message(device, rows, protocol=""):
    """Bentuk pesan seragam (lihat rules/engines/base.py). `rows` = (variable, value, ts)."""
    return {
        "type": "TELEMETRY",
        "originator": {"device": device.pk},
        "data": [
            {"variable": variable.pk, "key": variable.key, "value": value, "ts": ts.isoformat()}
            for variable, value, ts in rows
        ],
        "metadata": {"protocol": protocol, "received_at": timezone.now().isoformat()},
    }


def publish(message):
    """Serahkan pesan ke rule engine SETELAH transaksi penyimpanan berhasil."""
    mode = settings.RULE_ENGINE_MODE
    if mode == "off":
        return
    if mode == "inline":
        transaction.on_commit(lambda: process_safely(message))
    else:
        transaction.on_commit(lambda: push(message))


def process(message, now=None):
    """Teruskan pesan ke setiap model rule engine yang sudah diimplementasikan."""
    from .engines import implemented_engines

    now = now or timezone.now()
    executions = []
    for engine in implemented_engines():
        executions += engine.on_message(message, now) or []
    return executions


def process_safely(message):
    # Kesalahan rule engine tidak boleh membuat penyimpanan data gagal.
    try:
        process(message)
    except Exception:
        logger.exception("Rule engine failed to process a message")


# --- Redis ---------------------------------------------------------------------------

_client = None


def redis_client():
    """Koneksi Redis (redis-py, sudah terpasang bersama channels-redis)."""
    global _client
    if _client is None:
        import redis

        _client = redis.Redis.from_url(
            settings.REDIS_URL,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=5,
        )
    return _client


def push(message):
    try:
        client = redis_client()
        # RPUSH menambah di ujung kanan, worker mengambil dari kiri (BLPOP): FIFO.
        client.rpush(QUEUE_KEY, json.dumps(message))
        client.ltrim(QUEUE_KEY, -MAX_QUEUE_LENGTH, -1)
    except Exception:
        logger.warning("Could not queue a message for the rule engine", exc_info=True)


def pop(client, timeout):
    """Ambil satu pesan; tunggu paling lama `timeout` detik (BLPOP). None bila kosong."""
    item = client.blpop([QUEUE_KEY], timeout=timeout)
    if item is None:
        return None
    _key, raw = item
    return json.loads(raw)

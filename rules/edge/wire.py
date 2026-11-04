"""
Format frame ESP-NOW micro rule, versi 1 (lihat rencana tahap 26 bagian 6 dan 26b
bagian 3). Semua bilangan little-endian.

DATA (tipe 1), dipancarkan (broadcast) oleh device produsen, satu frame per device:

    magic "MR" (2) | versi (1) | tipe (1) | counter u32 (4) | n (1)
    n x [ hash key u16 (2) | nilai float32 (4) | umur u16, satuan 100 ms (2) ]
    tag (8)                                                   -> 17 + 8n byte

STATE (tipe 4), dipancarkan evaluator pada strategi SE:

    magic (2) | versi (1) | tipe (1) | counter u32 (4) | rule id u32 (4) | rev (4)
    keadaan u8 (1) | nomor transisi u16 (2) | umur transisi u16 (2) | tag (8) -> 29 byte

Tag = HMAC-SHA256 terpotong 8 byte; diisi nol sampai tahap 28.
"""
import struct

MAGIC = b"MR"
VERSION = 1
DATA, HELLO, EVENT, STATE = 1, 2, 3, 4
TAG_BYTES = 8
NO_TAG = bytes(TAG_BYTES)
AGE_UNIT_MS = 100
AGE_MAX = 0xFFFF

_HEAD = struct.Struct("<2sBBI")          # magic, versi, tipe, counter
_SIG = struct.Struct("<HfH")              # hash key, nilai, umur
_STATE_BODY = struct.Struct("<I4sBHH")    # rule, rev, keadaan, nomor transisi, umur

STATE_FRAME_BYTES = _HEAD.size + _STATE_BODY.size + TAG_BYTES


class WireError(ValueError):
    pass


def crc16(data):
    """CRC-16/CCITT-FALSE (poly 0x1021, awal 0xFFFF). crc16(b"123456789") == 0x29B1."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def key_hash(key):
    """Identitas sinyal di frame DATA: CRC-16 dari nama variabel (UTF-8)."""
    return crc16(key.encode("utf-8"))


def kh_text(value):
    return f"0x{value:04X}"


def data_frame_bytes(n):
    return _HEAD.size + 1 + _SIG.size * n + TAG_BYTES


def age_units(age_ms):
    # Dibulatkan setengah ke atas, seperti (age + 50) / 100 di C.
    return max(0, min(AGE_MAX, int((age_ms + AGE_UNIT_MS // 2) // AGE_UNIT_MS)))


def encode_data(counter, signals, tag=NO_TAG):
    """signals: [(hash key, nilai, umur ms), ...] -> bytes."""
    if len(signals) > 255:
        raise WireError("too many signals")
    body = b"".join(_SIG.pack(kh, value, age_units(age)) for kh, value, age in signals)
    return _HEAD.pack(MAGIC, VERSION, DATA, counter & 0xFFFFFFFF) + bytes([len(signals)]) + body + tag


def _head(frame, kind):
    if len(frame) < _HEAD.size + TAG_BYTES:
        raise WireError("frame too short")
    magic, version, frame_kind, counter = _HEAD.unpack_from(frame)
    if magic != MAGIC:
        raise WireError("bad magic")
    if version != VERSION:
        raise WireError("unsupported version")
    if frame_kind != kind:
        raise WireError("unexpected frame type")
    return counter


def frame_type(frame):
    if len(frame) < _HEAD.size or frame[:2] != MAGIC:
        return None
    return frame[3]


def decode_data(frame):
    """bytes -> {"counter", "signals": [(hash key, nilai, umur ms)], "tag"}."""
    counter = _head(frame, DATA)
    n = frame[_HEAD.size]
    if len(frame) != data_frame_bytes(n):
        raise WireError("length does not match signal count")
    signals = []
    offset = _HEAD.size + 1
    for _ in range(n):
        kh, value, age = _SIG.unpack_from(frame, offset)
        signals.append((kh, value, age * AGE_UNIT_MS))
        offset += _SIG.size
    return {"counter": counter, "signals": signals, "tag": frame[offset:]}


def rev_bytes(rev):
    """Rev 8 hex -> 4 byte."""
    return bytes.fromhex(rev)[:4]


def encode_state(counter, rule, rev, active, seq, age_ms, tag=NO_TAG):
    body = _STATE_BODY.pack(rule & 0xFFFFFFFF, rev_bytes(rev), 1 if active else 0, seq & 0xFFFF, age_units(age_ms))
    return _HEAD.pack(MAGIC, VERSION, STATE, counter & 0xFFFFFFFF) + body + tag


def decode_state(frame):
    counter = _head(frame, STATE)
    if len(frame) != STATE_FRAME_BYTES:
        raise WireError("bad STATE frame length")
    rule, rev, active, seq, age = _STATE_BODY.unpack_from(frame, _HEAD.size)
    return {
        "counter": counter, "rule": rule, "rev": rev.hex(), "active": bool(active),
        "seq": seq, "age_ms": age * AGE_UNIT_MS, "tag": frame[-TAG_BYTES:],
    }


def f32(value):
    """Bulatkan ke float32, seperti `float` di ESP32."""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]

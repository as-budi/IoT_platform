"""Membaca ukuran gambar JPEG tanpa library tambahan (tanpa Pillow).

Gambar JPEG terdiri atas segmen: FF D8 (awal), lalu FF <jenis> <panjang 2 byte> <isi> ...
Lebar dan tinggi ada di segmen SOF (Start Of Frame, FF C0..CF kecuali C4, C8, CC):
    FF Cx | panjang (2) | presisi (1) | tinggi (2) | lebar (2) | ...
"""
import struct

SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def is_jpeg(data):
    return len(data) > 4 and data[:3] == b"\xff\xd8\xff"


def dimensions(data):
    """(lebar, tinggi), atau (None, None) bila segmen SOF tidak ditemukan."""
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:                 # byte pengisi
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2                          # penanda tanpa panjang
            continue
        if marker in (0xD9, 0xDA):          # akhir gambar / awal data gambar: SOF sudah lewat
            break
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in SOF_MARKERS and i + 9 <= n:
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None, None

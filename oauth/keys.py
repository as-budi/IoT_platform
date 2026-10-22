"""Membuat kunci RSA untuk menandatangani id_token (dipakai `manage.py oidc_key`)."""
import os
from pathlib import Path

from jwcrypto import jwk


def generate_private_key_pem(bits=2048):
    """Kunci RSA privat baru dalam format PEM (teks)."""
    key = jwk.JWK.generate(kty="RSA", size=bits)
    return key.export_to_pem(private_key=True, password=None).decode()


def write_private_key(path, pem):
    """Tulis kunci dengan izin 600 (hanya pemilik yang bisa membaca), folder 700."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    # os.open dengan mode 600 SEJAK DIBUAT: tidak ada saat sesaat file bisa dibaca orang lain.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(pem)
    os.chmod(path, 0o600)


def public_key_id(pem):
    """Sidik jari kunci publik (kid), sama dengan yang tampil di /o/.well-known/jwks.json."""
    return jwk.JWK.from_pem(pem.encode()).thumbprint()

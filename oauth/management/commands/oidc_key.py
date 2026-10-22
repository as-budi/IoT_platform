"""
python manage.py oidc_key [--force]

Membuat kunci RSA untuk menandatangani id_token OpenID Connect (tahap 22), di
OIDC_RSA_PRIVATE_KEY_FILE (bawaan private_media/oidc/private.pem). Cukup SEKALI.

Mengganti kunci (--force) membuat id_token lama tidak bisa diverifikasi lagi; aplikasi
klien mengambil kunci publik baru dari /o/.well-known/jwks.json, jadi pengguna cukup
login ulang. Web server perlu di-restart supaya membaca kunci baru.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from oauth.keys import generate_private_key_pem, public_key_id, write_private_key


class Command(BaseCommand):
    help = "Create the RSA key that signs OpenID Connect id_tokens."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Replace an existing key.")

    def handle(self, *args, force=False, **options):
        path = settings.OIDC_RSA_PRIVATE_KEY_FILE
        if path.exists() and not force:
            raise CommandError(f"{path} already exists. Use --force to replace it.")
        pem = generate_private_key_pem()
        write_private_key(path, pem)
        self.stdout.write(self.style.SUCCESS(f"Key written to {path} (kid {public_key_id(pem)})."))
        self.stdout.write("Restart the web server so it loads the key.")

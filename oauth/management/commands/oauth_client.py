"""
python manage.py oauth_client "MQTT Client" --redirect-uri https://mqtt.domain/oidc/callback/
       [--logout-uri https://mqtt.domain/] [--rotate-secret]

Mendaftarkan (atau memperbarui) satu aplikasi klien OpenID Connect (tahap 22).

- Aplikasi baru: client_id dan client_secret dicetak SEKALI. Secret disimpan di
  database hanya sebagai hash, jadi tidak bisa dilihat lagi. Salin ke .env aplikasi
  klien saat itu juga.
- Aplikasi yang sudah ada (nama sama): alamat redirect diperbarui. Secret lama tetap,
  kecuali dengan --rotate-secret (secret lama langsung tidak berlaku).

Jenis aplikasi: confidential (punya server sendiri yang menyimpan secret), alur
authorization code + PKCE, id_token ditandatangani RS256.
"""
from django.core.management.base import BaseCommand, CommandError
from oauth2_provider.generators import generate_client_secret
from oauth2_provider.models import get_application_model


class Command(BaseCommand):
    help = "Register or update an OpenID Connect client application."

    def add_arguments(self, parser):
        parser.add_argument("name", help='Name shown to users, e.g. "MQTT Client".')
        parser.add_argument("--redirect-uri", action="append", required=True, dest="redirect_uris",
                            help="Exact callback URL of the client (repeat for more than one).")
        parser.add_argument("--logout-uri", action="append", default=[], dest="logout_uris",
                            help="Where users may be sent after logging out (repeatable).")
        parser.add_argument("--rotate-secret", action="store_true", help="Issue a new client secret.")

    def handle(self, name, redirect_uris, logout_uris, rotate_secret=False, **options):
        Application = get_application_model()
        for uri in redirect_uris + logout_uris:
            if not uri.startswith(("https://", "http://")):
                raise CommandError(f"Not an absolute URL: {uri}")

        application = Application.objects.filter(name=name).first()
        created = application is None
        if created:
            application = Application(
                name=name,
                client_type=Application.CLIENT_CONFIDENTIAL,
                authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
                algorithm=Application.RS256_ALGORITHM,
                skip_authorization=False,
            )
        application.redirect_uris = " ".join(redirect_uris)
        application.post_logout_redirect_uris = " ".join(logout_uris)

        secret = None
        if created or rotate_secret:
            # Secret dibuat di sini supaya teks aslinya bisa dicetak; save() menyimpannya sebagai hash.
            secret = generate_client_secret()
            application.client_secret = secret
        try:
            application.full_clean(exclude=["client_secret", "user"])
        except Exception as exc:
            raise CommandError(str(exc))
        application.save()

        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} application \"{name}\"."))
        self.stdout.write(f"client_id:     {application.client_id}")
        if secret:
            self.stdout.write(f"client_secret: {secret}")
            self.stdout.write(self.style.WARNING("Copy the secret now: it is stored only as a hash."))
        else:
            self.stdout.write("client_secret: (unchanged; use --rotate-secret for a new one)")

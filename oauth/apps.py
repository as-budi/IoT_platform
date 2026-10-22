"""
App "oauth" (tahap 22): platform sebagai PENYEDIA LOGIN untuk aplikasi lain.

Contohnya aplikasi MQTT client web (repo terpisah). Pengguna menekan "Sign in with
IoT Platform" di aplikasi itu, lalu login dan menyetujui izin di platform ini. Aplikasi
itu menerima token, BUKAN password pengguna. Protokolnya OAuth 2.0 + OpenID Connect,
dikerjakan paket django-oauth-toolkit (app "oauth2_provider").

App ini hanya menambahkan bagian yang khas platform:
    validators.py   isi id_token/userinfo (username, nama, email), tolak akun nonaktif
    views.py        halaman persetujuan, Profile -> Connected apps, cabut izin
    models.py       Consent: izin yang pernah diberikan (supaya tidak ditanya terus)
    auth.py         dekorator @oauth_required untuk API /api/v2/ (lihat api/v2.py)
    signals.py      akun dinonaktifkan -> semua token aplikasi luar dicabut
    checks.py       `check --deploy`: kunci RSA dan alamat issuer sudah diatur
"""
from django.apps import AppConfig


class OauthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "oauth"
    verbose_name = "Sign-in for other apps"

    def ready(self):
        from . import checks, signals  # noqa: F401  (mendaftarkan check dan sinyal)

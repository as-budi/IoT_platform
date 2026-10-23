"""
Pemeriksaan `python manage.py check --deploy` untuk login aplikasi lain (tahap 22).

Hanya dijalankan dengan --deploy (server produksi; juga oleh deploy/scripts/update.sh),
supaya laptop yang belum membuat kunci tetap bisa menjalankan runserver dan tes.
"""
from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=True)
def oidc_configuration(app_configs, **kwargs):
    errors = []
    key = settings.OAUTH2_PROVIDER.get("OIDC_RSA_PRIVATE_KEY", "")
    if "PRIVATE KEY" not in key:
        errors.append(checks.Error(
            f"The OpenID Connect signing key was not found at {settings.OIDC_RSA_PRIVATE_KEY_FILE}.",
            hint="Create it once with `python manage.py oidc_key` (see docs/deploy-vps.md, 14.3).",
            id="oauth.E001",
        ))
    issuer = settings.OIDC_ISSUER
    if not issuer.startswith("https://"):
        errors.append(checks.Error(
            "OIDC_ISSUER must be the https address of the OpenID Connect endpoints.",
            hint="Add OIDC_ISSUER=https://<your-domain>/o to .env.",
            id="oauth.E002",
        ))
    return errors

"""
Consent = izin yang pernah diberikan seorang pengguna kepada satu aplikasi luar.

django-oauth-toolkit sendiri tidak mengingat persetujuan; ia hanya melihat apakah
masih ada access token yang berlaku (umurnya 10 menit). Tanpa tabel ini pengguna
akan ditanya "Allow MQTT Client to ...?" hampir setiap kali login. Dengan tabel ini:

    pertama kali          halaman persetujuan -> Allow -> baris Consent dibuat
    login berikutnya      scope yang diminta sudah tercakup -> langsung lanjut
    scope baru diminta    ditanya lagi (mis. aplikasi kini juga minta email)
    Revoke di profil      baris dihapus + semua token aplikasi itu dicabut
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from oauth2_provider.settings import oauth2_settings


class Consent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="oauth_consents")
    application = models.ForeignKey(
        oauth2_settings.APPLICATION_MODEL, on_delete=models.CASCADE, related_name="consents"
    )
    # Scope yang disetujui, dipisah spasi, mis. "openid profile devices:read".
    scope = models.TextField("scopes")
    granted_at = models.DateTimeField("granted", default=timezone.now)
    # Terakhir kali aplikasi mendapat token baru untuk pengguna ini.
    last_used_at = models.DateTimeField("last used", null=True, blank=True)

    class Meta:
        ordering = ["application__name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "application"], name="oauth_consent_unique"),
        ]

    def __str__(self):
        return f"{self.user} -> {self.application}"

    @property
    def scopes(self):
        return self.scope.split()

    def covers(self, scopes):
        """True bila semua `scopes` yang diminta sudah pernah disetujui."""
        return set(scopes) <= set(self.scopes)

    def scope_descriptions(self):
        return [settings.OAUTH_SCOPES.get(name, name) for name in self.scopes]

"""
Sinyal app oauth.

- Akun dinonaktifkan (Admin, atau belum diaktivasi) -> cabut semua token aplikasi luar.
  Validator juga menolak akun nonaktif, tetapi mencabut token membuat aplikasi luar
  langsung kehilangan akses, termasuk ke endpoint bawaan seperti /o/userinfo/.
- Aplikasi mendapat token baru (login atau refresh) -> catat "last used" di Consent.
"""
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from oauth2_provider.signals import app_authorized

from .models import Consent


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def revoke_tokens_of_inactive_user(sender, instance, created, **kwargs):
    if created or instance.is_active:
        return
    from .services import revoke_tokens

    revoke_tokens(instance)


@receiver(app_authorized)
def remember_last_use(sender, request, token, **kwargs):
    if token.user_id is None:
        return
    Consent.objects.filter(user_id=token.user_id, application_id=token.application_id).update(
        last_used_at=timezone.now()
    )

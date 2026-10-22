"""Mencabut token aplikasi luar (dipakai halaman Connected apps dan sinyal akun nonaktif)."""
from django.db import transaction
from oauth2_provider.models import get_access_token_model, get_grant_model, get_id_token_model, get_refresh_token_model

from .models import Consent


@transaction.atomic
def revoke_tokens(user, application=None):
    """Cabut SEMUA token `user` (untuk satu `application`, atau semua aplikasi).

    Setelah ini aplikasi itu tidak bisa memanggil API maupun memperbarui token; pengguna
    harus login dan menyetujui lagi. Mengembalikan jumlah refresh token yang dicabut.
    """
    scope = {"user": user}
    if application is not None:
        scope["application"] = application
    refresh_tokens = list(get_refresh_token_model().objects.filter(revoked__isnull=True, **scope))
    for refresh_token in refresh_tokens:
        refresh_token.revoke()          # menandai dicabut + menghapus access token pasangannya
    get_access_token_model().objects.filter(**scope).delete()
    get_grant_model().objects.filter(**scope).delete()
    get_id_token_model().objects.filter(**scope).delete()
    return len(refresh_tokens)


def revoke_consent(user, application):
    """Tombol Revoke: hapus persetujuan dan cabut tokennya."""
    Consent.objects.filter(user=user, application=application).delete()
    return revoke_tokens(user, application)

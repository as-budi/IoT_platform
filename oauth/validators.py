"""
Validator = "otak" django-oauth-toolkit: memeriksa token dan menyusun isi id_token.

Kita memakai validator bawaan dengan tiga tambahan:

1. Klaim (claims) yang dikirim ke aplikasi klien, sesuai scope yang disetujui:

       scope "openid"   sub (id pengguna, tidak pernah berubah)
       scope "profile"  preferred_username, name, given_name, family_name
       scope "email"    email, email_verified

   Aplikasi klien sebaiknya mengenali pengguna dari `sub`, bukan username: username
   bisa diubah Admin.

2. Akun yang dinonaktifkan Admin langsung ditolak, walaupun tokennya belum kedaluwarsa
   (saat API dipanggil dan saat refresh token ditukar).
"""
from oauth2_provider.oauth2_validators import OAuth2Validator


class PlatformOAuth2Validator(OAuth2Validator):
    def get_additional_claims(self, request):
        user = request.user
        claims = {
            "preferred_username": user.username,
            "name": user.get_full_name() or user.username,
            "given_name": user.first_name,
            "family_name": user.last_name,
            "email": user.email,
            # Email akun platform selalu sudah dibuktikan lewat tautan (tahap 11).
            "email_verified": bool(user.email),
        }
        # Klaim kosong tidak dikirim (mis. pengguna tanpa nama belakang).
        return {key: value for key, value in claims.items() if value != ""}

    def validate_bearer_token(self, token, scopes, request):
        if not super().validate_bearer_token(token, scopes, request):
            return False
        return request.user is not None and request.user.is_active

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        if not super().validate_refresh_token(refresh_token, client, request, *args, **kwargs):
            return False
        return request.user is not None and request.user.is_active

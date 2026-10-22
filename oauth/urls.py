"""
Alamat OAuth 2.0 / OpenID Connect platform (tahap 22). Semua diawali /o/
(lihat config/urls.py). Namespace HARUS "oauth2_provider": django-oauth-toolkit
mencari alamatnya sendiri dengan nama itu (mis. untuk menyusun discovery).

    /o/authorize/                          login + persetujuan (oauth/views.py)
    /o/token/                              aplikasi klien menukar kode / refresh token
    /o/revoke_token/                       aplikasi klien mencabut token (mis. saat logout)
    /o/userinfo/                           profil pengguna menurut scope token
    /o/logout/                             logout dari aplikasi klien sekaligus dari platform
    /o/.well-known/openid-configuration    "discovery": daftar semua alamat di atas
    /o/.well-known/jwks.json               kunci PUBLIK untuk memeriksa tanda tangan id_token

Sengaja TIDAK diaktifkan: halaman kelola aplikasi untuk pengguna biasa, pendaftaran
klien dinamis, device flow, dan introspection. Aplikasi klien didaftarkan Admin dengan
`python manage.py oauth_client`.
"""
from django.urls import path, re_path
from oauth2_provider import views as dot

from . import views

app_name = "oauth2_provider"

urlpatterns = [
    path("authorize/", views.PlatformAuthorizationView.as_view(), name="authorize"),
    path("token/", dot.TokenView.as_view(), name="token"),
    path("revoke_token/", dot.RevokeTokenView.as_view(), name="revoke-token"),
    re_path(r"^\.well-known/openid-configuration/?$", dot.ConnectDiscoveryInfoView.as_view(),
            name="oidc-connect-discovery-info"),
    path(".well-known/jwks.json", dot.JwksInfoView.as_view(), name="jwks-info"),
    path("userinfo/", dot.UserInfoView.as_view(), name="user-info"),
    path("logout/", dot.RPInitiatedLogoutView.as_view(), name="rp-initiated-logout"),
]

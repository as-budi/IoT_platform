"""
@oauth_required(scope): pelindung API untuk APLIKASI LUAR (tahap 22), mis. /api/v2/.

Berbeda dengan halaman web (sesi + cookie) dan API device /api/v1/ (access token
device di URL), aplikasi luar mengirim access token OAuth di header:

    Authorization: Bearer <access token>

Hasil pemeriksaan:
    401 invalid_token       token tidak ada, salah, kedaluwarsa, dicabut, atau akun nonaktif
    403 insufficient_scope  token sah, tetapi pengguna tidak menyetujui scope itu
    lanjut                  request.oauth_user = pemilik token, request.oauth_token = token

Token TIDAK diterima lewat query string (?access_token=...): alamat URL tercatat di
log server dan riwayat browser (lihat COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT).
"""
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.oauth2_backends import get_oauthlib_core


def _error(status, error, description, scope=""):
    challenge = f'Bearer error="{error}", error_description="{description}"'
    if scope:
        challenge += f', scope="{scope}"'
    response = JsonResponse({"error": error, "error_description": description}, status=status)
    response["WWW-Authenticate"] = challenge
    return response


def oauth_required(*scopes):
    def decorator(view):
        @csrf_exempt   # tanpa cookie, jadi tanpa CSRF (sama seperti API device)
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            # Langkah 1: token sah? (scope kosong = cek keabsahan saja)
            valid, oauth_request = get_oauthlib_core().verify_request(request, scopes=[])
            if not valid:
                return _error(401, "invalid_token", "Missing, invalid or expired access token.")
            token = oauth_request.access_token
            # Langkah 2: scope cukup?
            if not token.allow_scopes(scopes):
                return _error(403, "insufficient_scope", "The token does not allow this request.",
                              scope=" ".join(scopes))
            request.oauth_user = oauth_request.user
            request.oauth_token = token
            return view(request, *args, **kwargs)

        return wrapper

    return decorator

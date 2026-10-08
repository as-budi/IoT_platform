"""
API untuk device (bukan untuk manusia di browser).

Perbedaan penting dengan view dashboard:

- Tidak ada login/sesi. Device dikenali dari ACCESS TOKEN di URL.
- Tanpa CSRF. Perlindungan CSRF ada untuk browser yang sudah login (cookie
  sesi dikirim otomatis, sehingga situs lain bisa "menumpang"). Device tidak
  memakai cookie; ia membuktikan identitasnya dengan token di setiap request.
  Karena itu view ini ditandai @csrf_exempt.
- Balasan berupa JSON dengan kode status HTTP yang bermakna, bukan halaman HTML.
"""
import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devices.models import Device

from .payload import PayloadError, parse_telemetry
from .services import save_telemetry


def error(status, message):
    return JsonResponse({"error": message}, status=status)


def reject_constant(name):
    # json.loads Python menerima NaN/Infinity, padahal itu bukan JSON standar.
    raise ValueError(f"{name} is not allowed")


@csrf_exempt
@require_POST  # selain POST -> 405 Method Not Allowed
def telemetry(request, token):
    """POST /api/v1/<token>/telemetry — device mengirim data sensor.

    Kode status balasan:
      200  tersimpan
      400  body bukan JSON yang valid, atau isinya tidak sesuai aturan
      401  token tidak dikenal
      403  device dinonaktifkan
      405  bukan POST
    """
    # 1) Autentikasi: cari device pemilik token.
    device = Device.objects.filter(access_token=token).first()
    if device is None:
        return error(401, "Invalid access token.")
    if not device.is_active:
        return error(403, "Device is inactive.")

    # 2) Baca body sebagai JSON.
    try:
        data = json.loads(request.body, parse_constant=reject_constant)
    except (ValueError, UnicodeDecodeError):
        return error(400, "Request body must be valid JSON.")

    # 3) Validasi isi payload (lihat api/payload.py).
    try:
        rows = parse_telemetry(data, now=timezone.now())
    except PayloadError as exc:
        message = str(exc)
        return error(400, message[:1].upper() + message[1:])

    # 4) Simpan.
    created_keys = save_telemetry(device, rows)
    return JsonResponse({"saved": len(rows), "new_variables": created_keys})

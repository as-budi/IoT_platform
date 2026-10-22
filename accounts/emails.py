"""
Mengirim email akun: tautan aktivasi (Sign up) dan konfirmasi email baru.

Isi email ada di template teks accounts/templates/accounts/email/. Tautannya
dibentuk dari alamat yang sedang dibuka pengguna (request.build_absolute_uri),
jadi otomatis https://domain-anda/... di server dan http://127.0.0.1:8000/...
di laptop. Nama host yang tidak ada di ALLOWED_HOSTS sudah ditolak Django
sebelum sampai ke sini, sehingga penyerang tidak bisa membuat tautan yang
mengarah ke situs palsu dengan memalsukan header Host.

Di laptop (EMAIL_HOST kosong) email dicetak di terminal yang menjalankan web
server: salin tautannya dari sana.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlencode, urlsafe_base64_encode

from .tokens import activation_token, email_change_token

logger = logging.getLogger(__name__)


def _link(request, url_name, user, token_generator):
    # uidb64 = id pengguna dalam base64 (aman ditaruh di URL), seperti reset password Django.
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    path = reverse(url_name, args=[uidb64, token_generator.make_token(user)])
    return request.build_absolute_uri(path)


def _send(template, recipient, context):
    """Kirim satu email. Mengembalikan False (dan mencatat log) bila gagal."""
    subject = render_to_string(f"accounts/email/{template}_subject.txt", context).strip()
    body = render_to_string(f"accounts/email/{template}_body.txt", context)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient])
    except Exception:  # SMTP mati, password salah, timeout, ...
        logger.exception("Could not send %s email to user", template)
        return False
    return True


def send_activation_email(request, user, next_url=""):
    """`next_url`: halaman tujuan setelah aktivasi (mis. kembali ke aplikasi lain, tahap 22)."""
    link = _link(request, "accounts:activate", user, activation_token)
    if next_url:
        link += "?" + urlencode({"next": next_url})
    context = {
        "user": user,
        "link": link,
        "hours": settings.PASSWORD_RESET_TIMEOUT // 3600,
    }
    return _send("activation", user.email, context)


def send_email_change_email(request, user):
    context = {
        "user": user,
        "link": _link(request, "accounts:email_confirm", user, email_change_token),
        "hours": settings.PASSWORD_RESET_TIMEOUT // 3600,
    }
    return _send("email_change", user.pending_email, context)

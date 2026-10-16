"""
Token untuk tautan di email: aktivasi akun dan konfirmasi perubahan email.

Token dibuat dengan mesin yang sama dengan tautan reset password Django
(PasswordResetTokenGenerator). Tokennya TIDAK disimpan di database:

    token = <timestamp>-<HMAC(SECRET_KEY + key_salt, nilai_hash + timestamp)>

Saat tautan diklik, Django menghitung ulang HMAC dari data pengguna SAAT INI.
Bila hasilnya sama dan umurnya belum lewat PASSWORD_RESET_TIMEOUT, token sah.

Akibatnya token otomatis "hangus" begitu data yang ikut di-hash berubah.
Itulah gunanya _make_hash_value() di bawah:
  - token aktivasi memuat activation_pending -> tidak bisa dipakai dua kali;
  - token email memuat pending_email         -> hangus bila email baru diganti
    lagi atau sudah dikonfirmasi.

key_salt yang berbeda memastikan token satu jenis tidak bisa dipakai untuk
jenis lain (misalnya token reset password sebagai token aktivasi).
"""
from django.contrib.auth.tokens import PasswordResetTokenGenerator


class AccountActivationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "accounts.tokens.AccountActivationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.is_active}{user.activation_pending}{user.email}{timestamp}"


class EmailChangeTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "accounts.tokens.EmailChangeTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{user.pending_email}{timestamp}"


activation_token = AccountActivationTokenGenerator()
email_change_token = EmailChangeTokenGenerator()

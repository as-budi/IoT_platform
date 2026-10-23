"""
Akun MQTT "sandbox" per pengguna (tahap 23).

Broker platform hanya menerima login dengan access token DEVICE, dan setiap device hanya
boleh memakai topiknya sendiri. Untuk belajar MQTT (subscribe, publish, wildcard) tanpa
membuat device, setiap pengguna bisa mengaktifkan satu akun sandbox:

    username   sbx-<id pengguna>         (tetap, walaupun username platform diganti)
    password   acak, bisa di-reset       (BUKAN password login platform)
    topik      sandbox/<username>/#      milik sendiri: baca + tulis
               sandbox/public/#          bersama semua pengguna sandbox: baca + tulis

Akun dan ACL-nya ditulis `manage.py mqtt_accounts` (timer 2 menit), sama seperti akun device.
Data di topik sandbox TIDAK masuk ke platform (mqtt_bridge hanya mendengar <prefix>/...).
"""
import re
import secrets

from django.conf import settings
from django.db import models

SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]+")
SHARED_ROOT = "sandbox/public"


def new_password():
    return secrets.token_urlsafe(18)


class SandboxAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mqtt_sandbox")
    password = models.CharField("password", max_length=64, default=new_password)
    created_at = models.DateTimeField("created", auto_now_add=True)
    password_changed_at = models.DateTimeField("password changed", auto_now_add=True)

    def __str__(self):
        return self.username

    @property
    def username(self):
        return f"sbx-{self.user_id}"

    @property
    def topic_root(self):
        """sandbox/<username platform>; username yang memuat karakter lain -> sandbox/~<id>.

        "~" tidak mungkin ada di username yang lolos SAFE_NAME, jadi sandbox/~7 tidak akan
        bertabrakan dengan pengguna lain (mis. pengguna bernama "user7").
        """
        name = self.user.username
        return f"sandbox/{name}" if SAFE_NAME.fullmatch(name) and name != "public" else f"sandbox/~{self.user_id}"

    def reset_password(self):
        from django.utils import timezone

        self.password = new_password()
        self.password_changed_at = timezone.now()
        self.save(update_fields=["password", "password_changed_at"])

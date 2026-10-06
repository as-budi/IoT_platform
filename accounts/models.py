"""
Model = representasi tabel database dalam bentuk class Python.

Django sudah punya model User lengkap (username, password ter-hash, email,
is_active, dst.). Kita mewarisinya lewat AbstractUser dan cukup menambah
satu kolom: `role`.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        # NAMA = "nilai yang disimpan di database", "label yang ditampilkan"
        ADMIN = "ADMIN", "Admin"
        USER = "USER", "User"

    role = models.CharField(
        "role",
        max_length=10,
        choices=Role.choices,
        default=Role.USER,
    )

    @property
    def is_admin(self):
        """True bila pengguna boleh mengelola seluruh platform.

        Superuser (dibuat lewat `createsuperuser`) selalu dianggap admin,
        supaya kita tidak terkunci dari aplikasi sendiri.
        """
        return self.is_superuser or self.role == self.Role.ADMIN

    def __str__(self):
        return self.get_full_name() or self.username

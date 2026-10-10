"""
Pengaturan dashboard milik setiap pengguna.

Yang disimpan adalah daftar yang DISEMBUNYIKAN, bukan yang ditampilkan.
Akibatnya device atau variabel baru otomatis muncul di dashboard tanpa
pengguna harus mengaturnya lebih dulu.

Aturan tampil: sebuah variabel tampil bila variabel itu, device-nya, DAN
lokasi device-nya tidak disembunyikan.
"""
from django.conf import settings
from django.db import models


class DashboardPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dashboard_preference",
        verbose_name="user",
    )
    # related_name="+": tidak perlu relasi balik dari Location/Device/Variable.
    hidden_locations = models.ManyToManyField(
        "devices.Location", blank=True, related_name="+", verbose_name="hidden locations"
    )
    hidden_devices = models.ManyToManyField(
        "devices.Device", blank=True, related_name="+", verbose_name="hidden devices"
    )
    hidden_variables = models.ManyToManyField(
        "devices.Variable", blank=True, related_name="+", verbose_name="hidden variables"
    )
    # Kelompok "No location" tidak punya baris Location, jadi memakai saklar sendiri.
    hide_unlocated = models.BooleanField("hide devices without a location", default=False)
    updated_at = models.DateTimeField("updated", auto_now=True)

    class Meta:
        verbose_name = "dashboard preference"
        verbose_name_plural = "dashboard preferences"

    def __str__(self):
        return f"Dashboard of {self.user}"

    @classmethod
    def for_user(cls, user):
        """Ambil pengaturan pengguna; buat baru (semua tampil) bila belum ada."""
        preference, _ = cls.objects.get_or_create(user=user)
        return preference

    def hidden_ids(self):
        """{"locations": set(id), "devices": set(id), "variables": set(id)}"""
        return {
            "locations": set(self.hidden_locations.values_list("pk", flat=True)),
            "devices": set(self.hidden_devices.values_list("pk", flat=True)),
            "variables": set(self.hidden_variables.values_list("pk", flat=True)),
        }

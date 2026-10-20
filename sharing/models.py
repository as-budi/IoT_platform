"""
Berbagi device antar-pengguna dan grup (tahap 19, docs/tahap-19-berbagi-device.md).

    ShareGroup        sekumpulan pengguna, mis. "Kelompok 3" atau "Teknisi lab".
                      Dibuat dan dikelola satu pengguna (owner); owner otomatis anggota.
      ShareGroupMember  satu baris = satu anggota grup.

    DeviceShare       "device D dibagikan ke X dengan peran R".
                      X adalah SATU pengguna atau SATU grup (tidak keduanya).

Peran (dari yang paling sempit):

    VIEWER    melihat data, chart, alarm, riwayat; memakai sensornya di kondisi aturan
    OPERATOR  + mengontrol aktuator, mengirim command, acknowledge/clear alarm,
                dan memakai device sebagai TARGET aksi aturan
    EDITOR    + mengubah pengaturan device, variabel, access token, firmware

Menghapus device dan mengatur siapa saja yang mendapat akses tetap hak PEMILIK
(dan Admin). Pemeriksaan hak ada di satu tempat: sharing/access.py.
"""
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Q


class ShareGroup(models.Model):
    name = models.CharField("name", max_length=80)
    description = models.CharField("description", max_length=200, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="owner", on_delete=models.CASCADE, related_name="owned_share_groups",
    )
    created_at = models.DateTimeField("created", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "sharing group"
        verbose_name_plural = "sharing groups"
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="sharegroup_unique_name_per_owner"),
        ]

    def __str__(self):
        return f"{self.name} ({self.owner.username})"


class ShareGroupMember(models.Model):
    group = models.ForeignKey(ShareGroup, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="share_memberships")
    added_at = models.DateTimeField("added", auto_now_add=True)

    class Meta:
        ordering = ["user__username"]
        verbose_name = "group member"
        verbose_name_plural = "group members"
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="sharegroupmember_unique")]

    def __str__(self):
        return f"{self.user.username} in {self.group.name}"


class Role(models.TextChoices):
    VIEWER = "VIEWER", "Viewer"
    OPERATOR = "OPERATOR", "Operator"
    EDITOR = "EDITOR", "Editor"


ROLE_HELP = {
    Role.VIEWER: "See data, charts, alarms and history. Use its sensors in rule conditions.",
    Role.OPERATOR: "Viewer + control actuators, send commands, handle alarms, use it as a rule action target.",
    Role.EDITOR: "Operator + change the device settings, variables, access token and firmware.",
}


class DeviceShare(models.Model):
    Role = Role

    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="shares")
    # Tepat salah satu dari user / group yang terisi. "Tidak keduanya" dijaga database
    # (CheckConstraint di bawah); "tidak kosong" dijaga ShareForm dan clean().
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="device_shares",
    )
    group = models.ForeignKey(ShareGroup, null=True, blank=True, on_delete=models.CASCADE, related_name="device_shares")
    role = models.CharField("role", max_length=10, choices=Role.choices, default=Role.VIEWER)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField("shared", auto_now_add=True)

    class Meta:
        ordering = ["group__name", "user__username"]
        verbose_name = "device share"
        verbose_name_plural = "device shares"
        constraints = [
            # Database ikut menjaga: satu baris tidak boleh untuk pengguna DAN grup sekaligus.
            #
            # Mengapa tidak "tepat satu" (user XOR group)? Saat grup atau pengguna dihapus,
            # Django di MySQL lebih dulu menjalankan UPDATE ... SET group_id = NULL pada
            # share-nya (FK nullable + CASCADE, karena MySQL tidak bisa menunda pemeriksaan
            # constraint), BARU kemudian DELETE. Sesaat baris itu kosong kedua-duanya, dan
            # constraint XOR menolak UPDATE tersebut (error 3819). SQLite menunda
            # pemeriksaan, sehingga masalah ini hanya muncul di MySQL.
            models.CheckConstraint(
                condition=Q(user__isnull=True) | Q(group__isnull=True),
                name="deviceshare_not_user_and_group",
            ),
            # NULL tidak dianggap sama oleh UNIQUE (MySQL, SQLite, PostgreSQL), jadi dua
            # constraint biasa ini cukup: satu baris per (device, user) dan per (device, group).
            models.UniqueConstraint(fields=["device", "user"], name="deviceshare_unique_user"),
            models.UniqueConstraint(fields=["device", "group"], name="deviceshare_unique_group"),
        ]

    def __str__(self):
        return f"{self.device} → {self.target_name} ({self.role})"

    def clean(self):
        if bool(self.user_id) == bool(self.group_id):
            raise ValidationError("A share is for one user or one group.")

    @property
    def target_name(self):
        return f"group {self.group.name}" if self.group_id else self.user.username

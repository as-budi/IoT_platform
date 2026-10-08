"""
Situs admin untuk device, variabel, dan telemetri.

Selama jalur komunikasi dengan device belum ada, /admin/ adalah cara paling
cepat untuk memasukkan data telemetri secara manual saat mencoba-coba.
"""
from django.contrib import admin

from .models import Device, Telemetry, Variable


class VariableInline(admin.TabularInline):
    """Daftar variabel ditampilkan (dan bisa diubah) di halaman device."""

    model = Variable
    extra = 0
    fields = ("key", "label", "unit")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "device_type", "location", "owner", "is_active")
    list_filter = ("is_active", "device_type")
    search_fields = ("name", "location", "owner__username")
    inlines = [VariableInline]


@admin.register(Variable)
class VariableAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "unit", "device")
    list_filter = ("device",)
    search_fields = ("key", "label", "device__name")


@admin.register(Telemetry)
class TelemetryAdmin(admin.ModelAdmin):
    list_display = ("ts", "variable", "value")
    list_filter = ("variable__device", "variable__key")
    date_hierarchy = "ts"
    # Menampilkan daftar variabel sebagai kotak pencarian, bukan dropdown
    # raksasa, supaya tetap cepat walau variabelnya banyak.
    raw_id_fields = ("variable",)

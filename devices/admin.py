"""
Situs admin untuk device, variabel, dan telemetri.

Selama jalur komunikasi dengan device belum ada, /admin/ adalah cara paling
cepat untuk memasukkan data telemetri secara manual saat mencoba-coba.
"""
from django.contrib import admin

from .models import Command, DesiredStateLog, Device, Location, Telemetry, Variable


class VariableInline(admin.TabularInline):
    """Daftar variabel ditampilkan (dan bisa diubah) di halaman device."""

    model = Variable
    extra = 0
    fields = ("key", "label", "unit", "kind", "value_type")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "device_type", "location", "owner", "is_active")
    list_filter = ("is_active", "location", "device_type")
    search_fields = ("name", "location__name", "owner__username")
    inlines = [VariableInline]
    # Token hanya ditampilkan, tidak bisa diketik manual di admin.
    readonly_fields = ("access_token",)


@admin.register(Variable)
class VariableAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "unit", "kind", "value_type", "desired_value", "device")
    list_filter = ("kind", "value_type", "device")
    search_fields = ("key", "label", "device__name")


@admin.register(Telemetry)
class TelemetryAdmin(admin.ModelAdmin):
    list_display = ("ts", "variable", "value")
    list_filter = ("variable__device", "variable__key")
    date_hierarchy = "ts"
    # Menampilkan daftar variabel sebagai kotak pencarian, bukan dropdown
    # raksasa, supaya tetap cepat walau variabelnya banyak.
    raw_id_fields = ("variable",)


@admin.register(DesiredStateLog)
class DesiredStateLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "variable", "value", "version", "changed_by")
    list_filter = ("variable__device",)


@admin.register(Command)
class CommandAdmin(admin.ModelAdmin):
    list_display = ("created_at", "device", "method", "status", "created_by", "expires_at")
    list_filter = ("status", "device")
    search_fields = ("method", "device__name")


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "latitude", "longitude")
    search_fields = ("name",)

"""
Situs admin untuk device & telemetri.

Karena komunikasi dengan device belum dibuat, /admin/ adalah cara paling
cepat untuk memasukkan data telemetri secara manual saat mencoba-coba.
"""
from django.contrib import admin

from .models import Device, Telemetry


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "variable", "unit", "device_type", "owner", "is_active")
    list_filter = ("is_active", "device_type")
    search_fields = ("name", "variable", "owner__username")


@admin.register(Telemetry)
class TelemetryAdmin(admin.ModelAdmin):
    list_display = ("ts", "device", "value")
    list_filter = ("device",)
    date_hierarchy = "ts"

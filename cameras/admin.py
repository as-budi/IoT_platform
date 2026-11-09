from django.contrib import admin

from .models import Camera, Snapshot


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("device", "name", "retention_days", "created_at")


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    list_display = ("camera", "ts", "size", "width", "height", "source")
    list_filter = ("source",)

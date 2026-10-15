from django.contrib import admin

from .models import FirmwarePackage, OtaEvent, OtaUpdate


@admin.register(FirmwarePackage)
class FirmwarePackageAdmin(admin.ModelAdmin):
    list_display = ("title", "version", "board", "size", "md5", "uploaded_by", "created_at")
    list_filter = ("board",)
    readonly_fields = ("size", "md5", "sha256")


class OtaEventInline(admin.TabularInline):
    model = OtaEvent
    extra = 0


@admin.register(OtaUpdate)
class OtaUpdateAdmin(admin.ModelAdmin):
    list_display = ("device", "package", "status", "progress", "created_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("download_token",)
    inlines = [OtaEventInline]

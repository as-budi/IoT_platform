"""Admin: daftar persetujuan (Consent). Aplikasi dan token tampil di bagian
"Django OAuth Toolkit" (admin bawaan paketnya)."""
from django.contrib import admin

from .models import Consent


@admin.register(Consent)
class ConsentAdmin(admin.ModelAdmin):
    list_display = ("user", "application", "scope", "granted_at", "last_used_at")
    list_filter = ("application",)
    search_fields = ("user__username", "application__name")
    readonly_fields = ("granted_at", "last_used_at")

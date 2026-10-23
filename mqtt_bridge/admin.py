from django.contrib import admin

from .models import SandboxAccount


@admin.register(SandboxAccount)
class SandboxAccountAdmin(admin.ModelAdmin):
    list_display = ("username", "user", "created_at", "password_changed_at")
    search_fields = ("user__username",)
    readonly_fields = ("created_at", "password_changed_at")
    exclude = ("password",)          # password tidak ditampilkan di admin

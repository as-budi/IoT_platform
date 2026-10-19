from django.contrib import admin

from .models import TelegramLink


@admin.register(TelegramLink)
class TelegramLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "chat_name", "chat_id", "linked_at")

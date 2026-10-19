from django.contrib import admin

from .models import ActivityEvent


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ("at", "level", "category", "verb", "message", "actor_name", "owner")
    list_filter = ("category", "level")
    search_fields = ("message", "device_name", "rule_name", "actor_name")
    raw_id_fields = ("owner", "actor", "device", "rule")

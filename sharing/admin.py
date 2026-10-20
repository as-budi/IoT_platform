from django.contrib import admin

from .models import DeviceShare, ShareGroup, ShareGroupMember


class MemberInline(admin.TabularInline):
    model = ShareGroupMember
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(ShareGroup)
class ShareGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    search_fields = ("name", "owner__username")
    inlines = [MemberInline]


@admin.register(DeviceShare)
class DeviceShareAdmin(admin.ModelAdmin):
    list_display = ("device", "user", "group", "role", "created_by", "created_at")
    list_filter = ("role",)
    search_fields = ("device__name", "user__username", "group__name")

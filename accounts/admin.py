"""
Mendaftarkan model ke situs admin bawaan Django (/admin/).

Kita memakai UserAdmin bawaan (sudah mengurus hashing password dsb.) dan
hanya menambahkan kolom `role` ke tampilannya.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (*BaseUserAdmin.list_display, "role")
    list_filter = (*BaseUserAdmin.list_filter, "role")
    fieldsets = (*BaseUserAdmin.fieldsets, ("Platform role", {"fields": ("role",)}))
    add_fieldsets = (
        *BaseUserAdmin.add_fieldsets,
        ("Platform role", {"fields": ("role",)}),
    )

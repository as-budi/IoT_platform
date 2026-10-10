from django.contrib import admin

from .models import DashboardPreference


@admin.register(DashboardPreference)
class DashboardPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "hide_unlocated", "updated_at")
    filter_horizontal = ("hidden_locations", "hidden_devices", "hidden_variables")

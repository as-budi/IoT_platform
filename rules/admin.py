from django.contrib import admin

from .models import Alarm, EcaSpec, Rule, RuleAction, RuleExecution, RuleState


class EcaSpecInline(admin.StackedInline):
    model = EcaSpec
    can_delete = False


class RuleActionInline(admin.TabularInline):
    model = RuleAction
    extra = 0


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "model", "is_enabled", "updated_at")
    list_filter = ("model", "is_enabled")
    search_fields = ("name",)
    inlines = [EcaSpecInline, RuleActionInline]


@admin.register(RuleState)
class RuleStateAdmin(admin.ModelAdmin):
    list_display = ("rule", "status", "last_value", "last_evaluated_at", "last_activated_at")


@admin.register(RuleExecution)
class RuleExecutionAdmin(admin.ModelAdmin):
    list_display = ("rule", "transition", "value", "ok", "created_at")
    list_filter = ("transition", "ok")


@admin.register(Alarm)
class AlarmAdmin(admin.ModelAdmin):
    list_display = ("message", "device", "severity", "created_at", "acknowledged_at", "cleared_at")
    list_filter = ("severity",)

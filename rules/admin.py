from django.contrib import admin

from .models import (
    Alarm, EcaCondition, EcaSpec, FlowLink, FlowNode, FlowSpec, MachineInstance, MachineState, MachineTransition,
    Rule, RuleAction, RuleExecution, RuleSchedule, RuleState,
)


class EcaSpecInline(admin.StackedInline):
    model = EcaSpec
    can_delete = False


class RuleScheduleInline(admin.StackedInline):
    model = RuleSchedule
    can_delete = False
    readonly_fields = ("next_run_at", "last_run_at")


class RuleActionInline(admin.TabularInline):
    model = RuleAction
    extra = 0


class FlowSpecInline(admin.StackedInline):
    model = FlowSpec
    can_delete = False


class FlowNodeInline(admin.TabularInline):
    model = FlowNode
    extra = 0
    fields = ("name", "kind", "key", "operator", "threshold", "seconds", "order")


class FlowLinkInline(admin.TabularInline):
    model = FlowLink
    extra = 0
    fk_name = "rule"


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "model", "is_enabled", "updated_at")
    list_filter = ("model", "is_enabled")
    search_fields = ("name",)
    inlines = [EcaSpecInline, RuleScheduleInline, RuleActionInline, FlowSpecInline, FlowNodeInline, FlowLinkInline]


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


@admin.register(EcaCondition)
class EcaConditionAdmin(admin.ModelAdmin):
    list_display = ("spec", "role", "group", "variable", "operator", "threshold")
    list_filter = ("role",)


# --- State machine (tahap 20) ---------------------------------------------------------

@admin.register(MachineState)
class MachineStateAdmin(admin.ModelAdmin):
    list_display = ("name", "rule", "tone", "is_initial", "order")
    list_filter = ("tone",)


@admin.register(MachineTransition)
class MachineTransitionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "rule", "trigger", "seconds", "label", "order")
    list_filter = ("trigger",)


@admin.register(MachineInstance)
class MachineInstanceAdmin(admin.ModelAdmin):
    list_display = ("rule", "device", "state", "entered_at", "last_evaluated_at")

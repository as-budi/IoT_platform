"""
Halaman rule engine: daftar aturan, pilih model, form ECA, detail, dan alarm.

Kepemilikan mengikuti device: pengguna biasa melihat aturan miliknya sendiri,
Admin melihat semua. Pilihan device/variabel di form dibatasi ke
visible_devices(pemilik aturan).
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import DeleteView, ListView, TemplateView, View

from devices.views import redirect_back, visible_devices

from .engines import ENGINES
from .forms import ActionFormSet, EcaSpecForm, RuleForm
from .models import Alarm, Rule, RuleModel, RuleState

EXECUTIONS_PAGE_SIZE = 25


def visible_rules(user):
    queryset = Rule.objects.select_related(
        "owner", "state", "eca", "eca__variable__device", "eca__device"
    )
    if user.is_admin:
        return queryset
    return queryset.filter(owner=user)


def visible_alarms(user):
    return Alarm.objects.select_related("device", "rule", "acknowledged_by", "cleared_by").filter(
        device__in=visible_devices(user)
    )


def reset_state(rule):
    """Kembalikan aturan ke IDLE (tanpa menjalankan aksi), mis. setelah diubah."""
    RuleState.objects.update_or_create(
        rule=rule, defaults={"status": RuleState.Status.IDLE, "pending_since": None}
    )


# --- Aturan --------------------------------------------------------------------------

class RuleListView(LoginRequiredMixin, ListView):
    template_name = "rules/rule_list.html"
    context_object_name = "rules"

    def get_queryset(self):
        return visible_rules(self.request.user).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for rule in context["rules"]:
            rule.summary = rule.engine.describe(rule) if rule.engine.implemented else None
        context["engines"] = ENGINES
        return context


class RuleModelPickerView(LoginRequiredMixin, TemplateView):
    """Langkah 1 membuat aturan: pilih model rule engine."""

    template_name = "rules/model_picker.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["engines"] = list(ENGINES.values())
        return context


class EcaRuleFormView(LoginRequiredMixin, View):
    """Langkah 2: form aturan ECA. Dipakai untuk membuat dan mengubah aturan."""

    template_name = "rules/rule_form_eca.html"

    def get_rule(self):
        pk = self.kwargs.get("pk")
        if pk is None:
            return None
        rule = get_object_or_404(visible_rules(self.request.user), pk=pk)
        if rule.model != RuleModel.ECA:
            raise Http404("Only ECA rules can be edited in this stage.")
        return rule

    def build_forms(self, rule, data=None):
        # Pilihan device mengikuti PEMILIK aturan (Admin yang mengedit aturan
        # milik orang lain tetap terbatas pada device pemiliknya).
        owner = rule.owner if rule else self.request.user
        devices = visible_devices(owner).order_by("name")
        spec = getattr(rule, "eca", None) if rule else None
        return (
            RuleForm(data, instance=rule, prefix="rule"),
            EcaSpecForm(data, instance=spec, devices=devices, prefix="eca"),
            ActionFormSet(data, instance=rule or Rule(), prefix="actions", form_kwargs={"devices": devices}),
        )

    def render_forms(self, rule, forms):
        rule_form, spec_form, action_formset = forms
        return render(self.request, self.template_name, {
            "rule": rule,
            "rule_form": rule_form,
            "spec_form": spec_form,
            "action_formset": action_formset,
            "engine": ENGINES[RuleModel.ECA],
        })

    def get(self, request, pk=None):
        rule = self.get_rule()
        return self.render_forms(rule, self.build_forms(rule))

    def post(self, request, pk=None):
        rule = self.get_rule()
        forms = self.build_forms(rule, request.POST)
        rule_form, spec_form, action_formset = forms
        if not (rule_form.is_valid() and spec_form.is_valid() and action_formset.is_valid()):
            return self.render_forms(rule, forms)

        # Ketiga bagian disimpan dalam satu transaksi: semuanya atau tidak sama sekali.
        with transaction.atomic():
            saved = rule_form.save(commit=False)
            if rule is None:
                saved.owner = request.user
                saved.model = RuleModel.ECA
            saved.save()
            spec = spec_form.save(commit=False)
            spec.rule = saved
            spec.save()
            action_formset.instance = saved
            actions = action_formset.save(commit=False)
            for removed in action_formset.deleted_objects:
                removed.delete()
            for order, action in enumerate(actions):
                action.order = order
                action.save()
            # Kondisi mungkin berubah: mulai lagi dari IDLE.
            reset_state(saved)

        messages.success(request, f"Rule \"{saved.name}\" saved.")
        return redirect("rules:detail", pk=saved.pk)


def eca_create_or_planned(request, model):
    """/rules/new/<model>/: ECA ke form; model lain belum tersedia."""
    if model == RuleModel.ECA.lower():
        return EcaRuleFormView.as_view()(request)
    try:
        engine = ENGINES[model.upper()]
    except KeyError:
        raise Http404("Unknown rule model.")
    messages.info(request, f"The {engine.label} model is planned for a later stage and cannot be used yet.")
    return redirect("rules:new")


class RuleDetailView(LoginRequiredMixin, TemplateView):
    template_name = "rules/rule_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rule = get_object_or_404(visible_rules(self.request.user), pk=self.kwargs["pk"])
        executions = Paginator(rule.executions.all(), EXECUTIONS_PAGE_SIZE).get_page(self.request.GET.get("page"))
        context.update(
            rule=rule,
            state=getattr(rule, "state", None),
            summary=rule.engine.describe(rule) if rule.engine.implemented else None,
            actions=rule.actions.select_related("target_variable__device", "target_device"),
            executions=executions,
            alarms=rule.alarms.select_related("device")[:10],
        )
        return context


class RuleToggleView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        rule = get_object_or_404(visible_rules(request.user), pk=pk)
        rule.is_enabled = not rule.is_enabled
        rule.save(update_fields=["is_enabled", "updated_at"])
        reset_state(rule)
        messages.success(request, f"Rule \"{rule.name}\" {'enabled' if rule.is_enabled else 'disabled'}.")
        return redirect_back(request, reverse("rules:detail", args=[rule.pk]))


class RuleDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "rules/rule_confirm_delete.html"
    context_object_name = "rule"
    success_url = reverse_lazy("rules:list")

    def get_queryset(self):
        return visible_rules(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, f"Rule \"{self.object.name}\" deleted.")
        return super().form_valid(form)


# --- Alarm ------------------------------------------------------------------------------

class AlarmListView(LoginRequiredMixin, ListView):
    template_name = "rules/alarm_list.html"
    context_object_name = "alarms"
    paginate_by = 50

    def get_queryset(self):
        alarms = visible_alarms(self.request.user)
        if self.request.GET.get("show") != "all":
            alarms = alarms.active()
        return alarms

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["show_all"] = self.request.GET.get("show") == "all"
        return context


class AlarmActionView(LoginRequiredMixin, View):
    """POST /rules/alarms/<pk>/acknowledge/ atau /clear/."""

    http_method_names = ["post"]
    action = None

    def post(self, request, pk):
        alarm = get_object_or_404(visible_alarms(request.user), pk=pk)
        if self.action == "acknowledge":
            done = alarm.acknowledge(request.user)
            text = "acknowledged"
        else:
            done = alarm.clear(request.user)
            text = "cleared"
        if done:
            messages.success(request, f"Alarm \"{alarm.message}\" {text}.")
        return redirect_back(request, reverse("rules:alarms"))



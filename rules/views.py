"""
Halaman rule engine: daftar aturan, pilih model, form ECA, detail, dan alarm.

Kepemilikan mengikuti device: pengguna biasa melihat aturan miliknya sendiri,
Admin melihat semua. Pilihan device/variabel di form dibatasi ke
visible_devices(pemilik aturan).
"""
import hashlib

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import DeleteView, ListView, TemplateView, View

from devices.views import redirect_back, visible_devices
from realtime.routing import DASHBOARD_WS_PATH, device_ws_path

from .engines import ENGINES
from .events import originator_device_id
from .forms import ActionFormSet, ClearFormSet, EcaSpecForm, RuleForm, WhenFormSet, cross_check
from .models import Alarm, EcaSpec, Rule, RuleExecution, RuleModel, RuleState

EXECUTIONS_PAGE_SIZE = 25


def visible_rules(user):
    queryset = Rule.objects.select_related(
        "owner", "state", "eca", "eca__device"
    ).prefetch_related("eca__conditions__variable__device")
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


# --- Pembaruan langsung ---------------------------------------------------------------
#
# Halaman aturan dan alarm memakai mekanisme yang sama dengan halaman device
# (static/js/auto_refresh.js): kabar WebSocket sebagai jalur utama, dan "versi
# data" yang diperiksa berkala sebagai cadangan. Versi = teks pendek yang
# berubah bila ada sesuatu yang perlu ditampilkan ulang.

def stamp(value):
    return int(value.timestamp()) if value else 0


def alarms_version(alarms):
    agg = alarms.aggregate(
        last=Max("pk"), active=Count("pk", filter=Q(cleared_at__isnull=True)),
        acked=Max("acknowledged_at"), cleared=Max("cleared_at"),
    )
    return f"{agg['last'] or 0}.{agg['active']}.{stamp(agg['acked'])}.{stamp(agg['cleared'])}"


def rule_version(rule):
    state = RuleState.objects.filter(rule=rule).first()
    last_execution = rule.executions.aggregate(last=Max("pk"))["last"] or 0
    return "-".join([
        state.status if state else "IDLE",
        str(int(rule.is_enabled)),
        str(last_execution),
        alarms_version(rule.alarms.all()),
    ])


def rules_version(user):
    rows = visible_rules(user).values_list("pk", "is_enabled", "state__status").order_by("pk")
    last_execution = RuleExecution.objects.filter(rule__in=visible_rules(user)).aggregate(
        last=Max("pk"))["last"] or 0
    # hashlib (bukan hash()): hasilnya sama di setiap proses Python.
    digest = hashlib.md5(repr(list(rows)).encode()).hexdigest()[:10]
    return f"{digest}-{last_execution}"


def updates(request, kind, pk=None):
    """GET /rules/updates/, /rules/<pk>/updates/, /rules/alarms/updates/ -> {"version": "..."}."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not logged in."}, status=401)
    if kind == "rule":
        version = rule_version(get_object_or_404(visible_rules(request.user), pk=pk))
    elif kind == "alarms":
        version = alarms_version(visible_alarms(request.user))
    else:
        version = rules_version(request.user)
    return JsonResponse({"version": version})


def live_context(ws_path, updates_url, version):
    return {"ws_path": ws_path, "updates_url": updates_url, "data_version": version}


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
        context.update(live_context(
            DASHBOARD_WS_PATH, reverse("rules:updates"), rules_version(self.request.user)
        ))
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
        spec_instance = spec or EcaSpec()
        kwargs = {"form_kwargs": {"devices": devices}}
        return {
            "rule_form": RuleForm(data, instance=rule, prefix="rule"),
            "spec_form": EcaSpecForm(data, instance=spec, devices=devices, prefix="eca"),
            "when_formset": WhenFormSet(data, instance=spec_instance, prefix="when", **kwargs),
            "clear_formset": ClearFormSet(data, instance=spec_instance, prefix="clear", **kwargs),
            "action_formset": ActionFormSet(data, instance=rule or Rule(), prefix="actions", **kwargs),
        }

    def render_forms(self, rule, forms, cross_errors=()):
        return render(self.request, self.template_name, {
            "rule": rule,
            **forms,
            "cross_errors": cross_errors,
            "engine": ENGINES[RuleModel.ECA],
        })

    def get(self, request, pk=None):
        rule = self.get_rule()
        return self.render_forms(rule, self.build_forms(rule))

    def post(self, request, pk=None):
        rule = self.get_rule()
        forms = self.build_forms(rule, request.POST)
        # all() akan berhenti di form pertama yang gagal; daftar ini memastikan
        # SEMUA form divalidasi supaya semua pesan error tampil sekaligus.
        valid = [form.is_valid() for form in forms.values()]
        if not all(valid):
            return self.render_forms(rule, forms)
        event = forms["spec_form"].cleaned_data["event"]
        cross_errors = cross_check(event, forms["when_formset"], forms["clear_formset"])
        if cross_errors:
            return self.render_forms(rule, forms, cross_errors)

        # Semua bagian disimpan dalam satu transaksi: semuanya atau tidak sama sekali.
        with transaction.atomic():
            saved = forms["rule_form"].save(commit=False)
            if rule is None:
                saved.owner = request.user
                saved.model = RuleModel.ECA
            saved.save()
            spec = forms["spec_form"].save(commit=False)
            spec.rule = saved
            spec.save()
            if event == EcaSpec.Event.TELEMETRY:
                save_conditions(forms["when_formset"], spec)
                save_conditions(forms["clear_formset"], spec)
            else:
                spec.conditions.all().delete()  # event "device diam" tidak memakai kondisi
            action_formset = forms["action_formset"]
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


def save_conditions(formset, spec):
    """Simpan kondisi satu peran, lalu rapikan nomor grup menjadi 1, 2, 3, ...

    Nomor grup dari halaman bisa "berlubang" (mis. 1, 4) setelah sebuah grup
    dihapus; yang penting hanya kondisi mana yang segrup.
    """
    formset.instance = spec
    formset.save(commit=False)
    for removed in formset.deleted_objects:
        removed.delete()
    live = [f for f in formset.live_forms() if f.cleaned_data.get("variable")]
    numbers = sorted({f.cleaned_data.get("group") or 1 for f in live})
    renumber = {number: index for index, number in enumerate(numbers, start=1)}
    position = {}
    for form in live:
        condition = form.instance
        condition.spec = spec
        condition.role = formset.role
        condition.group = renumber[form.cleaned_data.get("group") or 1]
        condition.order = position.setdefault(condition.group, 0)
        position[condition.group] += 1
        condition.save()


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


def evaluation_context(rule):
    """Data untuk kotak "Last evaluation" (rules/includes/evaluation.html)."""
    state = RuleState.objects.filter(rule=rule).first()
    snapshot = state.snapshot if state else {}
    spec = getattr(rule, "eca", None)
    return {
        "rule": rule,
        "state": state,
        "evaluation_parts": [
            {"title": "Activate when", "data": snapshot.get("when", {})},
            {"title": "Clear when", "data": snapshot.get("clear", {})},
        ],
        "has_clear_groups": bool(spec and spec.has_hysteresis),
        "max_age": spec.max_age_seconds if spec else None,
    }


def rule_evaluation(request, pk):
    """GET /rules/<pk>/evaluation/ -> potongan HTML kotak "Last evaluation".

    Dipanggil JavaScript halaman detail setiap ada kabar "rule_eval", sehingga
    tabel nilai dan hasil kondisi ikut berubah tanpa memuat ulang halaman.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not logged in."}, status=401)
    rule = get_object_or_404(visible_rules(request.user), pk=pk)
    return render(request, "rules/includes/evaluation.html", evaluation_context(rule))


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
            **{k: v for k, v in evaluation_context(rule).items() if k not in ("rule", "state")},
        )
        # Halaman berlangganan kabar dari device asal aturan (lihat rules/events.py).
        device_id = originator_device_id(rule)
        context.update(live_context(
            device_ws_path(device_id) if device_id else DASHBOARD_WS_PATH,
            reverse("rules:detail_updates", args=[rule.pk]),
            rule_version(rule),
        ))
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
        context.update(live_context(
            DASHBOARD_WS_PATH, reverse("rules:alarms_updates"), alarms_version(visible_alarms(self.request.user))
        ))
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



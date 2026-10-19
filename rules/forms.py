"""
Form untuk membuat aturan ECA (tahap 7) dan rule chain (tahap 8, di bagian bawah file).

Satu halaman berisi beberapa bagian yang disimpan bersamaan:
    RuleForm           nama, deskripsi, aktif
    EcaSpecForm        event, durasi, cooldown, batas umur data
    WhenFormSet        kondisi aktif, dikelompokkan: OR antar-grup, AND di dalam grup
    ClearFormSet       kondisi clear (opsional, untuk histeresis), bentuk yang sama
    ActionFormSet      satu atau lebih aksi (formset = kumpulan form sejenis)
    RuleScheduleForm   jadwal (tahap 14), hanya untuk event "Schedule" dan rule chain
                       dengan Input "On a schedule"

Semua pilihan device/variabel dibatasi ke device yang boleh dilihat pemilik
aturan (visible_devices), sehingga pengguna tidak bisa memilih device orang lain
walaupun mengubah isi form di browser.
"""
import json
import re

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from accounts.forms import BootstrapFormMixin
from devices.models import Variable

from .models import (
    Alarm, EcaCondition, EcaSpec, FlowLink, FlowNode, FlowSpec, MathOp, Operator, Rule, RuleAction,
    RuleSchedule,
)
from .schedules import DAY_LABELS, format_times, parse_times

# Id chat Telegram: angka (negatif untuk grup) atau @nama_channel (tahap 17).
TELEGRAM_CHAT = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")


def clean_telegram_chats(form, cleaned):
    """Periksa isian "recipients" untuk Telegram: daftar id chat dipisah koma."""
    chats = [c.strip() for c in (cleaned.get("recipients") or "").split(",") if c.strip()]
    wrong = [c for c in chats if not TELEGRAM_CHAT.match(c)]
    if wrong:
        form.add_error("recipients", f"Not a Telegram chat id: {', '.join(wrong)}. Use numbers like "
                                     "123456789 (or -100… for groups), or leave blank for yourself.")
    cleaned["recipients"] = ", ".join(chats)


LOW = {Operator.LT, Operator.LTE}
HIGH = {Operator.GT, Operator.GTE}


class VariableChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, variable):
        unit = f" ({variable.unit})" if variable.unit else ""
        return f"{variable.device.name} · {variable.display_name}{unit}"


class RuleForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Rule
        fields = ("name", "description", "is_enabled")
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class EcaSpecForm(BootstrapFormMixin, forms.ModelForm):
    """Bagian ECA selain kondisi: event, durasi, cooldown, batas umur data."""

    class Meta:
        model = EcaSpec
        fields = (
            "event",
            "device",
            "silence_seconds",
            "duration_seconds",
            "cooldown_seconds",
            "max_age_seconds",
        )
        widgets = {"event": forms.RadioSelect}

    def __init__(self, *args, devices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = devices
        self.fields["device"].empty_label = "— Choose a device —"
        # RadioSelect tidak cocok dengan class "form-select" dari BootstrapFormMixin.
        self.fields["event"].widget.attrs["class"] = "form-check-input"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("event") == EcaSpec.Event.INACTIVITY:
            if not cleaned.get("device"):
                self.add_error("device", "Required for this event.")
            if not cleaned.get("silence_seconds"):
                self.add_error("silence_seconds", "Enter how many seconds without data count as silent.")
            cleaned["duration_seconds"] = 0
        elif cleaned.get("event") == EcaSpec.Event.SCHEDULE:
            # Jadwal sudah menentukan KAPAN; "selama N detik" dan cooldown tidak
            # bermakna (aturan dinilai sekali pada setiap waktu jadwal).
            cleaned["device"] = None
            cleaned["silence_seconds"] = None
            cleaned["duration_seconds"] = 0
            cleaned["cooldown_seconds"] = 0
        else:
            cleaned["device"] = None
            cleaned["silence_seconds"] = None
        return cleaned


class ConditionForm(BootstrapFormMixin, forms.ModelForm):
    """Satu baris kondisi: <variabel> <operator> <ambang>, dengan nomor grupnya.

    Nomor grup (field tersembunyi) diisi JavaScript halaman: baris dengan nomor
    yang sama digabung AND, grup yang berbeda digabung OR.
    """

    class Meta:
        model = EcaCondition
        fields = ("group", "variable", "operator", "threshold")
        field_classes = {"variable": VariableChoiceField}
        widgets = {"group": forms.HiddenInput(attrs={"data-group-input": ""})}

    def __init__(self, *args, devices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["variable"].queryset = (
            Variable.objects.filter(device__in=devices).select_related("device").order_by("device__name", "key")
        )
        self.fields["variable"].empty_label = "— Variable —"
        self.fields["operator"].choices = Operator.choices
        self.fields["threshold"].widget.attrs["placeholder"] = "value"

    def has_changed(self):
        # Baris baru yang variabel dan ambangnya belum diisi dianggap kosong dan
        # dilewati (operator dan nomor grup selalu terisi oleh halaman).
        if self.instance.pk:
            return super().has_changed()
        return bool({"variable", "threshold"} & set(self.changed_data))


class BaseConditionFormSet(forms.BaseInlineFormSet):
    """Kumpulan kondisi untuk satu peran (WHEN atau CLEAR) dari satu aturan."""

    role = None

    def get_queryset(self):
        return super().get_queryset().filter(role=self.role)

    def live_forms(self):
        """Form yang berisi kondisi dan tidak dihapus."""
        return [
            form for form in self.forms
            if form.has_changed() or form.instance.pk
            if not (self.can_delete and self._should_delete_form(form))
        ]

    def condition_count(self):
        return len([f for f in self.live_forms() if f.cleaned_data.get("variable")])


def condition_formset(role):
    """Formset kondisi untuk satu peran. Prefix-nya "when" atau "clear"."""
    formset = inlineformset_factory(
        EcaSpec,
        EcaCondition,
        form=ConditionForm,
        formset=BaseConditionFormSet,
        extra=0,
        can_delete=True,
        max_num=20,
        validate_max=True,
    )
    return type(f"{role.title()}ConditionFormSet", (formset,), {"role": role})


WhenFormSet = condition_formset(EcaCondition.Role.WHEN)
ClearFormSet = condition_formset(EcaCondition.Role.CLEAR)


def cross_check(event, when_formset, clear_formset):
    """Pemeriksaan yang melibatkan kedua kumpulan kondisi. Mengembalikan daftar pesan error."""
    if event != EcaSpec.Event.TELEMETRY:
        return []
    errors = []
    when = [f.cleaned_data for f in when_formset.live_forms() if f.cleaned_data.get("variable")]
    clear = [f.cleaned_data for f in clear_formset.live_forms() if f.cleaned_data.get("variable")]
    if not when:
        errors.append("Add at least one condition.")
    # Histeresis satu variabel: kondisi clear tidak boleh tumpang tindih dengan
    # kondisi aktif. Contoh salah: aktif bila < 20, clear bila > 15 (nilai 17
    # memenuhi keduanya). Untuk ekspresi gabungan, mesin aturan sendiri menjaga
    # agar aturan tidak aktif selama kondisi clear juga benar (lihat engines/eca.py).
    if len(when) == 1 and len(clear) == 1 and when[0]["variable"] == clear[0]["variable"]:
        op, threshold = when[0]["operator"], when[0]["threshold"]
        clear_op, clear_threshold = clear[0]["operator"], clear[0]["threshold"]
        if (op in LOW and clear_op in LOW) or (op in HIGH and clear_op in HIGH):
            errors.append("The clear condition must point the other way (e.g. activate below, clear above).")
        elif op in LOW and clear_op in HIGH and clear_threshold < threshold:
            errors.append(f"Clear threshold must be at least {threshold:g}, otherwise both conditions can hold at once.")
        elif op in HIGH and clear_op in LOW and clear_threshold > threshold:
            errors.append(f"Clear threshold must be at most {threshold:g}, otherwise both conditions can hold at once.")
    return errors


class RuleActionForm(BootstrapFormMixin, forms.ModelForm):
    # Parameter command diketik sebagai teks JSON lalu diubah ke dict.
    params = forms.CharField(
        label="Parameters", required=False,
        widget=forms.TextInput(attrs={"placeholder": '{"times": 3}'}),
        help_text="Optional JSON object.",
    )
    severity = forms.ChoiceField(label="Severity", choices=Alarm.Severity.choices, required=False)

    class Meta:
        model = RuleAction
        fields = (
            "kind", "run_on", "target_variable", "value", "target_device", "method",
            "params", "ttl_seconds", "severity", "message", "recipients",
        )
        field_classes = {"target_variable": VariableChoiceField}
        widgets = {
            "message": forms.TextInput(attrs={"placeholder": "{device}: {variable} = {value}"}),
            "method": forms.TextInput(attrs={"placeholder": "e.g. blink"}),
        }
        help_texts = {
            "message": "Placeholders: {rule} {device} {variable} {value} {threshold} {values}.",
        }

    def __init__(self, *args, devices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_variable"].queryset = (
            Variable.objects.filter(device__in=devices, kind=Variable.Kind.ACTUATOR)
            .select_related("device").order_by("device__name", "key")
        )
        self.fields["target_device"].queryset = devices
        self.fields["target_variable"].empty_label = "— Choose an actuator —"
        self.fields["target_device"].empty_label = "— Choose a device —"
        self.fields["kind"].widget.attrs["data-action-kind"] = ""
        if self.instance.pk and self.instance.params:
            self.initial["params"] = json.dumps(self.instance.params)
        if not self.instance.pk:
            self.initial.setdefault("kind", RuleAction.Kind.SET_ACTUATOR)
        if not self.initial.get("severity"):
            self.initial["severity"] = Alarm.Severity.MAJOR

    def clean_params(self):
        raw = (self.cleaned_data.get("params") or "").strip()
        if not raw:
            return {}
        try:
            params = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Not valid JSON: {exc.msg}.")
        if not isinstance(params, dict):
            raise forms.ValidationError('Parameters must be a JSON object, e.g. {"times": 3}.')
        return params

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        kind = cleaned.get("kind")
        Kind = RuleAction.Kind

        def require(*names):
            for name in names:
                if cleaned.get(name) in (None, ""):
                    self.add_error(name, "Required for this action.")

        if kind == Kind.SET_ACTUATOR:
            require("target_variable", "value")
            variable, value = cleaned.get("target_variable"), cleaned.get("value")
            if variable and value is not None:
                if variable.is_boolean and value not in (0, 1):
                    self.add_error("value", "On/off actuators accept 1 (ON) or 0 (OFF).")
                elif variable.min_value is not None and value < variable.min_value:
                    self.add_error("value", f"Must be at least {variable.min_value:g}.")
                elif variable.max_value is not None and value > variable.max_value:
                    self.add_error("value", f"Must be at most {variable.max_value:g}.")
        elif kind == Kind.SEND_COMMAND:
            require("target_device", "method")
        elif kind == Kind.TELEGRAM:
            clean_telegram_chats(self, cleaned)
        elif kind == Kind.ALARM:
            require("severity")
            # Alarm dibuat saat aktif dan di-clear otomatis saat aturan pulih.
            cleaned["run_on"] = RuleAction.RunOn.ACTIVATE

        # Kosongkan field milik jenis aksi lain, supaya data tersimpan rapi.
        keep = {
            Kind.SET_ACTUATOR: {"target_variable", "value"},
            Kind.SEND_COMMAND: {"target_device", "method", "params", "ttl_seconds"},
            Kind.ALARM: {"severity", "message"},
            Kind.EMAIL: {"message", "recipients"},
            Kind.TELEGRAM: {"message", "recipients"},
        }.get(kind, set())
        blanks = {
            "target_variable": None, "value": None, "target_device": None, "method": "",
            "params": {}, "severity": "", "message": "", "recipients": "",
        }
        for name, blank in blanks.items():
            if name not in keep:
                cleaned[name] = blank
        return cleaned


ActionFormSet = inlineformset_factory(
    Rule,
    RuleAction,
    form=RuleActionForm,
    extra=0,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


# --- Jadwal (tahap 14) -------------------------------------------------------------------

class RuleScheduleForm(BootstrapFormMixin, forms.ModelForm):
    """Kapan aturan berjalan. Dipakai form ECA (event "Schedule") dan form rule chain
    (Input "On a schedule"); prefix-nya "schedule".

        At set times     times = "06:00, 18:30" + hari
        Every N minutes  interval_minutes = 15 + hari (dihitung dari 00:00)
        Once             run_at = tanggal dan jam

    with_active_for=False (rule chain): field "keep active for" tidak ada, karena
    rule chain tidak punya keadaan ACTIVE.
    """

    weekdays = forms.MultipleChoiceField(
        label="On", required=False,
        choices=[(str(i), label) for i, label in enumerate(DAY_LABELS)],
        widget=forms.CheckboxSelectMultiple,
    )
    run_at = forms.DateTimeField(
        label="On (date and time)", required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )

    class Meta:
        model = RuleSchedule
        fields = ("kind", "times", "weekdays", "interval_minutes", "run_at", "active_for_seconds")
        widgets = {
            "kind": forms.RadioSelect,
            "times": forms.TextInput(attrs={"placeholder": "06:00, 18:30"}),
        }
        help_texts = {
            "times": "24-hour clock, separated by commas.",
            "interval_minutes": "Counted from midnight: 15 → 00:00, 00:15, 00:30, …",
            "active_for_seconds": "Run the “on clear” actions this long after activating "
                                  "(e.g. pump ON, then OFF after 600 s). 0 = activate actions only.",
        }

    def __init__(self, *args, with_active_for=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].widget.attrs["class"] = "form-check-input"
        self.fields["kind"].widget.attrs["data-schedule-kind"] = ""
        self.fields["weekdays"].widget.attrs["class"] = "form-check-input"
        self.fields["interval_minutes"].widget.attrs.update(min=1, max=1440)
        self.fields["active_for_seconds"].widget.attrs["min"] = 0
        if not with_active_for:
            del self.fields["active_for_seconds"]
        self.initial["weekdays"] = list(self.instance.weekdays or "0123456")
        if self.instance.pk and self.instance.run_at:
            self.initial["run_at"] = timezone.localtime(self.instance.run_at)

    def clean_times(self):
        try:
            times = parse_times(self.cleaned_data.get("times"))
        except ValueError as exc:
            raise forms.ValidationError(str(exc))
        if len(times) > 24:
            raise forms.ValidationError("At most 24 times a day.")
        return format_times(times)

    def clean_weekdays(self):
        return "".join(sorted(set(self.cleaned_data.get("weekdays") or [])))

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        Kind = RuleSchedule.Kind
        if kind in (Kind.DAILY, Kind.INTERVAL) and "weekdays" in cleaned and not cleaned["weekdays"]:
            self.add_error("weekdays", "Choose at least one day.")
        if kind == Kind.DAILY:
            if "times" in cleaned and not cleaned["times"]:
                self.add_error("times", "Enter at least one time, e.g. 06:00.")
            cleaned["interval_minutes"], cleaned["run_at"] = None, None
        elif kind == Kind.INTERVAL:
            minutes = cleaned.get("interval_minutes")
            if not minutes:
                self.add_error("interval_minutes", "Enter the number of minutes.")
            elif minutes > 1440:
                self.add_error("interval_minutes", "At most 1440 minutes (once a day).")
            cleaned["times"], cleaned["run_at"] = "", None
        elif kind == Kind.ONCE:
            run_at = cleaned.get("run_at")
            if run_at is None:
                if "run_at" not in self.errors:
                    self.add_error("run_at", "Choose the date and time.")
            elif run_at <= timezone.now() and (
                not self.instance.pk or {"run_at", "kind"} & set(self.changed_data)
            ):
                # Waktu lampau hanya ditolak bila baru diisi; jadwal "sekali" yang
                # sudah lewat tetap bisa disimpan saat mengubah bagian lain aturan.
                self.add_error("run_at", "Choose a time in the future.")
            cleaned["times"], cleaned["interval_minutes"] = "", None
            cleaned["weekdays"] = "0123456"
        return cleaned


def schedule_checks(event_is_schedule, schedule_form, action_formset):
    """Pemeriksaan ECA berjadwal yang melibatkan beberapa form. Mengembalikan daftar error."""
    if not event_is_schedule or "active_for_seconds" not in schedule_form.fields:
        return []
    active_for = schedule_form.cleaned_data.get("active_for_seconds") or 0
    has_clear = any(
        form.cleaned_data.get("run_on") == RuleAction.RunOn.CLEAR
        for form in action_formset.forms
        if form.cleaned_data and not form.cleaned_data.get("DELETE")
    )
    if has_clear and not active_for:
        return ["“On clear” actions need “Keep active for” in the schedule: "
                "a scheduled rule clears only after that time."]
    return []


# --- Rule chain (Flow) -------------------------------------------------------------------
#
# Form rule chain terdiri dari:
#     RuleForm        nama, deskripsi, aktif (sama dengan ECA)
#     FlowSpecForm    pesan dari device mana yang masuk ke Input
#     NodeFormSet     node-node graf (model formset: id node tetap, ingatannya tidak hilang)
#     LinkFormSet     panah antar-node. Formset BIASA (bukan model formset), karena
#                     node tujuan bisa saja node baru yang belum punya id: panah
#                     menyebut node lewat NAMANYA, lalu view menerjemahkan nama -> node.

INPUT = "Input"
NODE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# Field yang WAJIB untuk setiap jenis node (field lain milik jenis itu opsional).
NODE_REQUIRED = {
    FlowNode.Kind.CHECK: ("key", "operator", "threshold"),
    FlowNode.Kind.CHANGED: ("key",),
    FlowNode.Kind.THROTTLE: ("seconds",),
    FlowNode.Kind.LATEST: ("variable",),
    FlowNode.Kind.CALCULATE: ("key", "operand_a", "math_op", "operand_b"),
    FlowNode.Kind.SET_ACTUATOR: ("target_variable", "value"),
    FlowNode.Kind.SEND_COMMAND: ("target_device", "method"),
    FlowNode.Kind.CREATE_ALARM: ("severity",),
    FlowNode.Kind.CLEAR_ALARM: (),
    FlowNode.Kind.EMAIL: (),
    FlowNode.Kind.TELEGRAM: (),
}
NODE_BLANKS = {
    "key": "", "operator": "", "threshold": None, "operand_a": "", "math_op": "", "operand_b": "",
    "variable": None, "seconds": None, "target_variable": None, "value": None, "target_device": None,
    "method": "", "params": {}, "severity": "", "message": "", "recipients": "",
}


class FlowSpecForm(BootstrapFormMixin, forms.ModelForm):
    """Input rule chain: telemetri (dari satu device atau semua), atau jadwal (tahap 14)."""

    class Meta:
        model = FlowSpec
        fields = ("trigger", "device")
        widgets = {"trigger": forms.RadioSelect}

    def __init__(self, *args, devices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = devices
        self.fields["device"].empty_label = "— Any of my devices —"
        self.fields["trigger"].widget.attrs["class"] = "form-check-input"
        self.fields["trigger"].widget.attrs["data-flow-trigger"] = ""
        # Tidak wajib: form/JSON lama (sebelum tahap 14) tidak mengirim field ini.
        self.fields["trigger"].required = False
        self.fields["device"].help_text = (
            "Telemetry: blank = every device you can see. "
            "Schedule: optional; the device used by alarm nodes and {deviceName}."
        )

    def clean_trigger(self):
        return self.cleaned_data.get("trigger") or FlowSpec.Trigger.TELEMETRY


def kind_choices():
    """Jenis node dikelompokkan per kategori (<optgroup>) di daftar pilihan."""
    from .engines.flow import NODE_TYPES

    groups = {}
    for kind, label in FlowNode.Kind.choices:
        groups.setdefault(NODE_TYPES[kind].category, []).append((kind, label))
    return list(groups.items())


class FlowNodeForm(BootstrapFormMixin, forms.ModelForm):
    params = forms.CharField(
        label="Parameters", required=False,
        widget=forms.TextInput(attrs={"placeholder": '{"seconds": 10}'}),
        help_text="Optional JSON object.",
    )
    severity = forms.ChoiceField(label="Severity", choices=Alarm.Severity.choices, required=False)

    class Meta:
        model = FlowNode
        fields = (
            "name", "kind", "key", "operator", "threshold", "operand_a", "math_op", "operand_b",
            "variable", "seconds", "target_variable", "value", "target_device", "method",
            "params", "ttl_seconds", "severity", "message", "recipients",
        )
        field_classes = {"variable": VariableChoiceField, "target_variable": VariableChoiceField}
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. too_hot", "data-node-name": ""}),
            "key": forms.TextInput(attrs={"placeholder": "e.g. temperature"}),
            "operand_a": forms.TextInput(attrs={"placeholder": "key or number"}),
            "operand_b": forms.TextInput(attrs={"placeholder": "key or number"}),
            "message": forms.TextInput(attrs={"placeholder": "{deviceName}: temperature {temperature} °C"}),
            "method": forms.TextInput(attrs={"placeholder": "e.g. buzzer"}),
        }
        help_texts = {"name": "", "message": "Placeholders: {rule} {device} {values} and any message key, e.g. {temperature}."}

    def __init__(self, *args, devices, **kwargs):
        super().__init__(*args, **kwargs)
        variables = Variable.objects.filter(device__in=devices).select_related("device").order_by("device__name", "key")
        self.fields["variable"].queryset = variables
        self.fields["target_variable"].queryset = variables.filter(kind=Variable.Kind.ACTUATOR)
        self.fields["target_device"].queryset = devices
        self.fields["variable"].empty_label = "— Variable —"
        self.fields["target_variable"].empty_label = "— Choose an actuator —"
        self.fields["target_device"].empty_label = "— Choose a device —"
        self.fields["kind"].choices = kind_choices()
        self.fields["kind"].widget.attrs["data-node-kind"] = ""
        self.fields["operator"].choices = [("", "—")] + list(Operator.choices)
        self.fields["math_op"].choices = [("", "—")] + list(MathOp.choices)
        self.fields["key"].label = "Message key"
        if self.instance.pk and self.instance.params:
            self.initial["params"] = json.dumps(self.instance.params)
        if not self.initial.get("severity"):
            self.initial["severity"] = Alarm.Severity.MAJOR
        self.fields["seconds"].widget.attrs["min"] = 0

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not NODE_NAME.match(name):
            raise forms.ValidationError("Use letters, digits and _, starting with a letter.")
        if name.lower() == INPUT.lower():
            raise forms.ValidationError("“Input” is reserved for the start of the chain.")
        return name

    def clean_params(self):
        return RuleActionForm.clean_params(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        kind = cleaned.get("kind")
        if kind not in NODE_REQUIRED:
            return cleaned
        from .engines.flow import NODE_TYPES

        for name in NODE_REQUIRED[kind]:
            if cleaned.get(name) in (None, ""):
                self.add_error(name, "Required for this node type.")
        for name in ("operand_a", "operand_b"):
            text = (cleaned.get(name) or "").strip()
            if kind == FlowNode.Kind.CALCULATE and text:
                cleaned[name] = text
                try:
                    float(text)
                except ValueError:
                    if not re.match(r"^[A-Za-z_][A-Za-z0-9_\-]*$", text):
                        self.add_error(name, "Enter a message key or a number.")
        if kind == FlowNode.Kind.TELEGRAM:
            clean_telegram_chats(self, cleaned)
        if kind == FlowNode.Kind.SET_ACTUATOR:
            variable, value = cleaned.get("target_variable"), cleaned.get("value")
            if variable and value is not None:
                if variable.is_boolean and value not in (0, 1):
                    self.add_error("value", "On/off actuators accept 1 (ON) or 0 (OFF).")
                elif variable.min_value is not None and value < variable.min_value:
                    self.add_error("value", f"Must be at least {variable.min_value:g}.")
                elif variable.max_value is not None and value > variable.max_value:
                    self.add_error("value", f"Must be at most {variable.max_value:g}.")
        # Kosongkan field milik jenis node lain, supaya data tersimpan rapi.
        used = set(NODE_TYPES[kind].fields)
        for name, blank in NODE_BLANKS.items():
            if name not in used:
                cleaned[name] = blank
        return cleaned


class BaseNodeFormSet(forms.BaseInlineFormSet):
    def live_forms(self):
        return [
            form for form in self.forms
            if (form.has_changed() or form.instance.pk)
            and not (self.can_delete and self._should_delete_form(form))
            and form.cleaned_data
        ]

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen = set()
        for form in self.live_forms():
            name = form.cleaned_data.get("name", "")
            if name.lower() in seen:
                form.add_error("name", "Another node already has this name.")
            seen.add(name.lower())

    def node_kinds(self):
        """{nama node: jenis} untuk semua node yang akan disimpan."""
        return {f.cleaned_data["name"]: f.cleaned_data["kind"] for f in self.live_forms()}


NodeFormSet = inlineformset_factory(
    Rule, FlowNode, form=FlowNodeForm, formset=BaseNodeFormSet,
    extra=0, min_num=1, validate_min=True, max_num=30, validate_max=True, can_delete=True,
)


class LinkForm(forms.Form):
    """Satu panah: <asal> —<label>→ <tujuan>. Asal/tujuan = NAMA node ("Input" = awal)."""

    source = forms.CharField(max_length=40)
    label = forms.ChoiceField(choices=FlowLink.Label.choices)
    target = forms.CharField(max_length=40)


LinkFormSet = forms.formset_factory(LinkForm, extra=0, can_delete=True, max_num=60, validate_max=True)


def live_links(link_formset):
    return [
        form.cleaned_data for form in link_formset.forms
        if form.cleaned_data and not form.cleaned_data.get("DELETE")
    ]


def check_graph(node_kinds, links):
    """Pemeriksaan graf utuh. `node_kinds` = {nama: jenis}, `links` = [{"source", "label", "target"}].

    Mengembalikan daftar pesan error. Aturan yang diperiksa:
    - nama asal/tujuan harus ada; Input hanya boleh menjadi asal,
    - label harus salah satu keluaran node asal (mis. filter: True/False/Failure),
    - tidak ada panah kembar dan tidak ada SIKLUS (pesan bisa berputar selamanya),
    - minimal satu panah dari Input.
    """
    from .engines.flow import NODE_TYPES

    errors = []
    seen = set()
    for link in links:
        source, label, target = link["source"], link["label"], link["target"]
        arrow = f"{source} —{FlowLink.Label(label).label}→ {target}"
        if source != INPUT and source not in node_kinds:
            errors.append(f"{arrow}: there is no node called “{source}”.")
            continue
        if target not in node_kinds:
            errors.append(f"{arrow}: there is no node called “{target}”." if target != INPUT
                          else f"{arrow}: Input can only be the start of a link.")
            continue
        if source == target:
            errors.append(f"{arrow}: a node cannot link to itself.")
        if source == INPUT:
            link["label"] = FlowLink.Label.SUCCESS   # panah dari Input selalu "Success"
        else:
            outputs = NODE_TYPES[node_kinds[source]].outputs
            if label not in outputs:
                names = "/".join(FlowLink.Label(o).label for o in outputs)
                errors.append(f"{arrow}: “{source}” ends with {names}, not {FlowLink.Label(label).label}.")
        key = (source, link["label"], target)
        if key in seen:
            errors.append(f"{arrow} appears twice.")
        seen.add(key)
    if not any(link["source"] == INPUT for link in links):
        errors.append("Connect Input to the first node.")
    if errors:
        return errors

    # Cari siklus dengan DFS berwarna: putih = belum, abu-abu = sedang ditelusuri, hitam = selesai.
    # Bertemu node abu-abu lagi berarti ada jalan yang kembali ke dirinya sendiri.
    following = {}
    for link in links:
        following.setdefault(link["source"], []).append(link["target"])
    color = {}

    def visit(name, path):
        color[name] = "grey"
        for nxt in following.get(name, []):
            if color.get(nxt) == "grey":
                cycle = path[path.index(nxt):] + [nxt]
                return " → ".join(cycle)
            if nxt not in color:
                found = visit(nxt, path + [nxt])
                if found:
                    return found
        color[name] = "black"
        return None

    for name in list(node_kinds):
        if name not in color:
            found = visit(name, [name])
            if found:
                errors.append(f"The links form a loop: {found}. A rule chain must not loop back.")
                break
    return errors

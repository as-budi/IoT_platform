"""
Form untuk membuat aturan ECA.

Satu halaman berisi tiga bagian yang disimpan bersamaan:
    RuleForm           nama, deskripsi, aktif
    EcaSpecForm        event, condition, clear (histeresis), durasi, cooldown
    ActionFormSet      satu atau lebih aksi (formset = kumpulan form sejenis)

Semua pilihan device/variabel dibatasi ke device yang boleh dilihat pemilik
aturan (visible_devices), sehingga pengguna tidak bisa memilih device orang lain
walaupun mengubah isi form di browser.
"""
import json

from django import forms
from django.forms import inlineformset_factory

from accounts.forms import BootstrapFormMixin
from devices.models import Variable

from .models import Alarm, EcaSpec, Operator, Rule, RuleAction

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
    class Meta:
        model = EcaSpec
        fields = (
            "event",
            "variable",
            "operator",
            "threshold",
            "duration_seconds",
            "device",
            "silence_seconds",
            "clear_operator",
            "clear_threshold",
            "cooldown_seconds",
        )
        field_classes = {"variable": VariableChoiceField}
        widgets = {"event": forms.RadioSelect}
        help_texts = {
            "threshold": "On/off variables use 1 for ON and 0 for OFF.",
            "clear_threshold": "Leave both clear fields blank to clear as soon as the condition stops holding.",
        }

    def __init__(self, *args, devices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["variable"].queryset = (
            Variable.objects.filter(device__in=devices).select_related("device").order_by("device__name", "key")
        )
        self.fields["device"].queryset = devices
        self.fields["variable"].empty_label = "— Choose a variable —"
        self.fields["device"].empty_label = "— Choose a device —"
        self.fields["operator"].choices = [("", "—")] + Operator.choices
        self.fields["clear_operator"].choices = [("", "—")] + Operator.choices
        # RadioSelect tidak cocok dengan class "form-select" dari BootstrapFormMixin.
        self.fields["event"].widget.attrs["class"] = "form-check-input"

    def clean(self):
        cleaned = super().clean()
        event = cleaned.get("event")
        if event == EcaSpec.Event.TELEMETRY:
            for field in ("variable", "operator", "threshold"):
                if cleaned.get(field) in (None, ""):
                    self.add_error(field, "Required for this event.")
            cleaned["device"] = None
            cleaned["silence_seconds"] = None
            self.check_hysteresis(cleaned)
        elif event == EcaSpec.Event.INACTIVITY:
            if not cleaned.get("device"):
                self.add_error("device", "Required for this event.")
            if not cleaned.get("silence_seconds"):
                self.add_error("silence_seconds", "Enter how many seconds without data count as silent.")
            # Kondisi "diam" dan pulihnya sudah tertentu; field ini tidak dipakai.
            cleaned.update(
                variable=None, operator="", threshold=None, clear_operator="",
                clear_threshold=None, duration_seconds=0,
            )
        return cleaned

    def check_hysteresis(self, cleaned):
        """Kondisi clear tidak boleh tumpang tindih dengan kondisi aktif.

        Contoh benar : aktif bila < 20, clear bila > 30 (jeda 20–30 = histeresis).
        Contoh salah : aktif bila < 20, clear bila > 15 -> nilai 17 memenuhi
        keduanya, sehingga aturan bisa aktif dan clear bergantian terus.
        """
        op, threshold = cleaned.get("operator"), cleaned.get("threshold")
        clear_op, clear_threshold = cleaned.get("clear_operator"), cleaned.get("clear_threshold")
        if bool(clear_op) != (clear_threshold is not None):
            self.add_error("clear_threshold", "Fill in both the clear operator and the clear threshold, or neither.")
            return
        if not clear_op or threshold is None:
            return
        if (op in LOW and clear_op in LOW) or (op in HIGH and clear_op in HIGH):
            self.add_error("clear_operator", "The clear condition must point the other way (e.g. activate below, clear above).")
        elif op in LOW and clear_op in HIGH and clear_threshold < threshold:
            self.add_error("clear_threshold", f"Must be at least {threshold:g}, otherwise both conditions can hold at once.")
        elif op in HIGH and clear_op in LOW and clear_threshold > threshold:
            self.add_error("clear_threshold", f"Must be at most {threshold:g}, otherwise both conditions can hold at once.")


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
            "message": "Placeholders: {rule} {device} {variable} {value} {threshold}.",
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

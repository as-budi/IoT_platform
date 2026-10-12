"""
Form untuk membuat aturan ECA.

Satu halaman berisi beberapa bagian yang disimpan bersamaan:
    RuleForm           nama, deskripsi, aktif
    EcaSpecForm        event, durasi, cooldown, batas umur data
    WhenFormSet        kondisi aktif, dikelompokkan: OR antar-grup, AND di dalam grup
    ClearFormSet       kondisi clear (opsional, untuk histeresis), bentuk yang sama
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

from .models import Alarm, EcaCondition, EcaSpec, Operator, Rule, RuleAction

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

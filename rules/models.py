"""
Model data rule engine.

Sebuah aturan (Rule) selalu punya MODEL rule engine (lihat docs/rule-engine-iot.md):

    ECA, Flow/rule chain, SQL streaming, CEP, Production rules, State machine

Tahap 7 baru mengimplementasikan ECA (Event–Condition–Action). Model lain sudah
terdaftar sebagai pilihan (RuleModel) dan punya "kerangka" mesin di
rules/engines/, tetapi belum bisa dipakai.

Struktur tabel:

    Rule            aturan milik seorang pengguna: nama, model, aktif/tidak
      EcaSpec       (1-1) isi aturan ECA: event, durasi, cooldown, batas umur data
        EcaCondition (1-banyak) kondisi "variabel op ambang", dikelompokkan OR-berisi-AND
      RuleAction    (1-banyak) aksi: set aktuator, kirim command, alarm, email
      RuleState     (1-1) keadaan aturan saat ini: IDLE / PENDING / ACTIVE
      RuleExecution (1-banyak) log setiap kali aturan aktif atau pulih
    Alarm           alarm yang dibuat aksi ALARM; punya siklus hidupnya sendiri

Mengapa EcaSpec dipisah dari Rule? Field-nya hanya bermakna untuk model ECA.
Model lain nanti punya tabel spesifikasinya sendiri (mis. graf node untuk
rule chain), sedangkan Rule, RuleAction, RuleState, dan RuleExecution dipakai
bersama.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from devices.models import Device, Variable, key_validator


class RuleModel(models.TextChoices):
    """Enam model rule engine. Penjelasan dan status implementasinya ada di
    rules/engines/ (ENGINES[...].summary, .implemented)."""

    ECA = "ECA", "Event–Condition–Action"
    FLOW = "FLOW", "Flow-based rule chain"
    SQL = "SQL", "SQL over the message stream"
    CEP = "CEP", "Complex event processing"
    PRODUCTION = "PRODUCTION", "Production rules (RETE)"
    STATE_MACHINE = "STATE_MACHINE", "State machine"


class Rule(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="owner",
        on_delete=models.CASCADE,
        related_name="rules",
    )
    name = models.CharField("name", max_length=100)
    description = models.TextField("description", blank=True)
    model = models.CharField(
        "rule model", max_length=20, choices=RuleModel.choices, default=RuleModel.ECA
    )
    is_enabled = models.BooleanField(
        "enabled", default=True, help_text="Disabled rules are kept but not evaluated."
    )
    created_at = models.DateTimeField("created", auto_now_add=True)
    updated_at = models.DateTimeField("updated", auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "rule"
        verbose_name_plural = "rules"

    def __str__(self):
        return self.name

    @property
    def engine(self):
        """Mesin yang menjalankan aturan ini (lihat rules/engines/)."""
        from .engines import ENGINES

        return ENGINES[self.model]

    @property
    def state_status(self):
        state = getattr(self, "state", None)
        return state.status if state else RuleState.Status.IDLE


# --- ECA ------------------------------------------------------------------------

class Operator(models.TextChoices):
    GT = ">", ">"
    GTE = ">=", "≥"
    LT = "<", "<"
    LTE = "<=", "≤"
    EQ = "==", "="
    NE = "!=", "≠"


def compare(value, operator, threshold):
    """Bandingkan `value` dengan `threshold` memakai operator, mis. compare(18, "<", 20) -> True."""
    if value is None or threshold is None:
        return False
    if operator == Operator.GT:
        return value > threshold
    if operator == Operator.GTE:
        return value >= threshold
    if operator == Operator.LT:
        return value < threshold
    if operator == Operator.LTE:
        return value <= threshold
    if operator == Operator.EQ:
        return abs(value - threshold) < 1e-9
    if operator == Operator.NE:
        return abs(value - threshold) >= 1e-9
    raise ValueError(f"Unknown operator {operator!r}")


class EcaSpec(models.Model):
    """Isi aturan ECA.

        WHEN  <event>                      data baru untuk variabel di kondisi, atau device diam
        IF    <ekspresi kondisi>           kelompok OR yang masing-masing berisi kondisi AND,
                                           harus bertahan <duration> detik
        THEN  aksi "on activate"
        CLEAR bila <ekspresi clear>        (opsional; histeresis)
              atau, bila kosong, saat ekspresi kondisi bernilai salah
        THEN  aksi "on clear"

    Kondisinya sendiri ada di tabel EcaCondition (lihat di bawah).
    """

    class Event(models.TextChoices):
        TELEMETRY = "TELEMETRY", "New data for a variable in the conditions"
        INACTIVITY = "INACTIVITY", "Device sends no data"

    rule = models.OneToOneField(Rule, on_delete=models.CASCADE, related_name="eca", primary_key=True)
    event = models.CharField("event", max_length=12, choices=Event.choices, default=Event.TELEMETRY)

    # Event INACTIVITY: device yang diamati, dan berapa lama boleh diam.
    device = models.ForeignKey(
        Device,
        verbose_name="device",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="eca_inactivity_triggers",
    )
    silence_seconds = models.PositiveIntegerField(
        "no data for (seconds)", null=True, blank=True
    )

    # Pencegahan flapping (lihat docs/rule-engine-iot.md bagian 4).
    duration_seconds = models.PositiveIntegerField(
        "for at least (seconds)",
        default=0,
        help_text="The condition must hold this long before the rule activates. 0 = immediately.",
    )
    cooldown_seconds = models.PositiveIntegerField(
        "cooldown (seconds)",
        default=0,
        help_text="Minimum time between two activations. 0 = no cooldown.",
    )
    # Nilai variabel lain diambil dari data terakhir di database. Data yang lebih
    # tua dari batas ini dianggap "tidak diketahui" (lihat rules/conditions.py).
    max_age_seconds = models.PositiveIntegerField(
        "ignore values older than (seconds)",
        default=300,
        help_text="A variable whose latest value is older than this counts as unknown. 0 = no limit.",
    )

    class Meta:
        verbose_name = "ECA specification"
        verbose_name_plural = "ECA specifications"

    def __str__(self):
        return f"ECA of {self.rule}"

    # --- kondisi ----------------------------------------------------------------

    def condition_list(self):
        """Semua kondisi aturan ini, memakai hasil prefetch bila ada."""
        return list(self.conditions.all())

    def groups(self, role):
        """[[kondisi, kondisi], [kondisi]] = (A AND B) OR (C), urut nomor grup."""
        grouped = {}
        for condition in self.condition_list():
            if condition.role == role:
                grouped.setdefault(condition.group, []).append(condition)
        return [grouped[number] for number in sorted(grouped)]

    @property
    def when_groups(self):
        return self.groups(EcaCondition.Role.WHEN)

    @property
    def clear_groups(self):
        return self.groups(EcaCondition.Role.CLEAR)

    @property
    def has_hysteresis(self):
        return bool(self.clear_groups)

    def variable_ids(self):
        return {c.variable_id for c in self.condition_list()}

    def primary_condition(self):
        """Kondisi pertama di grup pertama. Device-nya menjadi "device asal" aturan
        (tempat alarm dibuat dan kabar WebSocket dikirim)."""
        when = self.when_groups
        return when[0][0] if when else None


class EcaCondition(models.Model):
    """Satu perbandingan: <variabel> <operator> <ambang>, mis. soil_moisture < 20.

    Kondisi dikelompokkan dengan nomor `group`:
        - kondisi dengan nomor grup SAMA digabung dengan AND,
        - grup-grup yang BERBEDA digabung dengan OR.
    Contoh, role WHEN:
        group 1: soil_moisture < 20, temperature > 30
        group 2: soil_moisture < 10
    berarti  (soil_moisture < 20 AND temperature > 30) OR (soil_moisture < 10).
    Bentuk "OR dari AND" ini (disjunctive normal form) bisa menyatakan semua
    kombinasi AND/OR tanpa tanda kurung bersarang.
    """

    class Role(models.TextChoices):
        WHEN = "WHEN", "Activate when"
        CLEAR = "CLEAR", "Clear when"

    spec = models.ForeignKey(EcaSpec, on_delete=models.CASCADE, related_name="conditions")
    role = models.CharField("role", max_length=5, choices=Role.choices, default=Role.WHEN)
    group = models.PositiveSmallIntegerField("group", default=1)
    order = models.PositiveSmallIntegerField("order", default=0)
    variable = models.ForeignKey(
        Variable, verbose_name="variable", on_delete=models.CASCADE, related_name="eca_conditions"
    )
    operator = models.CharField("operator", max_length=2, choices=Operator.choices)
    threshold = models.FloatField("threshold")

    class Meta:
        ordering = ["role", "group", "order", "pk"]
        verbose_name = "ECA condition"
        verbose_name_plural = "ECA conditions"

    def __str__(self):
        return f"{self.variable.key} {self.operator} {self.threshold:g}"

    def test(self, value):
        return compare(value, self.operator, self.threshold)


# --- Aksi -----------------------------------------------------------------------

class RuleAction(models.Model):
    class Kind(models.TextChoices):
        SET_ACTUATOR = "SET_ACTUATOR", "Set actuator"
        SEND_COMMAND = "SEND_COMMAND", "Send command"
        ALARM = "ALARM", "Raise alarm"
        EMAIL = "EMAIL", "Send email"

    class RunOn(models.TextChoices):
        ACTIVATE = "ACTIVATE", "When the rule activates"
        CLEAR = "CLEAR", "When the rule clears"

    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name="actions")
    kind = models.CharField("action", max_length=15, choices=Kind.choices)
    run_on = models.CharField("run", max_length=10, choices=RunOn.choices, default=RunOn.ACTIVATE)
    order = models.PositiveSmallIntegerField("order", default=0)

    # SET_ACTUATOR
    target_variable = models.ForeignKey(
        Variable,
        verbose_name="actuator",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    value = models.FloatField("value", null=True, blank=True)

    # SEND_COMMAND
    target_device = models.ForeignKey(
        Device,
        verbose_name="device",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    method = models.CharField("method", max_length=50, blank=True, validators=[key_validator])
    params = models.JSONField("parameters", default=dict, blank=True)
    ttl_seconds = models.PositiveIntegerField("expires after (seconds)", default=300)

    # ALARM
    severity = models.CharField("severity", max_length=10, blank=True)  # Alarm.Severity
    # ALARM dan EMAIL: teks dengan placeholder {rule}, {device}, {variable}, {value}, {threshold}
    message = models.CharField("message", max_length=255, blank=True)

    # EMAIL
    recipients = models.CharField(
        "recipients", max_length=255, blank=True, help_text="Comma-separated. Blank = the rule owner."
    )

    class Meta:
        ordering = ["order", "pk"]
        verbose_name = "rule action"
        verbose_name_plural = "rule actions"

    def __str__(self):
        return f"{self.get_kind_display()} ({self.get_run_on_display().lower()})"


# --- Keadaan & log ---------------------------------------------------------------

class RuleState(models.Model):
    """Keadaan sebuah aturan, disimpan di database supaya tidak hilang saat
    worker restart (aturan "selama 60 detik" butuh ingatan)."""

    class Status(models.TextChoices):
        IDLE = "IDLE", "Idle"             # kondisi tidak terpenuhi
        PENDING = "PENDING", "Pending"    # kondisi terpenuhi, menunggu durasi/cooldown
        ACTIVE = "ACTIVE", "Active"       # aturan aktif; aksi "on activate" sudah jalan

    rule = models.OneToOneField(Rule, on_delete=models.CASCADE, related_name="state", primary_key=True)
    status = models.CharField("status", max_length=8, choices=Status.choices, default=Status.IDLE)
    pending_since = models.DateTimeField("pending since", null=True, blank=True)
    last_value = models.FloatField("last value", null=True, blank=True)
    last_evaluated_at = models.DateTimeField("last evaluated", null=True, blank=True)
    last_activated_at = models.DateTimeField("last activated", null=True, blank=True)
    last_cleared_at = models.DateTimeField("last cleared", null=True, blank=True)
    # Apa yang "dilihat" mesin pada penilaian terakhir: nilai tiap variabel,
    # umurnya, dan hasil tiap kondisi (lihat rules/conditions.py). Untuk halaman detail.
    snapshot = models.JSONField("last evaluation", default=dict, blank=True)

    class Meta:
        verbose_name = "rule state"
        verbose_name_plural = "rule states"

    def __str__(self):
        return f"{self.rule}: {self.status}"


class RuleExecution(models.Model):
    """Satu kali aturan berpindah ke ACTIVE atau kembali ke IDLE, beserta hasil aksinya."""

    class Transition(models.TextChoices):
        ACTIVATE = "ACTIVATE", "Activated"
        CLEAR = "CLEAR", "Cleared"

    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name="executions")
    transition = models.CharField("transition", max_length=10, choices=Transition.choices)
    value = models.FloatField("trigger value", null=True, blank=True)
    # [{"action": "Set actuator", "ok": true, "detail": "pump = ON"}, ...]
    results = models.JSONField("results", default=list)
    ok = models.BooleanField("all actions succeeded", default=True)
    # Nilai semua variabel kondisi saat transisi terjadi, mis. {"Soil moisture": 18, ...}.
    snapshot = models.JSONField("values", default=dict, blank=True)
    created_at = models.DateTimeField("time", default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "rule execution"
        verbose_name_plural = "rule executions"

    def __str__(self):
        return f"{self.rule} {self.transition} at {self.created_at:%Y-%m-%d %H:%M:%S}"


# --- Alarm ------------------------------------------------------------------------

class AlarmQuerySet(models.QuerySet):
    def active(self):
        return self.filter(cleared_at__isnull=True)


class Alarm(models.Model):
    """Alarm dengan siklus hidup (state machine kecil), mengikuti ThingsBoard:

        ACTIVE, UNACKNOWLEDGED  ──acknowledge──►  ACTIVE, ACKNOWLEDGED
                │                                         │
              clear                                     clear
                ▼                                         ▼
        CLEARED, UNACKNOWLEDGED ──acknowledge──►  CLEARED, ACKNOWLEDGED

    "Acknowledge" = seseorang sudah melihat dan menanganinya. "Clear" = kondisi
    penyebabnya sudah hilang (otomatis oleh aturan, atau manual).
    """

    class Severity(models.TextChoices):
        CRITICAL = "CRITICAL", "Critical"
        MAJOR = "MAJOR", "Major"
        MINOR = "MINOR", "Minor"
        WARNING = "WARNING", "Warning"

    rule = models.ForeignKey(Rule, null=True, blank=True, on_delete=models.SET_NULL, related_name="alarms")
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="alarms")
    severity = models.CharField("severity", max_length=10, choices=Severity.choices, default=Severity.MAJOR)
    message = models.CharField("message", max_length=255)
    value = models.FloatField("value", null=True, blank=True)
    created_at = models.DateTimeField("raised", default=timezone.now)
    acknowledged_at = models.DateTimeField("acknowledged", null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cleared_at = models.DateTimeField("cleared", null=True, blank=True)
    cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Empty when cleared automatically by the rule.",
    )

    objects = AlarmQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "alarm"
        verbose_name_plural = "alarms"
        indexes = [models.Index(fields=["device", "cleared_at"], name="alarm_device_cleared")]

    def __str__(self):
        return f"{self.get_severity_display()}: {self.message}"

    @property
    def is_active(self):
        return self.cleared_at is None

    @property
    def is_acknowledged(self):
        return self.acknowledged_at is not None

    @property
    def status_label(self):
        return ("Active" if self.is_active else "Cleared") + (
            ", acknowledged" if self.is_acknowledged else ", unacknowledged"
        )

    def acknowledge(self, user):
        if self.is_acknowledged:
            return False
        self.acknowledged_at = timezone.now()
        self.acknowledged_by = user
        self.save(update_fields=["acknowledged_at", "acknowledged_by"])
        alarm_changed(self)
        return True

    def clear(self, user=None):
        if not self.is_active:
            return False
        self.cleared_at = timezone.now()
        self.cleared_by = user
        self.save(update_fields=["cleared_at", "cleared_by"])
        alarm_changed(self)
        return True


def alarm_changed(alarm):
    """Kabari browser yang membuka device ini atau dashboard (WebSocket)."""
    from realtime import events

    events.emit(
        alarm.device_id,
        {
            "type": "alarm",
            "id": alarm.pk,
            "rule": alarm.rule_id,
            "severity": alarm.severity,
            "message": alarm.message,
            "active": alarm.is_active,
            "acknowledged": alarm.is_acknowledged,
        },
    )

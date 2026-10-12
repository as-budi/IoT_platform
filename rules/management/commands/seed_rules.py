"""
Perintah kustom:  python manage.py seed_rules [--reset]

Membuat contoh aturan ECA multi-variabel untuk device dari `seed_demo`.
Jalankan setelah `python manage.py seed_demo`. Aman dijalankan berulang:
aturan yang namanya sudah ada untuk pemilik yang sama dilewati, kecuali
--reset (aturan contoh dihapus lalu dibuat ulang).

Setiap aturan menunjukkan satu pola (lihat docs/contoh-data-dan-aturan.md):

    Smart irrigation      OR dari dua grup AND, lintas device, clear juga OR
    Poultry heat stress   AND dua variabel OR satu variabel, aksi + email
    Pond low oxygen       ambang bertingkat: kritis ATAU (agak rendah DAN air panas)
    Cold chain breach     aksi command ke device, alarm kritis, clear dengan AND
    Server room cooling   aturan lintas device: sensor di satu device, aktuator di device lain
    Weather station silent  event "device diam" (nonaktif bawaan; aktifkan untuk mencoba)

Kondisi ditulis sebagai daftar grup: [[(device, key, op, ambang), ...], ...]
  - kondisi di dalam satu grup digabung AND,
  - grup-grup digabung OR.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from devices.models import Device, Variable
from rules.models import Alarm, EcaCondition, EcaSpec, Rule, RuleAction, RuleModel, RuleState

User = get_user_model()

ACTIVATE, CLEAR = RuleAction.RunOn.ACTIVATE, RuleAction.RunOn.CLEAR

DEMO_RULES = [
    {
        "owner": "siti",
        "name": "Smart irrigation",
        "description": "Water when the soil is dry and it is not raining. Hot air makes "
                       "the plants thirsty sooner, so a higher soil threshold applies then.",
        "when": [
            [("Greenhouse Node", "soil_moisture", "<", 30),
             ("Greenhouse Node", "temperature", ">", 32),
             ("Greenhouse Weather Station", "rain", "<", 0.5)],
            [("Greenhouse Node", "soil_moisture", "<", 20),
             ("Greenhouse Weather Station", "rain", "<", 0.5)],
        ],
        "clear": [
            [("Greenhouse Node", "soil_moisture", ">", 45)],
            [("Greenhouse Weather Station", "rain", ">", 2)],
        ],
        "duration": 60, "cooldown": 300, "max_age": 900,
        "actions": [
            ("SET_ACTUATOR", ACTIVATE, {"target": ("Greenhouse Node", "pump"), "value": 1}),
            ("ALARM", ACTIVATE, {"severity": "WARNING", "message": "Irrigation started: {values}"}),
            ("SET_ACTUATOR", CLEAR, {"target": ("Greenhouse Node", "pump"), "value": 0}),
        ],
    },
    {
        "owner": "andi",
        "name": "Poultry heat stress",
        "description": "Broilers suffer when it is hot AND humid, or when ammonia builds up.",
        "when": [
            [("Poultry House Node", "temperature", ">", 32),
             ("Poultry House Node", "humidity", ">", 70)],
            [("Poultry House Node", "ammonia", ">", 25)],
        ],
        "clear": [
            [("Poultry House Node", "temperature", "<", 29),
             ("Poultry House Node", "ammonia", "<", 15)],
        ],
        "duration": 30, "cooldown": 0, "max_age": 600,
        "actions": [
            ("SET_ACTUATOR", ACTIVATE, {"target": ("Poultry House Node", "exhaust_fan"), "value": 1}),
            ("ALARM", ACTIVATE, {"severity": "MAJOR", "message": "Heat stress in {device}: {values}"}),
            ("EMAIL", ACTIVATE, {"message": "Rule \"{rule}\" {transition}. Values: {values}. The exhaust fan was switched on."}),
            ("SET_ACTUATOR", CLEAR, {"target": ("Poultry House Node", "exhaust_fan"), "value": 0}),
        ],
    },
    {
        "owner": "andi",
        "name": "Pond low oxygen",
        "description": "Warm water holds less oxygen, so a moderately low reading is "
                       "already dangerous when the water is warm.",
        "when": [
            [("Fish Pond Node", "dissolved_oxygen", "<", 4)],
            [("Fish Pond Node", "dissolved_oxygen", "<", 5),
             ("Fish Pond Node", "water_temp", ">", 30)],
        ],
        "clear": [
            [("Fish Pond Node", "dissolved_oxygen", ">", 6)],
        ],
        "duration": 0, "cooldown": 0, "max_age": 600,
        "actions": [
            ("SET_ACTUATOR", ACTIVATE, {"target": ("Fish Pond Node", "aerator"), "value": 1}),
            ("ALARM", ACTIVATE, {"severity": "CRITICAL", "message": "Low oxygen in the pond: {values}"}),
            ("SET_ACTUATOR", CLEAR, {"target": ("Fish Pond Node", "aerator"), "value": 0}),
        ],
    },
    {
        "owner": "rina",
        "name": "Cold chain breach",
        "description": "Vaccines must stay between 2 and 8 °C. Humid air above 6 °C means "
                       "the door seal is probably leaking.",
        "when": [
            [("Cold Storage Unit", "temperature", ">", 8)],
            [("Cold Storage Unit", "temperature", ">", 6),
             ("Cold Storage Unit", "humidity", ">", 85)],
        ],
        "clear": [
            [("Cold Storage Unit", "temperature", "<", 5),
             ("Cold Storage Unit", "humidity", "<", 80)],
        ],
        "duration": 120, "cooldown": 0, "max_age": 600,
        "actions": [
            ("SET_ACTUATOR", ACTIVATE, {"target": ("Cold Storage Unit", "compressor_boost"), "value": 1}),
            ("SEND_COMMAND", ACTIVATE, {"device": "Cold Storage Unit", "method": "buzzer",
                                        "params": {"seconds": 10}, "ttl": 300}),
            ("ALARM", ACTIVATE, {"severity": "CRITICAL", "message": "Cold chain breach: {values}"}),
            ("SET_ACTUATOR", CLEAR, {"target": ("Cold Storage Unit", "compressor_boost"), "value": 0}),
        ],
    },
    {
        "owner": "budi",
        "name": "Server room cooling",
        "description": "Sensors in the server room drive the fan on the lab relay board "
                       "(a rule across two devices).",
        "when": [
            [("Server Room Node", "temperature", ">", 30)],
            [("Server Room Node", "temperature", ">", 27),
             ("Server Room Node", "humidity", ">", 70)],
        ],
        "clear": [
            [("Server Room Node", "temperature", "<", 25)],
        ],
        "duration": 60, "cooldown": 0, "max_age": 600,
        "actions": [
            ("SET_ACTUATOR", ACTIVATE, {"target": ("Lab Relay Board", "fan_speed"), "value": 100}),
            ("SET_ACTUATOR", CLEAR, {"target": ("Lab Relay Board", "fan_speed"), "value": 25}),
        ],
    },
    {
        "owner": "siti",
        "name": "Weather station silent",
        "description": "Disabled by default: enable it, then stop sending data from the "
                       "weather station for 15 minutes.",
        "enabled": False,
        "inactivity": ("Greenhouse Weather Station", 900),
        "actions": [
            ("ALARM", ACTIVATE, {"severity": "WARNING", "message": "{device} has sent no data for {value} s"}),
        ],
    },
]


class Command(BaseCommand):
    help = "Create sample multi-variable ECA rules for the demo devices (run seed_demo first)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete and recreate the sample rules.")

    def handle(self, *args, **options):
        created = 0
        for spec in DEMO_RULES:
            try:
                owner = User.objects.get(username=spec["owner"])
            except User.DoesNotExist:
                raise CommandError(f"User {spec['owner']!r} not found. Run: python manage.py seed_demo")
            existing = Rule.objects.filter(owner=owner, name=spec["name"])
            if existing.exists():
                if not options["reset"]:
                    self.stdout.write(f"Skipping {spec['name']} ({owner.username}): already exists.")
                    continue
                Alarm.objects.filter(rule__in=existing).delete()
                existing.delete()
            with transaction.atomic():
                rule = self.create_rule(owner, spec)
            created += 1
            state = "enabled" if rule.is_enabled else "disabled"
            self.stdout.write(f"Rule created: {rule.name} ({owner.username}, {state})")
        self.stdout.write(self.style.SUCCESS(f"Done. {created} sample rule(s) created."))
        self.stdout.write("Try one: see docs/contoh-data-dan-aturan.md (python manage.py send_telemetry ...)")

    # --- pembuat ----------------------------------------------------------------------

    def variable(self, owner, device_name, key):
        try:
            return Variable.objects.get(device__owner=owner, device__name=device_name, key=key)
        except Variable.DoesNotExist:
            raise CommandError(f"{device_name} · {key} not found for {owner}. Run: python manage.py seed_demo")

    def device(self, owner, name):
        try:
            return Device.objects.get(owner=owner, name=name)
        except Device.DoesNotExist:
            raise CommandError(f"Device {name!r} not found for {owner}. Run: python manage.py seed_demo")

    def create_rule(self, owner, spec):
        rule = Rule.objects.create(
            owner=owner, name=spec["name"], description=spec.get("description", ""),
            model=RuleModel.ECA, is_enabled=spec.get("enabled", True),
        )
        if "inactivity" in spec:
            device_name, seconds = spec["inactivity"]
            eca = EcaSpec.objects.create(
                rule=rule, event=EcaSpec.Event.INACTIVITY,
                device=self.device(owner, device_name), silence_seconds=seconds,
            )
        else:
            eca = EcaSpec.objects.create(
                rule=rule, duration_seconds=spec["duration"],
                cooldown_seconds=spec["cooldown"], max_age_seconds=spec["max_age"],
            )
            for role, groups in ((EcaCondition.Role.WHEN, spec["when"]),
                                 (EcaCondition.Role.CLEAR, spec.get("clear", []))):
                for number, group in enumerate(groups, start=1):
                    for order, (device_name, key, op, threshold) in enumerate(group):
                        EcaCondition.objects.create(
                            spec=eca, role=role, group=number, order=order,
                            variable=self.variable(owner, device_name, key),
                            operator=op, threshold=threshold,
                        )
        for order, (kind, run_on, fields) in enumerate(spec["actions"]):
            action = RuleAction(rule=rule, kind=kind, run_on=run_on, order=order)
            if kind == "SET_ACTUATOR":
                action.target_variable = self.variable(owner, *fields["target"])
                action.value = fields["value"]
            elif kind == "SEND_COMMAND":
                action.target_device = self.device(owner, fields["device"])
                action.method = fields["method"]
                action.params = fields.get("params", {})
                action.ttl_seconds = fields.get("ttl", 300)
            elif kind == "ALARM":
                action.severity = fields["severity"]
                action.message = fields.get("message", "")
            elif kind == "EMAIL":
                action.recipients = fields.get("recipients", "")
                action.message = fields.get("message", "")
            action.save()
        RuleState.objects.create(rule=rule)
        return rule

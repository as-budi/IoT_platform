"""
Perintah kustom:  python manage.py seed_rules [--reset]

Membuat contoh aturan ECA multi-variabel dan contoh rule chain (Flow) untuk
device dari `seed_demo`.
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

Contoh rule chain (lihat docs/flow-mekanisme.md):

    Lab power monitor        dua cabang dari Input, dua node Calculate berurutan,
                             Failure saat pembagian nol, Rate limit sebelum email
    Pond pH watch            Value changed, OR dengan dua panah ke node yang sama,
                             alarm dibuat dan di-clear oleh node
    Cold room door check     AND dengan filter berurutan, Rate limit, kirim command
    Greenhouse watering chain  enrichment (data stasiun cuaca), Failure bila data basi,
                             satu hasil ke dua cabang (salinan pesan). Nonaktif bawaan:
                             memakai pompa yang sama dengan "Smart irrigation".

Node ditulis (nama, jenis, {field}); panah ditulis (asal, label, tujuan).
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from devices.models import Device, Variable
from rules.models import (
    Alarm, EcaCondition, EcaSpec, FlowLink, FlowNode, FlowSpec, Rule, RuleAction, RuleModel, RuleState,
)

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

DEMO_CHAINS = [
    {
        "owner": "budi",
        "name": "Lab power monitor",
        "description": "Over-voltage alarm, and an email (at most every 10 minutes) when the "
                       "power factor of the lab load drops below 0.8.",
        "device": "Lab Power Meter",
        "nodes": [
            ("over_voltage", "CHECK", {"key": "voltage", "operator": ">", "threshold": 235}),
            ("volt_alarm", "CREATE_ALARM", {"severity": "MAJOR", "key": "voltage",
                                            "message": "Over-voltage on {deviceName}: {voltage} V"}),
            ("volt_ok", "CLEAR_ALARM", {}),
            ("apparent", "CALCULATE", {"key": "apparent_power", "operand_a": "voltage", "math_op": "*",
                                       "operand_b": "current"}),
            ("pf", "CALCULATE", {"key": "power_factor", "operand_a": "power", "math_op": "/",
                                 "operand_b": "apparent_power"}),
            ("low_pf", "CHECK", {"key": "power_factor", "operator": "<", "threshold": 0.8}),
            ("pf_limit", "THROTTLE", {"seconds": 600}),
            ("pf_email", "EMAIL", {"message": "Low power factor on {deviceName}: {power_factor} "
                                              "({power} W of {apparent_power} VA)."}),
        ],
        "links": [
            ("Input", "SUCCESS", "over_voltage"),
            ("Input", "SUCCESS", "apparent"),
            ("over_voltage", "TRUE", "volt_alarm"),
            ("over_voltage", "FALSE", "volt_ok"),
            ("apparent", "SUCCESS", "pf"),
            ("pf", "SUCCESS", "low_pf"),
            ("low_pf", "TRUE", "pf_limit"),
            ("pf_limit", "TRUE", "pf_email"),
        ],
    },
    {
        "owner": "andi",
        "name": "Pond pH watch",
        "description": "Tilapia need pH 6.5–8.5. Only re-checks when pH moved by 0.1 or more.",
        "device": "Fish Pond Node",
        "nodes": [
            ("ph_changed", "CHANGED", {"key": "ph", "threshold": 0.1}),
            ("too_acid", "CHECK", {"key": "ph", "operator": "<", "threshold": 6.5}),
            ("too_alkaline", "CHECK", {"key": "ph", "operator": ">", "threshold": 8.5}),
            ("ph_alarm", "CREATE_ALARM", {"severity": "MAJOR", "key": "ph",
                                          "message": "Pond pH out of range: {ph}"}),
            ("ph_ok", "CLEAR_ALARM", {}),
        ],
        "links": [
            ("Input", "SUCCESS", "ph_changed"),
            ("ph_changed", "TRUE", "too_acid"),
            ("too_acid", "TRUE", "ph_alarm"),
            ("too_acid", "FALSE", "too_alkaline"),
            ("too_alkaline", "TRUE", "ph_alarm"),
            ("too_alkaline", "FALSE", "ph_ok"),
        ],
    },
    {
        "owner": "rina",
        "name": "Cold room door check",
        "description": "Humid AND warm air means the door is probably open: sound the buzzer, "
                       "at most once every 5 minutes.",
        "device": "Cold Storage Unit",
        "nodes": [
            ("humid", "CHECK", {"key": "humidity", "operator": ">", "threshold": 85}),
            ("warm", "CHECK", {"key": "temperature", "operator": ">", "threshold": 6}),
            ("buzz_limit", "THROTTLE", {"seconds": 300}),
            ("buzzer", "SEND_COMMAND", {"device": "Cold Storage Unit", "method": "buzzer",
                                        "params": {"seconds": 5}, "ttl": 120}),
        ],
        "links": [
            ("Input", "SUCCESS", "humid"),
            ("humid", "TRUE", "warm"),
            ("warm", "TRUE", "buzz_limit"),
            ("buzz_limit", "TRUE", "buzzer"),
        ],
    },
    {
        "owner": "siti",
        "name": "Greenhouse watering chain",
        "description": "Rule-chain version of Smart irrigation. Disabled by default because both "
                       "rules drive the same pump: disable Smart irrigation before enabling this one.",
        "device": "Greenhouse Node",
        "enabled": False,
        "nodes": [
            ("dry", "CHECK", {"key": "soil_moisture", "operator": "<", "threshold": 25}),
            ("rain_now", "LATEST", {"variable": ("Greenhouse Weather Station", "rain"), "key": "rain",
                                    "seconds": 900}),
            ("no_rain", "CHECK", {"key": "rain", "operator": "<", "threshold": 0.5}),
            ("pump_on", "SET_ACTUATOR", {"target": ("Greenhouse Node", "pump"), "value": 1}),
            ("mail_limit", "THROTTLE", {"seconds": 3600}),
            ("notify", "EMAIL", {"message": "Watering started: soil {soil_moisture} %, rain {rain} mm/h."}),
            ("wet", "CHECK", {"key": "soil_moisture", "operator": ">", "threshold": 45}),
            ("pump_off", "SET_ACTUATOR", {"target": ("Greenhouse Node", "pump"), "value": 0}),
            ("stale_rain", "CREATE_ALARM", {"severity": "WARNING",
                                            "message": "No recent rain data; watering skipped"}),
            ("rain_back", "CLEAR_ALARM", {}),
        ],
        "links": [
            ("Input", "SUCCESS", "dry"),
            ("dry", "TRUE", "rain_now"),
            ("dry", "FALSE", "wet"),
            ("rain_now", "SUCCESS", "no_rain"),
            ("rain_now", "SUCCESS", "rain_back"),
            ("rain_now", "FAILURE", "stale_rain"),
            ("no_rain", "TRUE", "pump_on"),
            ("no_rain", "FALSE", "pump_off"),
            ("pump_on", "SUCCESS", "mail_limit"),
            ("mail_limit", "TRUE", "notify"),
            ("wet", "TRUE", "pump_off"),
        ],
    },
]


class Command(BaseCommand):
    help = "Create sample ECA rules and rule chains for the demo devices (run seed_demo first)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete and recreate the sample rules.")

    def handle(self, *args, **options):
        created = 0
        for spec in DEMO_RULES + DEMO_CHAINS:
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
                rule = self.create_chain(owner, spec) if "nodes" in spec else self.create_rule(owner, spec)
            created += 1
            state = "enabled" if rule.is_enabled else "disabled"
            self.stdout.write(f"Rule created: {rule.name} ({owner.username}, {state})")
        self.stdout.write(self.style.SUCCESS(f"Done. {created} sample rule(s) created."))
        self.stdout.write("Rule chains: see docs/flow-mekanisme.md for messages to send to each one.")
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

    def create_chain(self, owner, spec):
        rule = Rule.objects.create(
            owner=owner, name=spec["name"], description=spec.get("description", ""),
            model=RuleModel.FLOW, is_enabled=spec.get("enabled", True),
        )
        FlowSpec.objects.create(rule=rule, device=self.device(owner, spec["device"]) if spec.get("device") else None)
        nodes = {}
        for order, (name, kind, fields) in enumerate(spec["nodes"]):
            node = FlowNode(rule=rule, name=name, kind=kind, order=order)
            for field_name in ("key", "operator", "threshold", "operand_a", "math_op", "operand_b",
                               "seconds", "value", "method", "params", "severity", "message", "recipients"):
                if field_name in fields:
                    setattr(node, field_name, fields[field_name])
            if "variable" in fields:
                node.variable = self.variable(owner, *fields["variable"])
            if "target" in fields:
                node.target_variable = self.variable(owner, *fields["target"])
            if "device" in fields:
                node.target_device = self.device(owner, fields["device"])
            if "ttl" in fields:
                node.ttl_seconds = fields["ttl"]
            node.save()
            nodes[name] = node
        for order, (source, label, target) in enumerate(spec["links"]):
            FlowLink.objects.create(
                rule=rule, source=None if source == "Input" else nodes[source],
                label=label, target=nodes[target], order=order,
            )
        RuleState.objects.create(rule=rule)
        return rule

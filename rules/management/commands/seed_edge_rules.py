"""
python manage.py seed_edge_rules [--owner siti] [--neighbour budi] [--reset]

Contoh aturan micro rule (tahap 26): satu aturan untuk setiap kemungkinan yang dikenali
analisis dan compiler, lengkap dengan device-nya (nama berawalan "Edge "). Semua device
diberi MAC tiruan (lihat fill_test_macs) kecuali "Edge New Sensor", yang sengaja dibiarkan
tanpa MAC. Semua aturan memakai "Also run on my devices", sehingga bagian "Run on devices"
di halaman aturan langsung terisi.

    01 Local loop                  sensor dan aktuator di device yang sama: tanpa radio
    02 One remote sensor           satu sensor, satu aktuator di device lain
    03 Two sensors, both required  AND lintas device; SE butuh dua hop
    04 Either sensor               OR lintas device; SE menambah device kritis (L1)
    05 Timing                      "for at least" + cooldown + clear dengan histeresis
    06 Two actuators               pompa dan katup di dua device: AE tereplikasi vs SE satu keputusan
    07 Sensor at the actuator      SE memilih aktuator: rencana AE dan SE identik
    08 Evaluator policy            SE: "primary" memilih Light Sensor, "most_signals" Weather Station
    09 Command action              Send command (butuh handler di firmware)
    10 Control plus notifications  aksi kendali di device; alarm dan email tetap di platform
    11 Shared broadcast            sinyal yang sama dipakai dua aturan: tabel publikasi digabung
    12 No age limit (warning)      max_age 0
    13 Fractional equality (warning)  "=" dengan ambang pecahan
    14 Scheduled (platform only)   event Schedule
    15 Device silent (platform only)  event "device sends no data"
    16 Notification only (platform only)  tanpa aksi kendali
    17 Missing MAC (platform only) device belum melaporkan MAC
    18 Too many variables (platform only)  9 variabel, batas 8
    19 No control access (platform only)   aktuator milik pengguna lain, dibagikan sebagai Viewer

Aman dijalankan berulang: device dan variabel yang sudah ada dipakai ulang; aturan yang
sudah ada dilewati kecuali --reset (aturan "Edge ..." pemilik dihapus lalu dibuat ulang).
Aturan 10 nonaktif bawaan karena mengirim email.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from devices.management.commands.fill_test_macs import token_mac
from devices.models import Device, Variable
from rules import schedules
from rules.models import Alarm, EcaCondition, EcaSpec, Rule, RuleAction, RuleModel, RuleSchedule
from sharing.models import DeviceShare

User = get_user_model()

SENSOR, ACTUATOR = Variable.Kind.SENSOR, Variable.Kind.ACTUATOR
NUMBER, BOOLEAN = Variable.ValueType.NUMBER, Variable.ValueType.BOOLEAN
ACT, CLR = "ACTIVATE", "CLEAR"

# nama -> (deskripsi, punya MAC?, [(key, label, unit, kind, type)])
DEVICES = {
    "Edge Greenhouse Node": ("Soil sensor and pump on one board.", True, [
        ("soil_moisture", "Soil moisture", "%", SENSOR, NUMBER), ("pump", "Pump", "", ACTUATOR, BOOLEAN)]),
    "Edge Soil Probe": ("Battery soil probe in the field.", True, [
        ("soil_moisture", "Soil moisture", "%", SENSOR, NUMBER)]),
    "Edge Weather Station": ("Temperature, humidity, rain and wind.", True, [
        ("temperature", "Temperature", "°C", SENSOR, NUMBER), ("humidity", "Humidity", "%", SENSOR, NUMBER),
        ("rain", "Raining", "", SENSOR, BOOLEAN), ("wind_speed", "Wind speed", "m/s", SENSOR, NUMBER)]),
    "Edge Pump Controller": ("Relay for the irrigation pump.", True, [("pump", "Pump", "", ACTUATOR, BOOLEAN)]),
    "Edge Valve Controller": ("Relay for the main water valve.", True, [("valve", "Valve open", "", ACTUATOR, BOOLEAN)]),
    "Edge Water Tank": ("Ultrasonic level sensor on the tank.", True, [
        ("water_level", "Water level", "%", SENSOR, NUMBER)]),
    "Edge Light Sensor": ("Ambient light.", True, [("lux", "Light", "lx", SENSOR, NUMBER)]),
    "Edge Lamp": ("Grow lamp relay.", True, [("lamp", "Lamp", "", ACTUATOR, BOOLEAN)]),
    "Edge Fan Controller": ("Exhaust fan; accepts the command \"boost\".", True, [
        ("fan", "Fan", "", ACTUATOR, BOOLEAN)]),
    "Edge Door Sensor": ("Reed switch on the store-room door.", True, [("door", "Door open", "", SENSOR, BOOLEAN)]),
    "Edge Siren": ("Alarm siren.", True, [("siren", "Siren", "", ACTUATOR, BOOLEAN)]),
    "Edge Multi-sensor Board": ("Board with nine analogue inputs.", True, [
        (f"input_{i}", f"Input {i}", "V", SENSOR, NUMBER) for i in range(1, 10)]),
    "Edge New Sensor": ("Just added; has not reported its MAC address yet.", False, [
        ("temperature", "Temperature", "°C", SENSOR, NUMBER)]),
}
# Milik --neighbour, dibagikan ke --owner sebagai Viewer (aturan 19).
NEIGHBOUR_DEVICE = ("Edge Neighbour Fan", "Fan owned by another user, shared with you as Viewer.",
                    [("fan", "Fan", "", ACTUATOR, BOOLEAN)])

GH, PROBE, WS, PUMP, VALVE, TANK = ("Edge Greenhouse Node", "Edge Soil Probe", "Edge Weather Station",
                                    "Edge Pump Controller", "Edge Valve Controller", "Edge Water Tank")
LIGHT, LAMP, FAN, DOOR, SIREN = "Edge Light Sensor", "Edge Lamp", "Edge Fan Controller", "Edge Door Sensor", "Edge Siren"
MULTI, NEW = "Edge Multi-sensor Board", "Edge New Sensor"


def on(device, key, value=1, run_on=ACT):
    return ("SET_ACTUATOR", run_on, {"target": (device, key), "value": value})


def off(device, key):
    return on(device, key, 0, CLR)


ALARM = ("ALARM", ACT, {"severity": "MAJOR", "message": "{rule}: {variable} = {value}"})

# "expect": (hasil AE, kode yang harus muncul di AE, kode yang harus muncul di SE) -- dipakai tes.
RULES = [
    {"name": "Edge 01 · Local loop",
     "description": "Sensor and actuator on the same board: the rule runs there without any radio message. "
                    "Both arrangements are the same.",
     "when": [[(GH, "soil_moisture", "<", 25)]], "clear": [[(GH, "soil_moisture", ">", 35)]],
     "actions": [on(GH, "pump"), off(GH, "pump")],
     "expect": ("FULL", [], ["SE_AT_ACTUATOR"])},
    {"name": "Edge 02 · One remote sensor",
     "description": "A soil probe controls a pump on another board. Actuator evaluates: the probe only broadcasts. "
                    "Sensor evaluates: the probe decides and sends its state; it must stay awake.",
     "when": [[(PROBE, "soil_moisture", "<", 20)]], "clear": [[(PROBE, "soil_moisture", ">", 30)]],
     "actions": [on(PUMP, "pump"), off(PUMP, "pump")],
     "expect": ("FULL", [], ["SE_EXTRA_CRITICAL", "SE_ALWAYS_ON"])},
    {"name": "Edge 03 · Two sensors, both required",
     "description": "Dry soil AND hot weather, from two boards. Both sensors are needed in either arrangement; "
                    "with Sensor evaluates the weather data needs two radio hops.",
     "when": [[(PROBE, "soil_moisture", "<", 20), (WS, "temperature", ">", 30)]],
     "clear": [[(PROBE, "soil_moisture", ">", 30)]], "duration": 30,
     "actions": [on(PUMP, "pump"), off(PUMP, "pump")],
     "expect": ("FULL", [], ["SE_RELAY"])},
    {"name": "Edge 04 · Either sensor",
     "description": "Very dry soil OR very hot weather; clears when it cools down. No single sensor is required "
                    "with Actuator evaluates, but with Sensor evaluates the soil probe decides and becomes a "
                    "device that must work.",
     "when": [[(PROBE, "soil_moisture", "<", 5)], [(WS, "temperature", ">", 35)]],
     "clear": [[(WS, "temperature", "<", 30)]], "max_age": 60,
     "actions": [on(PUMP, "pump"), off(PUMP, "pump")],
     "expect": ("FULL", [], ["SE_EXTRA_CRITICAL", "SE_RELAY"])},
    {"name": "Edge 05 · Timing",
     "description": "Lamp on after 60 s of darkness, at most once every 5 minutes; off when it is bright again "
                    "(hysteresis between 50 and 200 lx).",
     "when": [[(LIGHT, "lux", "<", 50)]], "clear": [[(LIGHT, "lux", ">", 200)]], "duration": 60, "cooldown": 300,
     "actions": [on(LAMP, "lamp"), off(LAMP, "lamp")],
     "expect": ("FULL", [], ["SE_ALWAYS_ON"])},
    {"name": "Edge 06 · Two actuators",
     "description": "Low tank: start the pump and close the valve, on two boards. Actuator evaluates: each board "
                    "decides by itself and may briefly disagree. Sensor evaluates: the tank sends one decision.",
     "when": [[(TANK, "water_level", "<", 20)]], "clear": [[(TANK, "water_level", ">", 80)]],
     "actions": [on(PUMP, "pump"), off(PUMP, "pump"), on(VALVE, "valve", 0), on(VALVE, "valve", 1, CLR)],
     "expect": ("FULL", ["AE_REPLICATED"], ["SE_MULTI_ACT"])},
    {"name": "Edge 07 · Sensor at the actuator",
     "description": "Soil sensor on the pump board plus rain from the weather station. The pump board measures "
                    "and controls, so it evaluates in both arrangements: the two plans are the same.",
     "when": [[(GH, "soil_moisture", "<", 25), (WS, "rain", "==", 0)]], "clear": [[(GH, "soil_moisture", ">", 35)]],
     "actions": [on(GH, "pump"), off(GH, "pump")],
     "expect": ("FULL", [], ["SE_AT_ACTUATOR"])},
    {"name": "Edge 08 · Evaluator policy",
     "description": "Bright, hot and dry: fan on. Sensor evaluates picks the board of the first condition (Light "
                    "Sensor); the \"most signals\" policy would pick the Weather Station (two variables).",
     "when": [[(LIGHT, "lux", ">", 800), (WS, "temperature", ">", 32), (WS, "humidity", "<", 40)]],
     "clear": [[(WS, "temperature", "<", 28)]],
     "actions": [on(FAN, "fan"), off(FAN, "fan")],
     "expect": ("FULL", [], ["SE_RELAY"])},
    {"name": "Edge 09 · Command action",
     "description": "Very hot: send the command \"boost\" to the fan controller. The firmware needs a handler "
                    "for this method.",
     "when": [[(WS, "temperature", ">", 35)]], "clear": [[(WS, "temperature", "<", 30)]],
     "actions": [("SEND_COMMAND", ACT, {"device": FAN, "method": "boost", "params": {"minutes": 10}})],
     "expect": ("FULL", ["COMMAND_HANDLER"], [])},
    {"name": "Edge 10 · Control plus notifications",
     "description": "Door open for 10 s: siren on (runs on the devices), plus an alarm and an email (stay on the "
                    "platform). Disabled by default because it sends email.",
     "when": [[(DOOR, "door", "==", 1)]], "duration": 10, "enabled": False,
     "actions": [on(SIREN, "siren"), off(SIREN, "siren"), ALARM,
                 ("EMAIL", ACT, {"message": "{rule}: the store-room door is open."})],
     "expect": ("PARTIAL", ["CLOUD_ACTION"], [])},
    {"name": "Edge 11 · Shared broadcast",
     "description": "Uses humidity like rule 08, but with a 30 s age limit. The Weather Station broadcasts "
                    "humidity once for both rules, every 10 s (the shorter heartbeat); see its device page.",
     "when": [[(WS, "humidity", ">", 85)]], "clear": [[(WS, "humidity", "<", 75)]], "max_age": 30,
     "actions": [on(FAN, "fan"), off(FAN, "fan")],
     "expect": ("FULL", [], [])},
    {"name": "Edge 12 · No age limit",
     "description": "\"Ignore values older than\" is 0: on a device the last value would be trusted forever "
                    "after the radio link is lost (warning).",
     "when": [[(TANK, "water_level", ">", 95)]], "clear": [[(TANK, "water_level", "<", 90)]], "max_age": 0,
     "actions": [on(VALVE, "valve", 0), on(VALVE, "valve", 1, CLR)],
     "expect": ("FULL", ["MAX_AGE_ZERO"], [])},
    {"name": "Edge 13 · Fractional equality",
     "description": "Wind speed equal to 12.5 m/s: \"=\" with a fraction may differ on a device that uses 32-bit "
                    "numbers (warning).",
     "when": [[(WS, "wind_speed", "==", 12.5)]],
     "actions": [on(SIREN, "siren"), off(SIREN, "siren")],
     "expect": ("FULL", ["FLOAT_EQ"], [])},
    {"name": "Edge 14 · Scheduled",
     "description": "Every day at 06:00 if the soil is dry. Schedules need an accurate clock, so this rule stays "
                    "on the platform.",
     "schedule": {"times": "06:00", "active_for": 600},
     "when": [[(PROBE, "soil_moisture", "<", 30)]],
     "actions": [on(PUMP, "pump"), off(PUMP, "pump")],
     "expect": ("NONE", ["EVENT_SCHEDULE"], ["EVENT_SCHEDULE"])},
    {"name": "Edge 15 · Device silent",
     "description": "Weather station sends nothing for 10 minutes: turn the fan on. A silent device cannot be told "
                    "apart from a lost radio link, so this stays on the platform.",
     "inactivity": (WS, 600),
     "actions": [on(FAN, "fan"), off(FAN, "fan")],
     "expect": ("NONE", ["EVENT_INACTIVITY"], ["EVENT_INACTIVITY"])},
    {"name": "Edge 16 · Notification only",
     "description": "Raises an alarm only. There is no control action for devices to run.",
     "when": [[(WS, "temperature", ">", 40)]], "clear": [[(WS, "temperature", "<", 35)]],
     "actions": [ALARM],
     "expect": ("NONE", ["NO_CONTROL"], ["NO_CONTROL"])},
    {"name": "Edge 17 · Missing MAC",
     "description": "Uses a board that has not reported its MAC address yet; devices find each other by MAC.",
     "when": [[(NEW, "temperature", ">", 30)]], "clear": [[(NEW, "temperature", "<", 26)]],
     "actions": [on(FAN, "fan"), off(FAN, "fan")],
     "expect": ("NONE", ["NO_MAC"], ["NO_MAC"])},
    {"name": "Edge 18 · Too many variables",
     "description": "Nine inputs in one rule; devices support at most eight.",
     "when": [[(MULTI, f"input_{i}", ">", 2.5) for i in range(1, 10)]],
     "actions": [on(LAMP, "lamp"), off(LAMP, "lamp")],
     "expect": ("NONE", ["LIMIT_SIGNALS"], ["LIMIT_SIGNALS"])},
    {"name": "Edge 19 · No control access",
     "description": "Controls a fan that another user shared with you as Viewer. Controlling it needs Operator "
                    "access.",
     "when": [[(WS, "temperature", ">", 30)]], "clear": [[(WS, "temperature", "<", 26)]],
     "actions": [on(NEIGHBOUR_DEVICE[0], "fan"), off(NEIGHBOUR_DEVICE[0], "fan")],
     "needs_neighbour": True,
     "expect": ("NONE", ["ACCESS"], ["ACCESS"])},
]


class Command(BaseCommand):
    help = "Create example rules and devices for micro rules (Run on devices)."

    def add_arguments(self, parser):
        parser.add_argument("--owner", default="siti", help="Username that owns the rules and devices.")
        parser.add_argument("--neighbour", default="budi",
                            help="Another username, owner of the fan shared as Viewer (rule 19).")
        parser.add_argument("--reset", action="store_true", help="Delete the owner's \"Edge ...\" rules first.")

    def handle(self, *args, **options):
        try:
            owner = User.objects.get(username=options["owner"])
        except User.DoesNotExist:
            raise CommandError(f"User {options['owner']!r} not found. Use --owner <your username>.")
        neighbour = User.objects.filter(username=options["neighbour"]).exclude(pk=owner.pk).first()
        self.devices = {}
        with transaction.atomic():
            for name, (description, with_mac, variables) in DEVICES.items():
                self.devices[name] = self.make_device(owner, name, description, with_mac, variables)
            if neighbour:
                name, description, variables = NEIGHBOUR_DEVICE
                device = self.make_device(neighbour, name, description, True, variables)
                DeviceShare.objects.update_or_create(device=device, user=owner,
                                                     defaults={"role": "VIEWER", "created_by": neighbour})
                self.devices[name] = device
        if options["reset"]:
            old = Rule.objects.filter(owner=owner, name__startswith="Edge ")
            Alarm.objects.filter(rule__in=old).delete()
            old.delete()

        from rules.edge.services import compile_and_store

        created = 0
        for spec in RULES:
            if spec.get("needs_neighbour") and not neighbour:
                self.stdout.write(f"Skipping {spec['name']}: user {options['neighbour']!r} not found.")
                continue
            if Rule.objects.filter(owner=owner, name=spec["name"]).exists():
                self.stdout.write(f"Skipping {spec['name']}: already exists (use --reset).")
                continue
            with transaction.atomic():
                rule = self.make_rule(owner, spec)
            plans = compile_and_store(rule, owner)
            verdicts = ", ".join(f"{s}: {p.verdict}" for s, p in plans.items()) or "feature off"
            self.stdout.write(f"Rule created: {rule.name} ({verdicts})")
            created += 1
        self.stdout.write(self.style.SUCCESS(f"Done. {created} example rule(s) created for {owner.username}."))
        self.stdout.write("Open Rules and look at \"Run on devices\" on each \"Edge ...\" rule.")

    def make_device(self, owner, name, description, with_mac, variables):
        device, created = Device.objects.get_or_create(owner=owner, name=name, defaults={"description": description})
        if with_mac and not device.mac_address:
            device.mac_address = token_mac(device.access_token)
            device.save(update_fields=["mac_address"])
        for key, label, unit, kind, value_type in variables:
            Variable.objects.get_or_create(device=device, key=key, defaults={
                "label": label, "unit": unit, "kind": kind, "value_type": value_type})
        if created:
            self.stdout.write(f"Device created: {name}{'' if with_mac else ' (no MAC address)'}")
        return device

    def variable(self, device_name, key):
        return Variable.objects.get(device=self.devices[device_name], key=key)

    def make_rule(self, owner, spec):
        rule = Rule.objects.create(owner=owner, name=spec["name"], description=spec["description"],
                                   model=RuleModel.ECA, is_enabled=spec.get("enabled", True), edge_enabled=True)
        if "inactivity" in spec:
            device_name, seconds = spec["inactivity"]
            EcaSpec.objects.create(rule=rule, event=EcaSpec.Event.INACTIVITY, device=self.devices[device_name],
                                   silence_seconds=seconds)
        else:
            eca = EcaSpec.objects.create(
                rule=rule, event=EcaSpec.Event.SCHEDULE if "schedule" in spec else EcaSpec.Event.TELEMETRY,
                duration_seconds=spec.get("duration", 0), cooldown_seconds=spec.get("cooldown", 0),
                max_age_seconds=spec.get("max_age", 300),
            )
            for role, groups in ((EcaCondition.Role.WHEN, spec.get("when", [])),
                                 (EcaCondition.Role.CLEAR, spec.get("clear", []))):
                for number, group in enumerate(groups, start=1):
                    for order, (device_name, key, op, threshold) in enumerate(group):
                        EcaCondition.objects.create(spec=eca, role=role, group=number, order=order,
                                                    variable=self.variable(device_name, key),
                                                    operator=op, threshold=threshold)
        for order, (kind, run_on, fields) in enumerate(spec["actions"]):
            action = RuleAction(rule=rule, kind=kind, run_on=run_on, order=order)
            if kind == "SET_ACTUATOR":
                action.target_variable = self.variable(*fields["target"])
                action.value = fields["value"]
            elif kind == "SEND_COMMAND":
                action.target_device = self.devices[fields["device"]]
                action.method, action.params = fields["method"], fields.get("params", {})
            elif kind == "ALARM":
                action.severity, action.message = fields["severity"], fields["message"]
            elif kind == "EMAIL":
                action.message = fields["message"]
            action.save()
        if "schedule" in spec:
            RuleSchedule.objects.create(rule=rule, kind=RuleSchedule.Kind.DAILY, times=spec["schedule"]["times"],
                                        active_for_seconds=spec["schedule"]["active_for"])
            schedules.reschedule(rule)
        return rule

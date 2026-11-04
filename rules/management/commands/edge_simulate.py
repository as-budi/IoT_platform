"""
python manage.py edge_simulate --rule 12 --strategy actuator,sensor --loss 0,0.05,0.2 \
       --kill "S@600-1200" --duration 3600 --runs 10 --out hasil.csv

Menjalankan simulator micro rule (rules/edge/sim.py) untuk satu aturan, dengan jejak
sensor sintetis (random walk di sekitar ambang). Satu baris CSV per kombinasi
strategi x loss x kegagalan x run x aktuator.

--kill  "<device>@<mati>-<hidup>" (detik); <device> = nama atau id device. Tanpa
        "-<hidup>" device mati sampai akhir. Boleh diulang. Setiap --kill menjadi satu
        skenario sendiri, ditambah skenario tanpa kegagalan.
--sample-file  JSON {"<key variabel>": [[detik, nilai], ...]} untuk memakai jejak nyata.
"""
import csv
import json

from django.core.management.base import BaseCommand, CommandError

from rules.edge.analyze import analyze_rule
from rules.edge.ir import device_names
from rules.edge.placement import STRATEGIES
from rules.edge.sim import SimConfig, simulate, synthetic_traces
from rules.models import Rule

COLUMNS = ["strategy", "policy", "loss", "failure", "run", "actuator", "wrong_ms", "wrong_ratio",
           "truth_transitions", "transitions", "missed", "spurious", "mean_latency_ms", "max_latency_ms",
           "divergence_ms", "data_sent", "state_sent", "delivered", "lost"]


def parse_kill(text, ir, names):
    try:
        who, window = text.rsplit("@", 1)
        start, _, stop = window.partition("-")
        start, stop = float(start), (float(stop) if stop else None)
    except ValueError:
        raise CommandError(f"--kill {text!r}: use <device>@<down>-<up>")
    for device in ir["devices"]:
        if who in (names.get(device["p"]), str(device["device"])):
            return device["p"], start, stop
    raise CommandError(f"--kill {text!r}: {who!r} is not a device of this rule")


class Command(BaseCommand):
    help = "Simulate a rule as micro rules under frame loss and device failures."

    def add_arguments(self, parser):
        parser.add_argument("--rule", type=int, required=True)
        parser.add_argument("--strategy", default="actuator,sensor")
        parser.add_argument("--policy", default=None, help="SE policy: primary, most_signals, min_traffic.")
        parser.add_argument("--loss", default="0,0.05,0.2")
        parser.add_argument("--kill", action="append", default=[])
        parser.add_argument("--duration", type=int, default=3600)
        parser.add_argument("--sample", type=int, default=5, help="Seconds between synthetic samples.")
        parser.add_argument("--sample-file")
        parser.add_argument("--runs", type=int, default=1)
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--out")

    def handle(self, *args, **o):
        rule = Rule.objects.filter(pk=o["rule"]).first()
        if rule is None:
            raise CommandError("Rule not found.")
        strategies = [s for s in o["strategy"].split(",") if s]
        if any(s not in STRATEGIES for s in strategies):
            raise CommandError(f"--strategy must be from {', '.join(STRATEGIES)}")
        losses = [float(x) for x in o["loss"].split(",") if x]
        handle = open(o["out"], "w", newline="", encoding="utf-8") if o.get("out") else self.stdout
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        rows = 0
        for strategy in strategies:
            report = analyze_rule(rule, strategy)
            if report.plan is None:
                raise CommandError("This rule cannot run on devices: "
                                   + "; ".join(i["text"] for i in report.items if i["level"] == "block"))
            ir, plan = report.ir, report.plan
            if strategy == "sensor" and o.get("policy"):
                from rules.edge.compile import compile_plan

                plan = compile_plan(ir, strategy, policy=o["policy"])
            names = device_names(ir)
            scenarios = [("none", [])] + [(k, [parse_kill(k, ir, names)]) for k in o["kill"]]
            for run in range(o["runs"]):
                traces = self.traces(ir, o, run)
                for loss in losses:
                    for label, failures in scenarios:
                        config = SimConfig(duration_s=o["duration"], loss=loss, failures=failures,
                                           seed=o["seed"] * 1000 + run)
                        result = simulate(ir, plan, traces, config)
                        for actuator, values in result["actuators"].items():
                            writer.writerow({
                                "strategy": strategy, "policy": plan["policy"] or "", "loss": loss,
                                "failure": label, "run": run, "actuator": names.get(actuator, actuator),
                                **values, **{k: result[k] for k in
                                             ("divergence_ms", "data_sent", "state_sent", "delivered", "lost")},
                            })
                            rows += 1
        if o.get("out"):
            handle.close()
            self.stdout.write(self.style.SUCCESS(f"{rows} rows written to {o['out']}"))

    def traces(self, ir, o, run):
        if not o.get("sample_file"):
            return synthetic_traces(ir, o["duration"], o["sample"], seed=o["seed"] * 1000 + run)
        with open(o["sample_file"], encoding="utf-8") as handle:
            data = json.load(handle)
        traces = {}
        for signal in ir["signals"]:
            points = data.get(signal["key"], [])
            traces[signal["s"]] = [(float(t) * 1000, float(v)) for t, v in points]
        return traces

"""
python manage.py edge_report [--out laporan.csv] [--owner username]

Satu baris per aturan ECA per strategi (AE, SE): verdict, alasan yang memblokir, ukuran
fragmen, dan metrik analitik (rules/edge/metrics.py). Untuk tabel di paper.
"""
import csv

from django.core.management.base import BaseCommand

from rules.edge.analyze import analyze_rule
from rules.edge.metrics import plan_metrics
from rules.edge.placement import STRATEGIES
from rules.models import Rule, RuleModel

COLUMNS = ["rule", "name", "strategy", "verdict", "blocked_by", "devices", "signals", "conditions",
           "fragments", "total_bytes", "max_bytes", "critical_devices", "max_hops", "frames_per_minute",
           "divergence", "always_on_sensor"]


class Command(BaseCommand):
    help = "Report how ECA rules would be split into micro rules (both strategies)."

    def add_arguments(self, parser):
        parser.add_argument("--out", help="CSV file (default: standard output).")
        parser.add_argument("--owner", help="Only rules of this username.")

    def handle(self, *args, **options):
        rules = Rule.objects.filter(model=RuleModel.ECA).select_related("owner").order_by("pk")
        if options.get("owner"):
            rules = rules.filter(owner__username=options["owner"])
        handle = open(options["out"], "w", newline="", encoding="utf-8") if options.get("out") else self.stdout
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        count = 0
        for rule in rules:
            for strategy in STRATEGIES:
                report = analyze_rule(rule, strategy)
                ir = report.ir or {}
                row = {
                    "rule": rule.pk, "name": rule.name, "strategy": strategy, "verdict": report.verdict,
                    "blocked_by": " ".join(sorted({i["code"] for i in report.items if i["level"] == "block"})),
                    "devices": len(ir.get("devices", [])), "signals": len(ir.get("signals", [])),
                    "conditions": sum(len(g) for part in ("when", "clear") for g in ir.get(part, [])),
                }
                if report.plan:
                    metrics = plan_metrics(ir, report.plan)
                    row.update({key: metrics[key] for key in COLUMNS if key in metrics})
                writer.writerow(row)
                count += 1
        if options.get("out"):
            handle.close()
            self.stdout.write(self.style.SUCCESS(f"{count} rows written to {options['out']}"))

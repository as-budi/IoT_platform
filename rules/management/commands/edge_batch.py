"""
python manage.py edge_batch --rules 1000 --out batch.csv

Simulasi massal micro rule: setiap aturan disimulasikan dengan strategi AE dan SE pada semua
tingkat loss dan skenario kegagalan, lalu satu baris CSV per aktuator ditulis bersama ciri
aturannya. Penjelasan: docs/metrik_dan_simulator.md bagian 12.

Sumber aturan:
    --source random (bawaan)  aturan acak yang dibangkitkan (rules/edge/batch.py random_rule),
                              tanpa database; sama untuk --seed yang sama
    --source db               semua aturan ECA di database yang bisa dijalankan di device

Skenario kegagalan (--failures, dipisah koma):
    none            tanpa kegagalan
    se_evaluator    device yang menjadi evaluator SE mati (hanya bila bukan aktuator)
    random_sensor   satu device sensor acak (bukan aktuator) mati
Device mati pada --fail-at detik (bawaan: 1/6 durasi) dan tidak hidup lagi. Pada setiap
skenario, AE dan SE kehilangan device yang SAMA dan memakai jejak sensor yang SAMA.

Contoh:
    python manage.py edge_batch --rules 1000 --processes 8 --out batch.csv
    python manage.py edge_batch --rules 200 --duration 1800 --loss 0,0.1 --failures none,random_sensor
    python manage.py edge_batch --source db --runs 5 --out db.csv
"""
import csv
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from django.core.management.base import BaseCommand, CommandError

from rules.edge import batch


class Command(BaseCommand):
    help = "Simulate many rules as micro rules (both strategies) and write one CSV."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=["random", "db"], default="random")
        parser.add_argument("--rules", type=int, default=1000, help="Number of random rules.")
        parser.add_argument("--seed", type=int, default=2026)
        parser.add_argument("--loss", default="0,0.05,0.2")
        parser.add_argument("--failures", default="none,se_evaluator")
        parser.add_argument("--runs", type=int, default=1, help="Runs per rule (different sensor data).")
        parser.add_argument("--duration", type=int, default=3600)
        parser.add_argument("--sample", type=int, default=5, help="Seconds between synthetic samples.")
        parser.add_argument("--fail-at", type=float, default=None)
        parser.add_argument("--policy", default=None, help="SE policy: primary, most_signals, min_traffic.")
        parser.add_argument("--max-signals", type=int, default=3)
        parser.add_argument("--max-groups", type=int, default=3)
        parser.add_argument("--max-conditions", type=int, default=3)
        parser.add_argument("--p-colocated", type=float, default=0.3, help="Chance the actuator also hosts a sensor.")
        parser.add_argument("--p-two-actuators", type=float, default=0.25)
        parser.add_argument("--processes", type=int, default=1, help="Parallel worker processes.")
        parser.add_argument("--out", required=True)

    def handle(self, *args, **o):
        failures = [f for f in o["failures"].split(",") if f]
        if any(f not in batch.FAILURES for f in failures):
            raise CommandError(f"--failures must be from {', '.join(batch.FAILURES)}")
        options = {
            "losses": [float(x) for x in o["loss"].split(",") if x], "failures": failures, "runs": o["runs"],
            "duration": o["duration"], "sample": o["sample"], "fail_at": o["fail_at"], "policy": o["policy"],
            "max_signals": o["max_signals"], "max_groups": o["max_groups"], "max_conditions": o["max_conditions"],
            "p_colocated": o["p_colocated"], "p_two_actuators": o["p_two_actuators"],
        }
        started = time.time()
        with open(o["out"], "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=batch.COLUMNS, extrasaction="ignore")
            writer.writeheader()
            if o["source"] == "db":
                done, skipped, rows = self.from_db(writer, options, o["seed"])
            else:
                done, skipped, rows = self.from_random(writer, options, o)
        self.stdout.write(self.style.SUCCESS(
            f"{done} rule(s) simulated, {skipped} skipped, {rows} rows written to {o['out']} "
            f"in {time.time() - started:.0f} s."))

    def progress(self, done, total, started):
        if done % 50 == 0 or done == total:
            rate = done / max(time.time() - started, 1e-6)
            left = (total - done) / rate if rate else 0
            sys.stderr.write(f"\r{done}/{total} rules, about {left:.0f} s left   ")
            sys.stderr.flush()

    def from_random(self, writer, options, o):
        jobs = [(o["seed"], i, options) for i in range(o["rules"])]
        done = skipped = rows = 0
        started = time.time()
        if o["processes"] > 1:
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
            with ProcessPoolExecutor(max_workers=o["processes"], initializer=batch.init_worker) as pool:
                results = pool.map(batch.run_random, jobs, chunksize=4)
                for result, reason in results:
                    writer.writerows(result); rows += len(result); done += 1; skipped += reason is not None
                    self.progress(done, len(jobs), started)
        else:
            for job in jobs:
                result, reason = batch.run_random(job)
                writer.writerows(result); rows += len(result); done += 1; skipped += reason is not None
                self.progress(done, len(jobs), started)
        sys.stderr.write("\n")
        return done - skipped, skipped, rows

    def from_db(self, writer, options, seed):
        from rules.edge.ir import from_rule
        from rules.models import Rule, RuleModel

        done = skipped = rows = 0
        simulation = {k: options[k] for k in ("losses", "failures", "runs", "duration", "sample", "fail_at", "policy")}
        for rule in Rule.objects.filter(model=RuleModel.ECA).order_by("pk"):
            if getattr(rule, "eca", None) is None:
                continue
            result, reason = batch.simulate_rule(from_rule(rule), seed=seed, **simulation)
            if reason:
                skipped += 1
                self.stdout.write(f"Skipping rule {rule.pk} ({rule.name}): {reason}")
                continue
            writer.writerows(result); rows += len(result); done += 1
        return done, skipped, rows

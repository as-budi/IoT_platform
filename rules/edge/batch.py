"""
Simulasi massal micro rule: banyak aturan sekaligus (lihat docs/metrik_dan_simulator.md bagian 12).

Dua sumber aturan:
    random_rule(seed, i)   aturan acak yang masuk akal (tanpa database), deterministik dari (seed, i)
    aturan ECA di database (perintah edge_batch --source db)

simulate_rule(ir, ...) menjalankan satu aturan pada semua kombinasi strategi x loss x skenario
kegagalan x run, dan mengembalikan baris-baris CSV (satu per aktuator) beserta ciri aturan,
sehingga hasil bisa dianalisis menurut bentuk aturan (jumlah sensor, histeresis, dsb.).
"""
import random

from .analyze import analyze_ir_full
from .metrics import critical_sources, plan_metrics
from .placement import ACTUATOR, SENSOR, STRATEGIES
from .samples import make_ir, set_action
from .sim import SimConfig, simulate, synthetic_traces

OPERATORS = ["<", "<=", ">", ">=", "==", "!="]
OPERATOR_WEIGHTS = [30, 15, 30, 15, 5, 5]      # "=" dan "≠" jarang dipakai pada nilai kontinu
FAILURES = ("none", "se_evaluator", "random_sensor")

FEATURES = ["rule", "n_signals", "n_sensor_devices", "n_actuators", "n_groups", "n_conditions", "has_clear",
            "duration", "cooldown", "max_age", "colocated", "required_sensors",
            "ae_critical", "se_critical", "ae_max_hops", "se_max_hops", "se_evaluator_is_actuator"]
RESULTS = ["strategy", "loss", "failure", "failed_required", "run", "actuator", "wrong_ms", "wrong_ratio",
           "truth_transitions", "transitions", "missed", "spurious", "mean_latency_ms", "max_latency_ms",
           "divergence_ms", "data_sent", "state_sent", "delivered", "lost"]
COLUMNS = FEATURES + RESULTS


def random_rule(seed, index, max_signals=3, max_groups=3, max_conditions=3, p_colocated=0.3, p_two_actuators=0.25):
    """Satu aturan ECA acak, deterministik dari (seed, index).

    - 1..max_signals variabel; setiap variabel baru diletakkan di device baru (75 %) atau di device
      sensor yang sudah ada (25 %).
    - Aktuator pertama di device sensor yang sudah ada dengan peluang p_colocated, selain itu device
      sendiri; aktuator kedua (peluang p_two_actuators) selalu device sendiri.
    - Ekspresi aktif: 1..max_groups grup OR x 1..max_conditions kondisi AND; ambang 20..80.
    - Histeresis pada 50 % aturan: kebalikan kondisi pertama dengan jarak 5..15.
    """
    rng = random.Random(seed * 1_000_003 + index)
    n_signals = rng.randint(1, max_signals)
    device_of, devices = [], 0
    for _ in range(n_signals):
        if devices == 0 or rng.random() < 0.75:
            devices += 1
            device_of.append(devices)
        else:
            device_of.append(rng.randint(1, devices))
    groups = []
    for _ in range(rng.randint(1, max_groups)):
        chosen = rng.sample(range(1, n_signals + 1), min(rng.randint(1, max_conditions), n_signals))
        groups.append([(s, rng.choices(OPERATORS, OPERATOR_WEIGHTS)[0], rng.randint(20, 80)) for s in chosen])
    used = sorted({s for group in groups for s, _, _ in group})
    renumber = {s: i for i, s in enumerate(used, start=1)}
    groups = [[(renumber[s], op, th) for s, op, th in group] for group in groups]
    signals = [(device_of[s - 1], f"v{s}") for s in used]
    sensor_devices = sorted({p for p, _ in signals})
    clear = []
    s, op, th = groups[0][0]
    if rng.random() < 0.5 and op not in ("==", "!="):
        gap = rng.randint(5, 15)
        clear = [[(s, ">", th + gap)]] if op in ("<", "<=") else [[(s, "<", th - gap)]]
    next_device = max(sensor_devices) + 1
    if rng.random() < p_colocated:
        first = rng.choice(sensor_devices)
    else:
        first, next_device = next_device, next_device + 1
    actions = [set_action("activate", first, "out1", 1), set_action("clear", first, "out1", 0)]
    if rng.random() < p_two_actuators:
        actions += [set_action("activate", next_device, "out2", 1), set_action("clear", next_device, "out2", 0)]
    return make_ir(signals, when=groups, clear=clear, actions=actions,
                   duration=rng.choice([0, 0, 10, 30, 60]), cooldown=rng.choice([0, 0, 0, 30]),
                   max_age=rng.choice([60, 120, 300]), rule=index + 1)


def features(ir, plans):
    sensors = sorted({s["p"] for s in ir["signals"]})
    actuators = sorted({a["p"] for a in ir["actions"]})
    ae = plan_metrics(ir, plans[ACTUATOR])
    se = plan_metrics(ir, plans[SENSOR])
    return {
        "rule": ir["rule"], "n_signals": len(ir["signals"]), "n_sensor_devices": len(sensors),
        "n_actuators": len(actuators), "n_groups": len(ir["when"]),
        "n_conditions": sum(len(g) for g in ir["when"] + ir["clear"]), "has_clear": bool(ir["clear"]),
        "duration": ir["timing"]["duration"], "cooldown": ir["timing"]["cooldown"], "max_age": ir["timing"]["max_age"],
        "colocated": any(p in sensors for p in actuators), "required_sensors": len(critical_sources(ir)),
        "ae_critical": ae["critical_devices"], "se_critical": se["critical_devices"],
        "ae_max_hops": ae["max_hops"], "se_max_hops": se["max_hops"],
        "se_evaluator_is_actuator": plans[SENSOR]["evaluators"][0] in actuators,
    }


def failure_target(ir, plans, failure, rng):
    """Device yang dimatikan untuk skenario `failure`, atau None bila skenario tidak berlaku."""
    actuators = {a["p"] for a in ir["actions"]}
    if failure == "se_evaluator":
        e = plans[SENSOR]["evaluators"][0]
        return None if e in actuators else e        # evaluator = aktuator: tidak ada yang bisa dibandingkan
    if failure == "random_sensor":
        candidates = sorted({s["p"] for s in ir["signals"]} - actuators)
        return rng.choice(candidates) if candidates else None
    return None


def simulate_rule(ir, losses=(0.0, 0.05, 0.2), failures=("none", "se_evaluator"), runs=1, duration=3600,
                  sample=5, fail_at=None, seed=0, policy=None):
    """Semua kombinasi untuk satu aturan. Mengembalikan (baris, alasan dilewati atau None)."""
    plans = {}
    for strategy in STRATEGIES:
        report = analyze_ir_full(ir, strategy, policy=policy)
        if report.plan is None:
            return [], "; ".join(i["code"] for i in report.items if i["level"] == "block")
        plans[strategy] = report.plan
    base = features(ir, plans)
    required = critical_sources(ir)
    fail_at = duration / 6 if fail_at is None else fail_at
    rng = random.Random(seed * 7919 + ir["rule"])
    rows = []
    for run in range(runs):
        run_seed = seed * 1000 + ir["rule"] * 37 + run
        traces = synthetic_traces(ir, duration, sample, seed=run_seed)
        for failure in failures:
            target = None if failure == "none" else failure_target(ir, plans, failure, rng)
            if failure != "none" and target is None:
                continue
            for loss in losses:
                config = SimConfig(duration_s=duration, loss=loss, seed=run_seed,
                                   failures=[(target, fail_at, None)] if target else [])
                for strategy in STRATEGIES:
                    result = simulate(ir, plans[strategy], traces, config)
                    for actuator, values in result["actuators"].items():
                        rows.append({
                            **base, "strategy": strategy, "loss": loss, "failure": failure,
                            "failed_required": (target in required) if target else "",
                            "run": run, "actuator": actuator, **values,
                            **{k: result[k] for k in ("divergence_ms", "data_sent", "state_sent", "delivered", "lost")},
                        })
    return rows, None


def run_random(args):
    """Untuk ProcessPoolExecutor: args = (seed, index, opsi)."""
    seed, index, options = args
    generator = {k: options[k] for k in ("max_signals", "max_groups", "max_conditions", "p_colocated", "p_two_actuators")}
    ir = random_rule(seed, index, **generator)
    simulation = {k: options[k] for k in ("losses", "failures", "runs", "duration", "sample", "fail_at", "policy")}
    return simulate_rule(ir, seed=seed, **simulation)


def init_worker():
    import django

    django.setup()

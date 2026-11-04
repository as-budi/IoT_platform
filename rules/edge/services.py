"""
Menyimpan dan menyiapkan rencana micro rule untuk halaman web (tahap 26).

    compile_and_store(rule, user)   dipanggil setelah aturan ECA disimpan
    analyze(rule, strategy)         analisis + batas per device lintas aturan
    rule_context(rule)              data bagian "Edge plan" di halaman aturan
    device_context(device)          data kartu "Edge rules" di halaman device
"""
from django.db import transaction
from django.db.models import Max

from ..models import EdgeFragment, EdgePlan
from . import analyze as analysis
from .compile import publish_table
from .diagram import svg
from .ir import device_names, digest
from .limits import enabled, limits
from .metrics import plan_metrics
from .placement import LABELS, STRATEGIES
from .wire import key_hash


def deployed_fragments(device, exclude_rule=None):
    """Fragmen device ini dari rencana terkini aturan lain yang memakai strategi pilihannya."""
    fragments = (
        EdgeFragment.objects.filter(device=device, plan__is_current=True, plan__rule__edge_enabled=True)
        .select_related("plan__rule")
    )
    if exclude_rule is not None:
        fragments = fragments.exclude(plan__rule=exclude_rule)
    return [f for f in fragments if f.plan.strategy == f.plan.rule.edge_strategy]


def budget_items(rule, report, lim=None):
    """Batas per device dihitung lintas SEMUA aturan edge (rencana 26 bagian 5c)."""
    lim = lim or limits()
    if report.plan is None:
        return []
    from devices.models import Device

    names = device_names(report.ir)
    pk_of = {d["p"]: d["device"] for d in report.ir["devices"]}
    devices = Device.objects.in_bulk(list(pk_of.values()))
    items = []
    for p, fragment in report.plan["fragments"].items():
        device = devices.get(pk_of[p])
        if device is None:
            continue
        others = [f.payload for f in deployed_fragments(device, exclude_rule=rule)]
        payloads = others + [fragment]
        rules = len({f["rule"] for f in payloads})
        keys = {e["key"] for f in payloads for e in f.get("publish", [])}
        sources = {s["mac"] for f in payloads for s in f.get("sources", [])}
        name = names.get(p)
        if rules > lim["MAX_RULES_PER_DEVICE"]:
            items.append(analysis.item("DEVICE_BUDGET", analysis.WARN,
                                       f"{name} would hold {rules} device rules; at most "
                                       f"{lim['MAX_RULES_PER_DEVICE']} are supported."))
        if len(keys) > lim["MAX_PUBLISHED_SIGNALS"]:
            items.append(analysis.item("DEVICE_BUDGET", analysis.WARN,
                                       f"{name} would broadcast {len(keys)} variables for all its device rules; "
                                       f"at most {lim['MAX_PUBLISHED_SIGNALS']} are supported."))
        if len(sources) > lim["MAX_SOURCES"]:
            items.append(analysis.item("DEVICE_BUDGET", analysis.WARN,
                                       f"{name} would listen to {len(sources)} devices for all its device rules; "
                                       f"at most {lim['MAX_SOURCES']} are supported."))
        if len({key_hash(k) for k in keys}) != len(keys):
            items.append(analysis.item("KEY_HASH", analysis.BLOCK,
                                       f"Two variable keys broadcast by {name} share the same short code. "
                                       "Rename one of them."))
    return items


def analyze(rule, strategy):
    report = analysis.analyze_rule(rule, strategy)
    extra = budget_items(rule, report)
    if extra:
        report.items += extra
        report.verdict = analysis.verdict_of(report.items, report.ir)
    return report


def _rev(report):
    if report.plan:
        return report.plan["rev"]
    return digest({"ir": report.ir, "items": report.items})


def store(rule, strategy, report, user=None):
    """Simpan revisi baru bila isinya berubah. Mengembalikan EdgePlan terkini."""
    rev = _rev(report)
    current = EdgePlan.objects.filter(rule=rule, strategy=strategy, is_current=True).first()
    if current and current.rev == rev and current.verdict == report.verdict:
        return current
    with transaction.atomic():
        EdgePlan.objects.filter(rule=rule, strategy=strategy, is_current=True).update(is_current=False)
        last = EdgePlan.objects.filter(rule=rule, strategy=strategy).aggregate(n=Max("revision"))["n"] or 0
        plan = EdgePlan.objects.create(
            rule=rule, strategy=strategy, revision=last + 1, rev=rev, verdict=report.verdict,
            report=report.items, ir=report.ir or {},
            metrics=plan_metrics(report.ir, report.plan) if report.plan else {},
            is_current=True, created_by=user if user and user.is_authenticated else None,
        )
        if report.plan:
            pk_of = {d["p"]: d["device"] for d in report.ir["devices"]}
            EdgeFragment.objects.bulk_create([
                EdgeFragment(plan=plan, device_id=pk_of[p], role=fragment["role"], payload=fragment,
                             size_bytes=report.plan["sizes"][p])
                for p, fragment in report.plan["fragments"].items()
            ])
    return plan


def compile_and_store(rule, user=None):
    """Setelah aturan disimpan: buat rencana kedua strategi bila aturan memakai micro rule."""
    if not enabled() or not getattr(rule, "edge_enabled", False) or rule.model != "ECA":
        return {}
    return {strategy: store(rule, strategy, analyze(rule, strategy), user) for strategy in STRATEGIES}


def rule_context(rule):
    """Data untuk rules/includes/edge_plan.html."""
    lim = limits()
    current = {p.strategy: p for p in EdgePlan.objects.filter(rule=rule, is_current=True)}
    tabs = []
    for strategy in STRATEGIES:
        report = analyze(rule, strategy)
        names = device_names(report.ir) if report.ir else {}
        stored = current.get(strategy)
        rows = []
        if report.plan:
            pk_of = {d["p"]: d["device"] for d in report.ir["devices"]}
            for p, fragment in report.plan["fragments"].items():
                size = report.plan["sizes"][p]
                evaluator = fragment.get("evaluator")
                rows.append({
                    "p": p, "device_id": pk_of[p], "name": names.get(p), "role": fragment["role"],
                    "size": size, "percent": min(100, round(100 * size / lim["MAX_FRAGMENT_BYTES"])),
                    # Evaluator mendengar frame DATA dari sensornya; aktuator SE mendengar frame
                    # STATE dari sensor evaluator.
                    "sources": [names.get(s["p"]) for s in fragment.get("sources", [])]
                    + ([names.get(evaluator["p"])] if evaluator else []),
                    "listens_state": bool(evaluator),
                    "publish": [e["key"] for e in fragment.get("publish", [])],
                    "frame": report.plan["data_frames"].get(p),
                    # SE: evaluator memancarkan keputusan (frame STATE) ke aktuator.
                    "emit": fragment.get("emit"),
                    "state_frame": report.plan["state_frame"] if fragment.get("emit") else None,
                    "json": analysis_json(fragment),
                })
        tabs.append({
            "strategy": strategy,
            "label": LABELS[strategy],
            "is_selected": strategy == rule.edge_strategy,
            "report": report,
            "items": report.sorted_items(),
            "metrics": plan_metrics(report.ir, report.plan) if report.plan else None,
            "diagram": svg(report.ir, report.plan, names) if report.plan else "",
            "rows": rows,
            "rev": _rev(report) if report.ir else "",
            "stored": stored,
            "stale": bool(rule.edge_enabled and (stored is None or stored.rev != _rev(report))),
        })
    return {"tabs": tabs, "limits": lim, "edge_enabled": rule.edge_enabled}


def analysis_json(obj):
    import json

    return json.dumps(obj, indent=2, ensure_ascii=False)


def device_context(device):
    """Kartu "Edge rules on this device": peran device di setiap aturan + tabel publikasi."""
    fragments = deployed_fragments(device)
    rows = [{"rule": f.plan.rule, "role": f.role, "revision": f.plan.revision, "rev": f.plan.rev,
             "strategy": f.plan.get_strategy_display(), "size": f.size_bytes} for f in fragments]
    table = publish_table(device.mac_address, [f.payload for f in fragments]) if fragments else None
    return {"rows": sorted(rows, key=lambda r: r["rule"].name), "table": table}

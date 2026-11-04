"""
Metrik analitik untuk membandingkan strategi AE dan SE (rencana 26b bagian 4-5).
Dihitung dari IR dan rencana saja, tanpa simulasi.

    critical_devices   device yang bila mati menghentikan aksi aturan: aktuator, sumber
                       yang muncul di SEMUA grup WHEN, dan (SE) sensor evaluator
    max_hops           hop radio terpanjang dari sensor ke tempat aksi
    frames_per_minute  frame heartbeat per menit (DATA + STATE), keadaan tenang
    divergence         apakah dua aktuator bisa sementara berbeda keputusan (AE tereplikasi)
    always_on_sensor   apakah sebuah sensor harus selalu menyala (SE)
"""
from .placement import SENSOR


def critical_sources(ir):
    groups = [{signal_p(ir, s) for s, _, _ in group} for group in ir["when"]]
    return set.intersection(*groups) if groups else set()


def signal_p(ir, s):
    for signal in ir["signals"]:
        if signal["s"] == s:
            return signal["p"]
    return None


def plan_metrics(ir, plan):
    evals, acts = plan["evaluators"], plan["actuators"]
    se = plan["strategy"] == SENSOR
    critical = set(acts) | critical_sources(ir)
    if se:
        critical |= set(evals)
    hops = 0
    for signal in ir["signals"]:
        for a in acts:
            if se and evals:
                e = evals[0]
                hop = (0 if signal["p"] == e else 1) + (0 if a == e else 1)
            else:
                hop = 0 if signal["p"] == a else 1
            hops = max(hops, hop)
    per_minute = sum(60 / fragment["publish"][0]["heartbeat"]
                     for fragment in plan["fragments"].values() if fragment.get("publish"))
    if plan["state_frame"]:
        per_minute += 60 / plan["fragments"][evals[0]]["emit"]["heartbeat"]
    sizes = list(plan["sizes"].values())
    return {
        "critical_devices": len(critical),
        "critical": sorted(critical),
        "max_hops": hops,
        "frames_per_minute": round(per_minute, 2),
        "divergence": (not se) and len(evals) > 1,
        "always_on_sensor": se and bool(evals) and evals[0] not in acts,
        "fragments": len(sizes),
        "total_bytes": sum(sizes),
        "max_bytes": max(sizes) if sizes else 0,
    }

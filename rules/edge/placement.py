"""
Di device mana sebuah aturan dievaluasi.

    AE (strategi "actuator", rencana utama): setiap device aktuator mengevaluasi aturan
       sendiri (evaluasi tereplikasi) dan hanya menjalankan aksinya sendiri.
    SE (strategi "sensor", pembanding tahap 26b): satu device sensor mengevaluasi aturan
       dan memancarkan keadaan (frame STATE); aktuator hanya menerapkannya.

Kebijakan memilih sensor evaluator pada SE untuk aturan multi-sensor:
    primary       device dari kondisi pertama di grup pertama ("device asal" di cloud)
    most_signals  device yang menyumbang sinyal terbanyak
    min_traffic   device dengan laju sampel tertinggi (butuh `rates` {s: sampel/detik})
Tie-break selalu nomor device (p) terkecil.
"""
ACTUATOR, SENSOR = "actuator", "sensor"
STRATEGIES = (ACTUATOR, SENSOR)
LABELS = {ACTUATOR: "Actuator evaluates", SENSOR: "Sensor evaluates"}
POLICIES = ("primary", "most_signals", "min_traffic")


def actuators(ir):
    return sorted({action["p"] for action in ir["actions"]})


def sources(ir):
    return sorted({signal["p"] for signal in ir["signals"]})


def evaluators(ir, strategy=ACTUATOR, policy="primary", rates=None):
    acts = actuators(ir)
    if not acts:
        return []
    if strategy == ACTUATOR:
        return acts
    if strategy != SENSOR:
        raise ValueError(f"unknown strategy {strategy!r}")
    srcs = sources(ir)
    if not srcs:
        return [acts[0]]
    both = [p for p in srcs if p in acts]
    if both:
        return [both[0]]      # sumber sekaligus aktuator: evaluasi di sana, tanpa hop tambahan
    if len(srcs) == 1:
        return srcs
    signals_of = {p: [s for s in ir["signals"] if s["p"] == p] for p in srcs}
    if policy == "primary" and ir.get("primary"):
        for signal in ir["signals"]:
            if signal["s"] == ir["primary"]:
                return [signal["p"]]
    if policy == "min_traffic" and rates:
        score = {p: sum(rates.get(s["s"], 0) for s in signals_of[p]) for p in srcs}
    else:
        score = {p: len(signals_of[p]) for p in srcs}
    best = max(score.values())
    return [min(p for p in srcs if score[p] == best)]

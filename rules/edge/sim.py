"""
Simulator kejadian diskret untuk membandingkan strategi AE dan SE (rencana 26b bagian 7).

Device menjalankan fragmen hasil compiler dengan evaluator referensi (evaluator.py),
pengirim STATE (emitter.py), dan penerima STATE (actuator.py), yaitu kode yang sama
dengan yang menjadi acuan firmware. Yang disimulasikan hanya udara dan waktu:

    - broadcast ESP-NOW: setiap penerima kehilangan frame dengan peluang `loss`
      (independen), latensi acak antara latency_ms[0] dan latency_ms[1];
    - device bisa dimatikan dan dihidupkan lagi (failures); saat hidup lagi keadaan
      evaluator hilang (restart), counter frame dianggap tersimpan di NVS;
    - "kebenaran" = aturan yang menerima semua sampel seketika tanpa kehilangan
      (sama dengan mesin cloud bila platform sehat dan jaringan sempurna).

Keluaran per aktuator: lama berada di keadaan yang salah, transisi yang terlewat,
latensi transisi, transisi palsu; ditambah jumlah frame dan lama aktuator saling
berbeda keadaan (divergensi).
"""
import heapq
import random
from dataclasses import dataclass, field

from .actuator import ActuatorRuntime
from .emitter import StateEmitter
from .evaluator import Runtime
from .wire import encode_data, key_hash


@dataclass
class SimConfig:
    duration_s: int = 3600
    loss: float = 0.0
    latency_ms: tuple = (5, 30)
    tick_ms: int = 1000
    seed: int = 0
    failures: list = field(default_factory=list)   # [(p, mati_detik, hidup_detik), ...]
    match_window_s: int = 30


def synthetic_traces(ir, duration_s, sample_s=5, seed=0):
    """Jejak sampel acak (random walk) di sekitar ambang setiap sinyal: {s: [(ms, nilai), ...]}."""
    rng = random.Random(seed)
    thresholds = {}
    for groups in (ir["when"], ir["clear"]):
        for group in groups:
            for s, _, th in group:
                thresholds.setdefault(s, []).append(th)
    traces = {}
    for signal in ir["signals"]:
        s = signal["s"]
        values = thresholds.get(s, [0.0])
        low, high = min(values) - 10, max(values) + 10
        step = (high - low) / 12
        value = rng.uniform(low, high)
        offset = rng.randrange(0, sample_s * 1000)
        points = []
        t = offset
        while t < duration_s * 1000:
            value = min(high, max(low, value + rng.uniform(-step, step)))
            points.append((t, round(value, 2)))
            t += sample_s * 1000
        traces[s] = points
    return traces


def _truth_fragment(ir):
    return {
        "rule": ir["rule"], "rev": "00000000", "when": ir["when"], "clear": ir["clear"],
        "timing": ir["timing"], "sources": [], "on_activate": [], "on_clear": [],
        "signals": [{"s": s["s"], "key": s["key"], "kh": "0x0000", "from": 0} for s in ir["signals"]],
    }


class Simulation:
    def __init__(self, ir, plan, traces, config=None):
        self.ir, self.plan, self.traces = ir, plan, traces
        self.config = config or SimConfig()
        self.rng = random.Random(self.config.seed)
        self.mac = {d["p"]: d["mac"].upper() for d in ir["devices"]}
        self.p_of_mac = {mac: p for p, mac in self.mac.items()}
        self.signal = {s["s"]: s for s in ir["signals"]}
        self.fragments = plan["fragments"]
        self.queue, self.seq = [], 0
        self.down = set()
        self.counters = {p: 0 for p in self.mac}
        self.latest = {p: {} for p in self.mac}         # nilai terakhir yang diukur device
        self.last_send = {}
        self.stats = {"data_sent": 0, "state_sent": 0, "delivered": 0, "lost": 0}
        self.levels = {a: [(0, False)] for a in plan["actuators"]}
        self.truth = [(0, False)]
        self.truth_runtime = Runtime(_truth_fragment(ir))
        self.evaluators, self.emitters, self.acts = {}, {}, {}
        for p in self.mac:
            self._boot(p, 0)

    # --- infrastruktur ------------------------------------------------------------

    def push(self, t, kind, *data):
        self.seq += 1
        heapq.heappush(self.queue, (t, self.seq, kind, data))

    def _boot(self, p, now):
        fragment = self.fragments.get(p)
        self.evaluators.pop(p, None)
        self.emitters.pop(p, None)
        if fragment is None:
            return
        if "when" in fragment:
            self.evaluators[p] = Runtime(fragment)
            if "emit" in fragment:
                emitter = StateEmitter.from_fragment(fragment, counter=self.counters[p])
                emitter.start(now)
                self.emitters[p] = emitter
                self._schedule_emit(p)
        if "evaluator" in fragment:
            if p in self.acts:
                self.acts[p].restart()
            else:
                self.acts[p] = ActuatorRuntime(fragment)

    def _level(self, p, active, now):
        if p in self.levels and self.levels[p][-1][1] != active:
            self.levels[p].append((now, active))

    def _broadcast(self, sender, frame, now):
        for receiver in self.mac:
            if receiver == sender or not self._listens(receiver, sender):
                continue
            if self.rng.random() < self.config.loss:
                self.stats["lost"] += 1
                continue
            delay = self.rng.uniform(*self.config.latency_ms)
            self.push(now + delay, "deliver", receiver, sender, frame)

    def _listens(self, receiver, sender):
        evaluator = self.evaluators.get(receiver)
        if evaluator and self.mac[sender] in evaluator.sources:
            return True
        act = self.acts.get(receiver)
        return bool(act and act.evaluator == self.mac[sender])

    # --- produsen -----------------------------------------------------------------

    def _publish(self, p, now):
        fragment = self.fragments.get(p) or {}
        entries = fragment.get("publish")
        if not entries or p in self.down:
            return
        signals = [(key_hash(e["key"]), value, now - ts)
                   for e in entries
                   for value, ts in [self.latest[p].get(e["key"], (None, None))] if value is not None]
        if not signals:
            return
        self.counters[p] += 1
        self.stats["data_sent"] += 1
        self.last_send[p] = now
        self._broadcast(p, encode_data(self.counters[p], signals), now)
        self.push(now + entries[0]["heartbeat"] * 1000, "heartbeat", p, now)

    def _sample(self, s, value, now):
        signal = self.signal[s]
        self.truth_runtime.receive(s, value, 0, now)
        self._truth_step(now)
        p = signal["p"]
        if p in self.down:
            return
        self.latest[p][signal["key"]] = (value, now)
        evaluator = self.evaluators.get(p)
        if evaluator and evaluator.read_local(signal["key"], value, now):
            self._evaluate(p, now)
        publish = (self.fragments.get(p) or {}).get("publish", [])
        if any(e["key"] == signal["key"] for e in publish):
            last = self.last_send.get(p)
            gap = publish[0]["min_interval"] * 1000
            if last is None or now - last >= gap:
                self._publish(p, now)
            else:
                self.push(last + gap, "publish", p)

    # --- evaluator dan aktuator ----------------------------------------------------

    def _truth_step(self, now):
        transition = self.truth_runtime.step(now)
        if transition:
            self.truth.append((now, transition.kind == "ACTIVATE"))

    def _evaluate(self, p, now, tick=False):
        evaluator = self.evaluators[p]
        transition = evaluator.tick(now) if tick else evaluator.step(now)
        if transition is None:
            return
        if p in self.levels:
            self._level(p, transition.kind == "ACTIVATE", now)
        if p in self.emitters:
            self.emitters[p].on_transition(transition.kind, now)
            self._schedule_emit(p)

    def _schedule_emit(self, p):
        due = self.emitters[p].next_due()
        if due is not None:
            self.push(due, "emit", p)

    def _emit(self, p, now):
        if p in self.down or p not in self.emitters:
            return
        emitter = self.emitters[p]
        for frame in emitter.due(now):
            self.stats["state_sent"] += 1
            self._broadcast(p, frame, now)
        self.counters[p] = emitter.counter
        self._schedule_emit(p)

    def _deliver(self, receiver, sender, frame, now):
        if receiver in self.down:
            self.stats["lost"] += 1
            return
        self.stats["delivered"] += 1
        evaluator = self.evaluators.get(receiver)
        if evaluator and frame[3] == 1 and evaluator.receive_frame(self.mac[sender], frame, now):
            self._evaluate(receiver, now)
        act = self.acts.get(receiver)
        if act and frame[3] == 4:
            for transition in act.receive_state(self.mac[sender], frame, now):
                self._level(receiver, transition.kind == "ACTIVATE", now)

    def _tick(self, now):
        transition = self.truth_runtime.tick(now)
        if transition:
            self.truth.append((now, transition.kind == "ACTIVATE"))
        for p in list(self.evaluators):
            if p not in self.down:
                self._evaluate(p, now, tick=True)
        for p, act in self.acts.items():
            if p not in self.down:
                for transition in act.tick(now):
                    self._level(p, transition.kind == "ACTIVATE", now)

    # --- jalankan -----------------------------------------------------------------

    def run(self):
        end = self.config.duration_s * 1000
        for s, points in self.traces.items():
            for t, value in points:
                self.push(t, "sample", s, value)
        for t in range(0, end, self.config.tick_ms):
            self.push(t + 0.5, "tick")
        for p, down_s, up_s in self.config.failures:
            self.push(down_s * 1000, "fail", p)
            if up_s is not None:
                self.push(up_s * 1000, "recover", p)
        while self.queue:
            now, _, kind, data = heapq.heappop(self.queue)
            if now > end:
                break
            if kind == "sample":
                self._sample(data[0], data[1], now)
            elif kind == "tick":
                self._tick(now)
            elif kind == "deliver":
                self._deliver(*data, now)
            elif kind == "publish":
                self._publish(data[0], now)
            elif kind == "heartbeat":
                p, sent_at = data
                if self.last_send.get(p) == sent_at:
                    self._publish(p, now)
            elif kind == "emit":
                self._emit(data[0], now)
            elif kind == "fail":
                self.down.add(data[0])
            elif kind == "recover":
                self.down.discard(data[0])
                self._boot(data[0], now)
        return self.result(end)

    # --- hasil --------------------------------------------------------------------

    def result(self, end):
        window = self.config.match_window_s * 1000
        per_actuator = {}
        for a, levels in self.levels.items():
            wrong = _mismatch(levels, self.truth, end)
            truth_changes = self.truth[1:]
            changes = levels[1:]
            used, latencies, missed = set(), [], 0
            for t, active in truth_changes:
                match = next((i for i, (u, v) in enumerate(changes)
                              if i not in used and v == active and t <= u <= t + window), None)
                if match is None:
                    missed += 1
                else:
                    used.add(match)
                    latencies.append(changes[match][0] - t)
            per_actuator[a] = {
                "wrong_ms": round(wrong),
                "wrong_ratio": round(wrong / end, 5) if end else 0,
                "truth_transitions": len(truth_changes),
                "transitions": len(changes),
                "missed": missed,
                "spurious": len(changes) - len(used),
                "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
                "max_latency_ms": round(max(latencies), 1) if latencies else None,
            }
        divergence = 0
        acts = list(self.levels)
        for i in range(len(acts)):
            for j in range(i + 1, len(acts)):
                divergence = max(divergence, _mismatch(self.levels[acts[i]], self.levels[acts[j]], end))
        return {"actuators": per_actuator, "divergence_ms": round(divergence), **self.stats}


def _value_at(levels, t):
    value = False
    for time, active in levels:
        if time > t:
            break
        value = active
    return value


def _mismatch(a, b, end):
    """Lama (ms) dua garis waktu keadaan berbeda dalam [0, end]."""
    times = sorted({t for t, _ in a} | {t for t, _ in b} | {0, end})
    total = 0
    for start, stop in zip(times, times[1:]):
        if start >= end:
            break
        if _value_at(a, start) != _value_at(b, start):
            total += min(stop, end) - start
    return total


def simulate(ir, plan, traces, config):
    return Simulation(ir, plan, traces, config).run()

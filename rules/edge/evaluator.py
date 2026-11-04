"""
Evaluator referensi micro rule: menjalankan bagian "eval" sebuah fragmen.

API-nya sengaja meniru yang nanti ditulis dalam C++ di firmware (tahap 27):

    rt = Runtime(fragment)
    rt.read_local("soil_moisture", 18.0, now_ms=1000)    # sensor di device ini
    rt.receive_frame("AA:BB:CC:00:00:02", frame, now_ms)   # frame DATA broadcast
    transition = rt.step(now_ms)                           # setiap data baru
    transition = rt.tick(now_ms)                           # setiap detik (hanya bila PENDING)

Semantiknya SAMA dengan mesin ECA di cloud karena memakai fungsi yang sama:
    rules.conditions: check, all_of, any_of, decide   (logika tiga nilai)
    rules.models.compare                              (operator, toleransi "=")
    rules.engines.eca.step                            (IDLE -> PENDING -> ACTIVE)
Yang berbeda hanya tipe data: waktu dalam milidetik (jam monotonic device, tanpa NTP)
dan nilai float32.
"""
from collections import Counter, namedtuple
from datetime import datetime, timedelta, timezone

from .. import conditions
from .wire import WireError, decode_data, f32

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)

Transition = namedtuple("Transition", "kind actions at_ms")


def at(ms):
    """Milidetik jam device -> datetime (hanya untuk fungsi cloud yang dipakai ulang)."""
    return EPOCH + timedelta(milliseconds=ms)


class _Condition:
    __slots__ = ("variable_id", "operator", "threshold")

    def __init__(self, s, operator, threshold):
        self.variable_id, self.operator, self.threshold = s, operator, f32(threshold)

    def test(self, value):
        from ..models import compare

        return compare(value, self.operator, self.threshold)


class _Spec:
    def __init__(self, duration, cooldown):
        self.duration_seconds, self.cooldown_seconds = duration, cooldown


class _State:
    def __init__(self):
        self.status = "IDLE"
        self.pending_since = None
        self.last_activated_at = None
        self.last_cleared_at = None


class Runtime:
    def __init__(self, fragment):
        self.fragment = fragment
        self.rule, self.rev = fragment["rule"], fragment["rev"]
        self.when = [[_Condition(*c) for c in group] for group in fragment["when"]]
        self.clear = [[_Condition(*c) for c in group] for group in fragment["clear"]]
        timing = fragment["timing"]
        self.spec = _Spec(timing["duration"], timing["cooldown"])
        self.max_age = timing["max_age"]
        self.state = _State()
        self.readings = {}
        mac_of = {src["p"]: src["mac"].upper() for src in fragment.get("sources", [])}
        self.sources = set(mac_of.values())
        self.local, self.route = {}, {}
        for signal in fragment["signals"]:
            if signal["from"] == 0:
                self.local[signal["key"]] = signal["s"]
            else:
                self.route[(mac_of[signal["from"]], int(signal["kh"], 16))] = signal["s"]
        self.counters = {}
        self.dropped = Counter()

    # --- masukan --------------------------------------------------------------------

    def receive(self, s, value, age_ms, now_ms):
        self.readings[s] = conditions.Reading(f32(value), at(now_ms - age_ms), "frame")

    def read_local(self, key, value, now_ms):
        s = self.local.get(key)
        if s is None:
            return False
        self.readings[s] = conditions.Reading(f32(value), at(now_ms), "local")
        return True

    def receive_frame(self, src_mac, frame, now_ms):
        """Frame DATA dari udara. Mengembalikan jumlah sinyal yang dipakai."""
        mac = src_mac.upper()
        if mac not in self.sources:
            self.dropped["source"] += 1
            return 0
        try:
            data = decode_data(frame)
        except WireError:
            self.dropped["malformed"] += 1
            return 0
        last = self.counters.get(mac)
        if last is not None and data["counter"] <= last:
            self.dropped["replay"] += 1
            return 0
        self.counters[mac] = data["counter"]
        used = 0
        for kh, value, age_ms in data["signals"]:
            s = self.route.get((mac, kh))
            if s is None:
                self.dropped["key"] += 1
                continue
            self.receive(s, value, age_ms, now_ms)
            used += 1
        return used

    # --- penilaian ------------------------------------------------------------------

    def _expression(self, groups, now):
        return conditions.any_of(
            conditions.all_of(
                conditions.check(c, self.readings.get(c.variable_id), now, self.max_age)[0] for c in group
            )
            for group in groups
        )

    def judge(self, now_ms):
        now = at(now_ms)
        when_result = self._expression(self.when, now)
        has_hysteresis = bool(self.clear)
        if has_hysteresis:
            clear_result = self._expression(self.clear, now)
        else:
            clear_result = None if when_result is None else not when_result
        return conditions.decide(when_result, clear_result, has_hysteresis)

    def step(self, now_ms):
        """Nilai aturan sekarang. Mengembalikan Transition (ACTIVATE / CLEAR) atau None."""
        from ..engines.eca import step

        condition, clear = self.judge(now_ms)
        transition = step(self.state, self.spec, condition, clear, at(now_ms))
        if transition is None:
            return None
        kind = getattr(transition, "value", transition)
        actions = self.fragment["on_activate"] if kind == "ACTIVATE" else self.fragment["on_clear"]
        return Transition(kind, actions, now_ms)

    def tick(self, now_ms):
        """Penilaian berkala. Seperti EcaEngine.tick(), hanya aturan yang PENDING dinilai
        ulang (menunggu "for at least" / cooldown). IDLE dan ACTIVE hanya berubah karena data
        baru; menilainya di setiap tick bisa berbeda dari cloud, mis. ekspresi clear yang
        menjadi "tidak diketahui" karena usang membuat ekspresi aktif lolos."""
        if self.status != "PENDING":
            return None
        return self.step(now_ms)

    @property
    def status(self):
        return str(getattr(self.state.status, "value", self.state.status))

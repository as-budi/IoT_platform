"""
Penerima frame STATE di device aktuator (strategi SE, rencana 26b bagian 3).

    act = ActuatorRuntime(fragment)
    events = act.receive_state("AA:BB:CC:00:00:01", frame, now_ms)
    events += act.tick(now_ms)       # timeout bila evaluator diam

Aksi dijalankan hanya bila keadaan yang diterima BERBEDA dari yang sedang berlaku
(frame duplikat dan heartbeat tidak menjalankan apa pun). Setelah restart keadaan
belum diketahui (None) dan diambil dari frame STATE pertama.
"""
from collections import Counter

from .evaluator import Transition
from .wire import WireError, decode_state, rev_bytes


class ActuatorRuntime:
    def __init__(self, fragment):
        self.fragment = fragment
        self.rule, self.rev = fragment["rule"], fragment["rev"]
        self.evaluator = fragment["evaluator"]["mac"].upper()
        self.timeout_ms = int(fragment["state_timeout"] * 1000)
        self.on_timeout = fragment["on_timeout"]
        self.applied = False          # keadaan awal sama dengan cloud: IDLE
        self.last_seq = None
        self.last_counter = None
        self.last_heard = None
        self.timed_out = False
        self.missed = 0               # transisi yang terlewat (nomor melompat)
        self.dropped = Counter()

    def restart(self):
        self.applied = None
        self.last_seq = self.last_counter = self.last_heard = None
        self.timed_out = False

    def _run(self, active, now_ms):
        self.applied = active
        actions = self.fragment["on_activate"] if active else self.fragment["on_clear"]
        return Transition("ACTIVATE" if active else "CLEAR", actions, now_ms)

    def receive_state(self, src_mac, frame, now_ms):
        if src_mac.upper() != self.evaluator:
            self.dropped["source"] += 1
            return []
        try:
            state = decode_state(frame)
        except WireError:
            self.dropped["malformed"] += 1
            return []
        if state["rule"] != self.rule or state["rev"] != rev_bytes(self.rev).hex():
            self.dropped["rule"] += 1
            return []
        if self.last_counter is not None and state["counter"] <= self.last_counter:
            self.dropped["replay"] += 1
            return []
        self.last_counter = state["counter"]
        self.last_heard = now_ms
        self.timed_out = False
        if self.last_seq is not None:
            jump = (state["seq"] - self.last_seq) & 0xFFFF
            if jump > 1:
                self.missed += jump - 1
        self.last_seq = state["seq"]
        if state["active"] != self.applied:
            return [self._run(state["active"], now_ms)]
        return []

    def tick(self, now_ms):
        if self.last_heard is None or self.timed_out or now_ms - self.last_heard <= self.timeout_ms:
            return []
        self.timed_out = True
        if self.on_timeout == "clear" and self.applied:
            return [self._run(False, now_ms)]
        return []

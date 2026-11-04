"""
Pengirim frame STATE pada strategi SE (evaluator di sensor, rencana 26b bagian 3).

Keputusan dipancarkan sebagai KEADAAN (level-triggered): saat transisi frame dikirim
`repeat` kali berjarak `gap_ms`, dan di luar transisi setiap `heartbeat` detik. Dengan
begitu aktuator selalu akhirnya menyamakan diri walaupun ada frame yang hilang.
"""
from .wire import encode_state


class StateEmitter:
    def __init__(self, rule, rev, heartbeat=10, repeat=3, gap_ms=100, counter=0):
        self.rule, self.rev = rule, rev
        self.heartbeat_ms = int(heartbeat * 1000)
        self.repeat, self.gap_ms = repeat, gap_ms
        self.counter = counter
        self.active = False
        self.seq = 0
        self.changed_ms = None
        self.queue = []
        self.next_heartbeat = None

    @classmethod
    def from_fragment(cls, fragment, counter=0):
        emit = fragment["emit"]
        return cls(fragment["rule"], fragment["rev"], emit["heartbeat"], emit["repeat"], emit["repeat_gap_ms"],
                   counter)

    def start(self, now_ms):
        self.next_heartbeat = now_ms

    def on_transition(self, kind, now_ms):
        self.active = kind == "ACTIVATE"
        self.seq = (self.seq + 1) & 0xFFFF
        self.changed_ms = now_ms
        self.queue = [now_ms + i * self.gap_ms for i in range(self.repeat)]
        self.next_heartbeat = now_ms + self.heartbeat_ms

    def next_due(self):
        times = ([self.queue[0]] if self.queue else []) + ([self.next_heartbeat] if self.next_heartbeat is not None else [])
        return min(times) if times else None

    def frame(self, now_ms):
        self.counter = (self.counter + 1) & 0xFFFFFFFF
        age = now_ms - self.changed_ms if self.changed_ms is not None else 0
        return encode_state(self.counter, self.rule, self.rev, self.active, self.seq, age)

    def due(self, now_ms):
        """Frame yang harus dikirim pada `now_ms`."""
        frames = []
        while self.queue and self.queue[0] <= now_ms:
            self.queue.pop(0)
            frames.append(self.frame(now_ms))
        if self.next_heartbeat is not None and now_ms >= self.next_heartbeat:
            if not frames:
                frames.append(self.frame(now_ms))
            self.next_heartbeat = now_ms + self.heartbeat_ms
        return frames

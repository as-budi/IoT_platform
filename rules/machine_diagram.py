"""
Gambar state machine sebagai SVG, dibuat di server (tahap 20). Pasangan rules/flow_diagram.py.

Tata letak satu baris:

        ┌─── temperature > 8 for 120 s ───┐   (panah MAJU: lengkung di atas)
    ●──►[ NORMAL ]                     [ WARM ]──after 600 s──►[ ALARM ]
        └───────── temperature <= 7 ──────┘   (panah MUNDUR: lengkung di bawah)
                       ▲
                  * device offline            (dari keadaan mana pun: panah dari bawah)

- Keadaan berurutan seperti di form; keadaan awal paling kiri, ditandai titik hitam
  dan panah masuk (notasi UML).
- Lengkung yang rentangnya tumpang tindih ditaruh di "tingkat" berbeda (lebih tinggi
  atau lebih rendah), seperti mewarnai interval, supaya label tidak saling menutupi.
- Panah ke dirinya sendiri (mis. "setiap 600 s di ALARM, ulangi") digambar sebagai
  lingkaran kecil di pojok kanan atas kotak.
- Bila ada jumlah device per keadaan, kotak keadaan menampilkannya ("2 devices") dan
  keadaan yang sedang ditempati diberi garis tebal.
"""
from .engines.state_machine import describe_transition

BOX_W, BOX_H = 150, 56
GAP = 96
PAD = 18
START = 34          # ruang untuk titik keadaan awal
BASE, LEVEL_H = 46, 30
STUB_H = 46
CHARS = 30


def shorten(text, limit=CHARS):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def label_width(text):
    return len(text) * 6.1 + 14


def assign_levels(arcs):
    """Arc = (kiri, kanan, ...). Tingkat terendah yang tidak tumpang tindih dengan arc lain."""
    taken = {}
    levels = []
    for arc in sorted(arcs, key=lambda a: (a[1] - a[0], a[0])):
        left, right = arc[0], arc[1]
        level = 0
        while any(left < end and begin < right for begin, end in taken.get(level, [])):
            level += 1
        taken.setdefault(level, []).append((left, right))
        levels.append((arc, level))
    return levels


def layout(rule, counts=None):
    counts = counts or {}
    states = list(rule.machine_states.all())
    states.sort(key=lambda s: (not s.is_initial, s.order, s.pk))
    index = {state.pk: i for i, state in enumerate(states)}
    transitions = list(rule.machine_transitions.all())

    def center(i):
        return PAD + START + i * (BOX_W + GAP) + BOX_W / 2

    forward, backward, loops, stubs = [], [], [], []
    for t in transitions:
        if t.target_id not in index:
            continue
        text = describe_transition(t)
        j = index[t.target_id]
        if t.source_id is None:
            stubs.append((j, text, t))
        elif t.source_id not in index:
            continue
        elif t.source_id == t.target_id:
            loops.append((j, text, t))
        elif index[t.source_id] < j:
            forward.append((index[t.source_id], j, text, t))
        else:
            backward.append((j, index[t.source_id], text, t))

    fwd = assign_levels(forward)
    bwd = assign_levels(backward)
    up = max([BASE + level * LEVEL_H for _, level in fwd] + ([BASE] if loops else []) + [0])
    down = max([BASE + level * LEVEL_H for _, level in bwd] + [0])
    stub_levels = {}
    for j, _text, _t in stubs:
        stub_levels[j] = stub_levels.get(j, 0) + 1
    stub_depth = max(stub_levels.values(), default=0)
    top = PAD + up * 0.75 + (12 if up else 0)
    bottom = top + BOX_H
    height = bottom + down * 0.75 + (14 if down else 0) + (STUB_H + (stub_depth - 1) * 24 if stubs else 0) + PAD
    width = PAD * 2 + START + len(states) * BOX_W + max(len(states) - 1, 0) * GAP

    out_states = []
    for i, state in enumerate(states):
        x = center(i) - BOX_W / 2
        count = counts.get(state.pk, 0)
        out_states.append({
            "pk": state.pk, "x": x, "y": top, "w": BOX_W, "h": BOX_H,
            "name": shorten(state.name, 18), "description": shorten(state.description, 24),
            "tone": state.tone.lower(), "initial": state.is_initial,
            "count": count, "occupied": count > 0,
            "cx": x + BOX_W / 2, "ty1": top + 24, "ty2": top + 41,
        })

    links = []

    def add(d, text, transition, lx, ly, kind):
        label = shorten(text)
        links.append({
            "d": d, "label": label, "full": text, "kind": kind,
            "trigger": transition.trigger.lower(),
            "lx": lx, "ly": ly, "lw": label_width(label), "lx0": -label_width(label) / 2,
        })

    for (left, right, text, t), level in fwd:
        h = BASE + level * LEVEL_H
        x1, x2 = center(left) + 18, center(right) - 18
        y = top
        d = f"M{x1:.1f},{y:.1f} C{x1:.1f},{y - h:.1f} {x2:.1f},{y - h:.1f} {x2:.1f},{y:.1f}"
        add(d, text, t, (x1 + x2) / 2, y - h * 0.75, "forward")
    for (left, right, text, t), level in bwd:
        h = BASE + level * LEVEL_H
        x1, x2 = center(right) - 18, center(left) + 18     # dari kanan (asal) ke kiri (tujuan)
        y = bottom
        d = f"M{x1:.1f},{y:.1f} C{x1:.1f},{y + h:.1f} {x2:.1f},{y + h:.1f} {x2:.1f},{y:.1f}"
        add(d, text, t, (x1 + x2) / 2, y + h * 0.75, "backward")
    for j, text, t in loops:
        x = center(j) + BOX_W / 2 - 26
        y = top
        d = f"M{x:.1f},{y:.1f} C{x - 6:.1f},{y - 44:.1f} {x + 40:.1f},{y - 44:.1f} {x + 26:.1f},{y + 10:.1f}"
        add(d, text, t, x + 14, y - 38, "loop")
    seen = {}
    stub_top = bottom + down * 0.75 + (14 if down else 0)
    for j, text, t in stubs:
        k = seen.get(j, 0)
        seen[j] = k + 1
        cx = center(j) + (k - (stub_levels[j] - 1) / 2) * 14
        ly = stub_top + STUB_H - 12 + k * 24
        d = f"M{cx:.1f},{ly - 9:.1f} L{cx:.1f},{bottom:.1f}"
        add(d, "* " + text, t, cx, ly, "any")

    initial = next((s for s in out_states if s["initial"]), out_states[0] if out_states else None)
    start = None
    if initial:
        start = {"cx": PAD + 6, "x1": PAD + 13, "cy": top + BOX_H / 2, "x2": initial["x"], "y2": top + BOX_H / 2}
    return {
        "width": max(width, 320), "height": height,
        "states": out_states, "links": links, "start": start,
        "has_counts": bool(counts),
    }

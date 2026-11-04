"""
Diagram penempatan micro rule (SVG dibuat di server, seperti flow_diagram.py).

Kolom kiri ke kanan: sensor yang hanya memancarkan data, device yang mengevaluasi
aturan, dan (SE) aktuator yang menerima keputusan. Garis penuh = data (broadcast
DATA), garis putus-putus = keputusan (STATE). Aksi yang tetap di platform tampil di
kotak paling bawah.
"""
from html import escape

BOX_W, BOX_H, COL_GAP, ROW_GAP, PAD = 190, 58, 90, 22, 16


def _role_text(fragment):
    parts = []
    if fragment.get("publish"):
        parts.append("broadcasts " + ", ".join(e["key"] for e in fragment["publish"]))
    if "when" in fragment:
        parts.append("evaluates the rule")
    if "evaluator" in fragment:
        parts.append("applies the decision")
    actions = fragment.get("on_activate", []) + fragment.get("on_clear", [])
    keys = sorted({a.get("key") or a.get("method") for a in actions})
    if keys:
        parts.append("controls " + ", ".join(keys))
    return parts


def _short(text, limit=28):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def svg(ir, plan, names):
    fragments = plan["fragments"]
    evaluators = plan["evaluators"]
    columns = {}
    for p, fragment in fragments.items():
        if "when" in fragment:
            columns[p] = 1
        elif "evaluator" in fragment:
            columns[p] = 2
        else:
            columns[p] = 0
    used = sorted(set(columns.values()))
    remap = {col: i for i, col in enumerate(used)}
    rows = {}
    position = {}
    for p in sorted(fragments):
        col = remap[columns[p]]
        row = rows.get(col, 0)
        rows[col] = row + 1
        position[p] = (PAD + col * (BOX_W + COL_GAP), PAD + row * (BOX_H + ROW_GAP))
    width = PAD * 2 + len(used) * BOX_W + (len(used) - 1) * COL_GAP + 60
    height = PAD * 2 + max(rows.values()) * (BOX_H + ROW_GAP)
    cloud = sorted({c["kind"].replace("_", " ").lower() for c in ir["cloud"]})
    if cloud:
        height += 44

    links = []
    for p, fragment in fragments.items():
        if not fragment.get("publish"):
            continue
        for e in evaluators:
            if e != p and any(s["from"] == p for s in fragments[e].get("signals", [])):
                links.append((p, e, "data"))
    for p, fragment in fragments.items():
        if "evaluator" in fragment:
            links.append((fragment["evaluator"]["p"], p, "state"))

    # Id panah unik per diagram: halaman punya dua diagram (dua tab); bila id-nya sama,
    # browser memakai <marker> di tab yang tersembunyi dan panah di tab lain tidak tampil.
    arrow = f"edge-arrow-{plan['rev']}"
    out = [f'<svg class="edge-diagram" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'role="img" aria-label="Where the rule runs">',
           f'<defs><marker id="{arrow}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
           'markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" class="edge-arrow"/>'
           '</marker></defs>']
    for p, (x, y) in position.items():
        fragment = fragments[p]
        lines = _role_text(fragment)
        css = "edge-node-eval" if "when" in fragment else ("edge-node-act" if "evaluator" in fragment else "edge-node-src")
        out.append(f'<g class="edge-node {css}"><rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="8"/>'
                   f'<text x="{x + 10}" y="{y + 20}" class="edge-node-name">{escape(_short(names.get(p, str(p))))}</text>')
        for i, line in enumerate(lines[:2]):
            out.append(f'<text x="{x + 10}" y="{y + 36 + i * 14}" class="edge-node-role">{escape(_short(line, 32))}</text>')
        out.append("</g>")
    for source, target, kind in links:
        x1, y1 = position[source]
        x2, y2 = position[target]
        y1c, y2c = y1 + BOX_H / 2, y2 + BOX_H / 2
        if x1 == x2:
            bend = x1 + BOX_W + 50
            path = f"M {x1 + BOX_W} {y1c} C {bend} {y1c} {bend} {y2c} {x1 + BOX_W} {y2c}"
        elif x1 < x2:
            mid = (x1 + BOX_W + x2) / 2
            path = f"M {x1 + BOX_W} {y1c} C {mid} {y1c} {mid} {y2c} {x2} {y2c}"
        else:
            mid = (x2 + BOX_W + x1) / 2
            path = f"M {x1} {y1c} C {mid} {y1c} {mid} {y2c} {x2 + BOX_W} {y2c}"
        out.append(f'<path class="edge-link edge-link-{kind}" d="{path}" marker-end="url(#{arrow})">'
                   f'<title>{"data (broadcast)" if kind == "data" else "decision (state)"}</title></path>')
    if cloud:
        y = height - PAD - 34
        out.append(f'<g class="edge-node edge-node-cloud"><rect x="{PAD}" y="{y}" width="{width - 2 * PAD}" '
                   f'height="34" rx="8"/><text x="{PAD + 10}" y="{y + 21}" class="edge-node-role">'
                   f'Stays on the platform: {escape(", ".join(cloud))}</text></g>')
    out.append("</svg>")
    return "".join(out)

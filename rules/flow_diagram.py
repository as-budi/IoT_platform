"""
Gambar rule chain sebagai SVG, dibuat di server (tanpa pustaka JavaScript).

Tata letak sederhana "berlapis" (layered layout):
- Input di kolom 0.
- Setiap node berada di kolom = jalur TERPANJANG dari Input ke node itu.
  Karena graf tidak boleh berputar (rules/forms.py), panah selalu mengarah ke
  kanan: dari kolom kecil ke kolom yang lebih besar.
- Node yang tidak tersambung dari Input ditaruh mulai kolom 1 dan diberi garis
  putus-putus (pesan tidak akan pernah sampai ke sana).

Bila ada jejak (trace) pesan terakhir, node dan panah yang dilewati pesan itu
ditandai, sehingga jalannya pesan terlihat langsung di gambar.
"""
from .engines.flow import NODE_TYPES, node_summary
from .models import FlowLink

NODE_W, NODE_H = 184, 60
INPUT_W = 80
COL_GAP, ROW_GAP = 64, 26
# Huruf monospace 11 px kira-kira 6.7 px lebarnya: ringkasan dipotong supaya muat di kotak.
SUMMARY_CHARS = int((NODE_W - 18) / 6.7)
PAD = 16


def shorten(text, limit=28):
    return text if len(text) <= limit else text[: limit - 1] + "…"


def layout(rule, trace=None):
    nodes = list(rule.nodes.all())
    links = list(rule.links.all())
    by_pk = {node.pk: node for node in nodes}

    # 1) Kolom: jalur terpanjang dari Input (relaksasi berulang; aman karena graf tanpa siklus,
    #    dan dibatasi jumlah node bila data lama ternyata berputar).
    column = {}
    for link in links:
        if link.source_id is None:
            column[link.target_id] = 1
    for _ in range(len(nodes)):
        moved = False
        for link in links:
            if link.source_id in column and column.get(link.target_id, 0) < column[link.source_id] + 1:
                column[link.target_id] = column[link.source_id] + 1
                moved = True
        if not moved:
            break
    reachable = set(column)
    for node in nodes:
        column.setdefault(node.pk, 1)

    # 2) Baris: urut menurut urutan node di dalam kolomnya.
    columns = {}
    for node in nodes:
        columns.setdefault(column[node.pk], []).append(node)
    rows = max([len(v) for v in columns.values()] + [1])
    height = PAD * 2 + rows * NODE_H + (rows - 1) * ROW_GAP

    def x_of(col):
        return PAD + (INPUT_W + COL_GAP if col >= 1 else 0) + max(col - 1, 0) * (NODE_W + COL_GAP)

    # 3) Apa yang dilewati pesan terakhir.
    visited, walked = {}, set()
    for row in trace or []:
        if "node" not in row:
            continue
        visited[row["node"]] = row["label"]
        if row["from"] == "Input":
            walked.add(("Input", FlowLink.Label.SUCCESS, row["name"]))
        for name in row["next"]:
            walked.add((row["name"], row["label"], name))

    boxes = {}
    out_nodes = []
    for col, members in columns.items():
        top = PAD + (height - 2 * PAD - (len(members) * NODE_H + (len(members) - 1) * ROW_GAP)) / 2
        for index, node in enumerate(members):
            x, y = x_of(col), top + index * (NODE_H + ROW_GAP)
            boxes[node.pk] = (x, y, NODE_W)
            info = NODE_TYPES[node.kind]
            out_nodes.append({
                "pk": node.pk, "x": x, "y": y, "w": NODE_W, "h": NODE_H,
                "name": shorten(node.name, 22),
                "kind": node.get_kind_display(),
                "category": info.category.lower(),
                "summary": shorten(node_summary(node), SUMMARY_CHARS),
                "reachable": node.pk in reachable,
                "visited": node.pk in visited,
                "result": FlowLink.Label(visited[node.pk]).label if node.pk in visited else "",
                "tx": x + 10, "ty1": y + 19, "ty2": y + 35, "ty3": y + 51,
            })
    input_box = (PAD, PAD + (height - 2 * PAD - NODE_H) / 2, INPUT_W)
    input_used = bool(trace)

    out_links = []
    for link in links:
        if link.target_id not in boxes:
            continue
        sx, sy, sw = input_box if link.source_id is None else boxes.get(link.source_id, (None,) * 3)
        if sx is None:
            continue
        tx, ty, _ = boxes[link.target_id]
        x1, y1 = sx + sw, sy + NODE_H / 2
        x2, y2 = tx, ty + NODE_H / 2
        bend = max((x2 - x1) / 2, 40)
        # Label di tengah kurva Bezier (t = 0.5): di situ cabang-cabang dari satu node
        # sudah cukup terpisah sehingga labelnya tidak bertumpuk.
        t = 0.5
        c1x, c1y, c2x, c2y = x1 + bend, y1, x2 - bend, y2
        lx = (1 - t) ** 3 * x1 + 3 * (1 - t) ** 2 * t * c1x + 3 * (1 - t) * t ** 2 * c2x + t ** 3 * x2
        ly = (1 - t) ** 3 * y1 + 3 * (1 - t) ** 2 * t * c1y + 3 * (1 - t) * t ** 2 * c2y + t ** 3 * y2
        source_name = "Input" if link.source_id is None else by_pk[link.source_id].name
        out_links.append({
            "d": f"M{x1:.1f},{y1:.1f} C{c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} {x2:.1f},{y2:.1f}",
            "label": "" if link.source_id is None else link.get_label_display(),
            "kind": link.label.lower(),
            "lx": lx, "ly": ly,
            "walked": (source_name, link.label, by_pk[link.target_id].name) in walked,
        })

    width = max([x + w for x, y, w in boxes.values()] + [PAD + INPUT_W]) + PAD
    return {
        "width": width, "height": height,
        "input": {"x": input_box[0], "y": input_box[1], "w": INPUT_W, "h": NODE_H, "visited": input_used,
                  "cx": input_box[0] + INPUT_W / 2, "cy": input_box[1] + NODE_H / 2 + 5},
        "nodes": out_nodes,
        "links": out_links,
        "has_unreachable": any(not n["reachable"] for n in out_nodes),
    }

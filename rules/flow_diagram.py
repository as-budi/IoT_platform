"""
Gambar rule chain sebagai SVG, dibuat di server (tanpa pustaka JavaScript).

Tata letak sederhana "berlapis" (layered layout):
- Input di kolom 0.
- Setiap node berada di kolom = jalur TERPANJANG dari Input ke node itu.
  Karena graf tidak boleh berputar (rules/forms.py), panah selalu mengarah ke
  kanan: dari kolom kecil ke kolom yang lebih besar.
- Node yang tidak tersambung dari Input ditaruh mulai kolom 1 dan diberi garis
  putus-putus (pesan tidak akan pernah sampai ke sana).
- Panah yang melompati kolom (mis. kolom 1 -> kolom 3) diberi "jalur" (lane)
  kosong di setiap kolom yang dilewatinya, seperti "dummy node" pada tata letak
  Sugiyama. Panah lewat jalur itu, bukan menembus kotak node di kolom tengah,
  dan labelnya diletakkan di jalur pertama sehingga selalu terlihat.

Bila ada jejak (trace) pesan terakhir, node dan panah yang dilewati pesan itu
ditandai, sehingga jalannya pesan terlihat langsung di gambar.
"""
from .engines.flow import NODE_TYPES, node_summary
from .models import FlowLink

NODE_W, NODE_H = 184, 60
INPUT_W = 80
COL_GAP, ROW_GAP = 64, 26
LANE_H = 22     # tinggi jalur untuk panah yang melompati kolom
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

    # 2) Isi tiap kolom: node (urut menurut urutan di form), lalu jalur untuk panah
    #    yang melompati kolom itu. Setiap isi punya tinggi sendiri.
    columns = {}
    for node in nodes:
        columns.setdefault(column[node.pk], []).append(("node", node, NODE_H))
    for index, link in enumerate(links):
        if link.target_id not in column:
            continue
        start = 0 if link.source_id is None else column.get(link.source_id)
        if start is None:
            continue
        for col in range(start + 1, column[link.target_id]):
            columns.setdefault(col, []).append(("lane", index, LANE_H))

    def stack_height(items):
        return sum(h for _, _, h in items) + max(len(items) - 1, 0) * ROW_GAP

    inner = max([stack_height(items) for items in columns.values()] + [NODE_H])
    height = PAD * 2 + inner

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
    lanes = {}      # (indeks panah, kolom) -> (x kiri, y tengah)
    out_nodes = []
    for col, members in columns.items():
        y = PAD + (inner - stack_height(members)) / 2
        for kind, item, item_h in members:
            x = x_of(col)
            if kind == "lane":
                lanes[(item, col)] = (x, y + item_h / 2)
                y += item_h + ROW_GAP
                continue
            node, top = item, y
            y += item_h + ROW_GAP
            boxes[node.pk] = (x, top, NODE_W)
            info = NODE_TYPES[node.kind]
            out_nodes.append({
                "pk": node.pk, "x": x, "y": top, "w": NODE_W, "h": NODE_H,
                "name": shorten(node.name, 22),
                "kind": node.get_kind_display(),
                "category": info.category.lower(),
                "summary": shorten(node_summary(node), SUMMARY_CHARS),
                "reachable": node.pk in reachable,
                "visited": node.pk in visited,
                "result": FlowLink.Label(visited[node.pk]).label if node.pk in visited else "",
                "tx": x + 10, "ty1": top + 19, "ty2": top + 35, "ty3": top + 51,
            })
    input_box = (PAD, PAD + (height - 2 * PAD - NODE_H) / 2, INPUT_W)
    input_used = bool(trace)

    def curve(x1, y1, x2, y2):
        """Kurva Bezier mendatar dari (x1, y1) ke (x2, y2), dan titik tengahnya (t = 0.5)."""
        bend = max((x2 - x1) / 2, 40)
        c1x, c2x = x1 + bend, x2 - bend
        # Pada t = 0.5: x = (x1 + 3·c1x + 3·c2x + x2) / 8, y = (y1 + y2) / 2.
        middle = ((x1 + 3 * c1x + 3 * c2x + x2) / 8, (y1 + y2) / 2)
        return f" C{c1x:.1f},{y1:.1f} {c2x:.1f},{y2:.1f} {x2:.1f},{y2:.1f}", middle

    out_links = []
    for index, link in enumerate(links):
        if link.target_id not in boxes:
            continue
        sx, sy, sw = input_box if link.source_id is None else boxes.get(link.source_id, (None,) * 3)
        if sx is None:
            continue
        tx, ty, _ = boxes[link.target_id]
        x1, y1 = sx + sw, sy + NODE_H / 2
        x2, y2 = tx, ty + NODE_H / 2
        # Titik-titik yang dilewati: jalur di setiap kolom antara asal dan tujuan.
        passes = sorted((col, point) for (i, col), point in lanes.items() if i == index)
        d = f"M{x1:.1f},{y1:.1f}"
        x, y = x1, y1
        label_at = None
        for _, (lane_x, lane_y) in passes:
            segment, _ = curve(x, y, lane_x, lane_y)
            # Di dalam jalur panah lurus mendatar selebar kotak node.
            d += segment + f" L{lane_x + NODE_W:.1f},{lane_y:.1f}"
            if label_at is None:
                label_at = (lane_x + NODE_W / 2, lane_y)
            x, y = lane_x + NODE_W, lane_y
        segment, middle = curve(x, y, x2, y2)
        d += segment
        # Label: di jalur pertama untuk panah panjang; selain itu di tengah kurva,
        # tempat cabang-cabang dari satu node sudah cukup terpisah.
        lx, ly = label_at or middle
        source_name = "Input" if link.source_id is None else by_pk[link.source_id].name
        out_links.append({
            "d": d,
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

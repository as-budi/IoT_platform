"""
IR -> fragmen per device (rencana tahap 26 bagian 5, 26b bagian 2).

Setiap device yang terlibat mendapat SATU fragmen dengan bagian sesuai perannya:

    src   "publish": variabel yang dipancarkan (broadcast) untuk evaluator di device lain
    eval  "sources", "signals", "when", "clear", "timing", aksi lokal, dan (SE) "emit"
    act   (hanya SE) "evaluator" sumber keputusan, aksi, "state_timeout", "on_timeout"

Peran digabung dengan "+", mis. "src+eval". Fragmen tidak memuat token akses, nama
pengguna, atau teks notifikasi.
"""
from .ir import digest, size_of
from .limits import limits
from .placement import ACTUATOR, SENSOR, actuators, evaluators
from .wire import STATE_FRAME_BYTES, data_frame_bytes, key_hash, kh_text

FORMAT = "microrule/1"
ROLE_ORDER = ("src", "eval", "act")


def heartbeat_for(max_age):
    """Detik antar pengiriman ulang nilai: tiga frame boleh hilang sebelum nilai usang."""
    return max(5, max_age // 3) if max_age else 30


def local_actions(ir, p, on):
    result = []
    for action in ir["actions"]:
        if action["p"] != p or action["on"] != on:
            continue
        if action["do"] == "set":
            result.append({"do": "set", "key": action["key"], "value": action["value"]})
        else:
            result.append({"do": "command", "method": action["method"], "params": action["params"]})
    return result


def compile_plan(ir, strategy=ACTUATOR, policy=None, rates=None, lim=None):
    """Mengembalikan dict rencana:

        {"strategy", "policy", "rev", "ir_rev", "evaluators", "actuators",
         "fragments": {p: fragmen}, "sizes": {p: byte}, "data_frames": {p: byte},
         "state_frame": byte atau None, "heartbeat": detik}
    """
    lim = lim or limits()
    if strategy == SENSOR:
        policy = policy or lim["SE_POLICY"]
    else:
        policy = None
    evals = evaluators(ir, strategy, policy or "primary", rates)
    acts = actuators(ir)
    mac = {device["p"]: device["mac"] for device in ir["devices"]}
    rev = digest({"ir": ir, "strategy": strategy, "policy": policy})
    heartbeat = heartbeat_for(ir["timing"]["max_age"])

    publish = {}
    for signal in ir["signals"]:
        if any(e != signal["p"] for e in evals):
            publish.setdefault(signal["p"], []).append({
                "key": signal["key"], "kh": kh_text(key_hash(signal["key"])),
                "heartbeat": heartbeat, "min_interval": 1,
            })

    acting = [a for a in acts if a not in evals] if strategy == SENSOR else []
    evaluation = {}
    for e in evals:
        remote = sorted({signal["p"] for signal in ir["signals"] if signal["p"] != e})
        part = {
            "sources": [{"p": p, "mac": mac[p], "key_id": None} for p in remote],
            "signals": [
                {"s": signal["s"], "key": signal["key"], "kh": kh_text(key_hash(signal["key"])),
                 "from": 0 if signal["p"] == e else signal["p"]}
                for signal in ir["signals"]
            ],
            "when": ir["when"],
            "clear": ir["clear"],
            "timing": {**ir["timing"], "tick": lim["TICK_SECONDS"]},
            "on_activate": local_actions(ir, e, "activate"),
            "on_clear": local_actions(ir, e, "clear"),
        }
        if acting:
            part["emit"] = {"heartbeat": lim["SE_HEARTBEAT"], "repeat": lim["SE_REPEAT"],
                            "repeat_gap_ms": lim["SE_REPEAT_GAP_MS"]}
        evaluation[e] = part

    actuation = {}
    for a in acting:
        e = evals[0]
        actuation[a] = {
            "evaluator": {"p": e, "mac": mac[e], "key_id": None},
            "on_activate": local_actions(ir, a, "activate"),
            "on_clear": local_actions(ir, a, "clear"),
            "state_timeout": 3 * lim["SE_HEARTBEAT"],
            "on_timeout": "hold",
        }

    fragments = {}
    for p in sorted(set(publish) | set(evaluation) | set(actuation)):
        roles = [role for role, parts in (("src", publish), ("eval", evaluation), ("act", actuation)) if p in parts]
        fragment = {"fmt": FORMAT, "rule": ir["rule"], "rev": rev, "strategy": strategy,
                    "role": "+".join(roles), "self": p}
        if p in publish:
            fragment["publish"] = sorted(publish[p], key=lambda entry: entry["key"])
        fragment.update(evaluation.get(p, {}))
        fragment.update(actuation.get(p, {}))
        fragments[p] = fragment

    return {
        "strategy": strategy,
        "policy": policy,
        "rev": rev,
        "ir_rev": digest(ir),
        "evaluators": evals,
        "actuators": acts,
        "fragments": fragments,
        "sizes": {p: size_of(fragment) for p, fragment in fragments.items()},
        "data_frames": {p: data_frame_bytes(len(entries)) for p, entries in publish.items()},
        "state_frame": STATE_FRAME_BYTES if acting else None,
        "heartbeat": heartbeat,
    }


def publish_table(mac, fragments):
    """Gabungan bagian "publish" dari beberapa fragmen milik SATU device (lintas aturan).

    Satu baris per variabel; heartbeat dan min_interval diambil yang terkecil.
    """
    table = {}
    for fragment in fragments:
        for entry in fragment.get("publish", []):
            current = table.get(entry["key"])
            if current is None:
                table[entry["key"]] = dict(entry)
            else:
                current["heartbeat"] = min(current["heartbeat"], entry["heartbeat"])
                current["min_interval"] = min(current["min_interval"], entry["min_interval"])
    rows = [table[key] for key in sorted(table)]
    body = {"fmt": "microrule-pub/1", "mac": mac, "publish": rows}
    return {**body, "rev": digest(body)}

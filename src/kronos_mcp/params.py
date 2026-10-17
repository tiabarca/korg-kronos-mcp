"""Mapas de parámetros oficiales de Korg (generados por tools/build_param_maps.py en data/params).

- decode(): interpreta un objeto (Program, Combi, Global…) con nombres reales, incluidos
  los parámetros del efecto de cada slot y del motor EXi activo.
- find(): busca parámetros por nombre → offset y Parameter ID (TYP/SOC/SUB/PID/IDX).
- lookup_id(): traduce un Parameter Change (0x43) recibido a nombre(s) de parámetro.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .protocol.constants import PROGRAM_SIZE_EXI, ObjType

PARAMS_DIR = Path(__file__).resolve().parents[2] / "data" / "params"
ENDIAN = "little"  # ✅ validado contra el preload de fábrica (ver tests/test_params.py)

MAP_FOR = {
    ObjType.COMBINATION: "combination",
    ObjType.SONG_TIMBRE_SET: "combination",
    ObjType.GLOBAL: "global",
    ObjType.DRUM_KIT: "drum_kit",
    ObjType.WAVE_SEQ: "wave_seq",
    ObjType.SET_LIST: "set_list",
    ObjType.SONG_CONTROL: "song_control",
}


def available() -> bool:
    return (PARAMS_DIR / "summary.json").exists()


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    return json.loads((PARAMS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def map_name(obj: ObjType, size: int) -> str:
    if obj == ObjType.PROGRAM:
        return "program_exi" if size == PROGRAM_SIZE_EXI else "program_hd1"
    return MAP_FOR[obj]


# --- lectura de campos -------------------------------------------------------------
def read(data: bytes, f: dict, base: int = 0):
    if "chunks" in f:
        val, shift = 0, 0
        for ofs, lo, hi in f["chunks"]:
            o = ofs + base
            if o >= len(data):
                return None
            width = hi - lo + 1
            val |= ((data[o] >> lo) & ((1 << width) - 1)) << shift
            shift += width
        bits = shift
    else:
        o, size = f["ofs"] + base, f.get("size", 1)
        if o + size > len(data):
            return None
        raw = data[o : o + size]
        if f.get("ascii"):
            return raw.split(b"\x00", 1)[0].decode("latin-1").rstrip()
        if size > 4:
            return raw.hex()
        val, bits = int.from_bytes(raw, ENDIAN), 8 * size
    if f.get("signed") and val >> (bits - 1) & 1:
        val -= 1 << bits
    return val


def write(data: bytearray, f: dict, value: int, base: int = 0) -> None:
    """Escribe un valor entero en un campo (para preparar dumps modificados)."""
    if "chunks" in f:
        bits = sum(hi - lo + 1 for _, lo, hi in f["chunks"])
        value &= (1 << bits) - 1
        for ofs, lo, hi in f["chunks"]:
            width = hi - lo + 1
            mask = ((1 << width) - 1) << lo
            data[ofs + base] = (data[ofs + base] & ~mask) | ((value & ((1 << width) - 1)) << lo)
            value >>= width
    else:
        size = f.get("size", 1)
        value &= (1 << (8 * size)) - 1
        data[f["ofs"] + base : f["ofs"] + base + size] = value.to_bytes(size, ENDIAN)


def data_range(f: dict) -> tuple[int, int] | None:
    """Rango declarado en la columna data(hex), ya con signo."""
    m = re.match(r"\s*([0-9A-Fa-f]+)\s*~\s*([0-9A-Fa-f]+)", f.get("data", ""))
    if not m:
        m1 = re.match(r"\s*([0-9A-Fa-f]+)\(fixed\)", f.get("data", ""))
        if not m1:
            return None
        v = int(m1.group(1), 16)
        return (v, v)
    lo, hi = int(m.group(1), 16), int(m.group(2), 16)
    if f.get("signed"):
        # Korg escribe el rango con el ancho de sus dígitos hex (p. ej. E2~1E en un campo de 6 bits)
        width = 4 * len(m.group(1))
        lo = lo - (1 << width) if lo >> (width - 1) & 1 else lo
    return (lo, hi) if lo <= hi else (hi, lo)


def _pid(f: dict) -> str | None:
    i = f.get("id")
    if not i:
        return None
    if set(i) == {"PID"}:  # parámetros de efecto: solo PID relativo al slot
        return f"pid={i['PID']}"
    return ",".join(i.get(k, "") for k in ("TYP", "SOC", "SUB", "PID", "IDX") if k in i) or None


def _label(f: dict, group: str | None = None) -> str:
    g = group if group is not None else f.get("group", "")
    n = f.get("name") or f"(sin nombre @{f['ofs']})"
    return f"{g} / {n}".strip(" /") if g else n


# --- decodificación completa ------------------------------------------------------------
def iter_fields(obj: ObjType, data: bytes):
    """(campo, base, etiqueta) para todos los parámetros del objeto, incluidos efectos y motor EXi."""
    m = load(map_name(obj, len(data)))
    for f in m["fields"]:
        yield f, 0, _label(f)
    effects = load("effects")
    for blk in m.get("effect_blocks", []):
        t = read(data, blk["selector"])
        e = effects.get(str(t))
        if not e:
            continue
        grp = f"{blk['selector_group']} [{e['name']}]"
        for f in e["fields"]:
            yield f, blk["base"], _label(f, grp)
    engines = load("exi_engines")
    for blk in m.get("exi_blocks", []):
        a = read(data, blk["selector"])
        e = engines.get(str(a))
        if not e:
            continue
        grp = f"{blk['selector_group'].replace(' Common', '')} [{e['name']}]"
        for f in e["fields"]:
            yield f, blk["base"], _label(f, grp)


def decode(obj: ObjType, data: bytes, query: str | None = None, limit: int = 300, only_ids: bool = False) -> list[dict]:
    q = query.lower() if query else None
    out = []
    for f, base, label in iter_fields(obj, data):
        if q and q not in label.lower():
            continue
        if only_ids and "id" not in f:
            continue
        v = read(data, f, base)
        item = {"param": label, "value": v, "ofs": f["ofs"] + base}
        if f.get("value"):
            item["range"] = f["value"]
        if (pid := _pid(f)) is not None:
            item["id"] = pid
        out.append(item)
        if len(out) >= limit:
            break
    return out


# grupos pesados que no se muestran en compact sin query (se piden con groups=[...])
HEAVY_GROUPS = ("karma", "dynamic midi", "audio input", "s/pdif", "drum track", "vector control")


def value_text(f: dict, v) -> str:
    """Valor legible: el mapa solo da los extremos de la lista ('Off~On', 'Poly~Mono'), así que se traducen
    esos; el resto queda numérico. El texto del mapa viene cortado a 19 caracteres: un extremo final truncado no se usa."""
    if not isinstance(v, int):
        return str(v)
    lab = f.get("value") or ""
    rng = data_range(f)
    if rng is None or not lab:
        return str(v)
    if "~" not in lab:  # (fixed)
        return lab if v == rng[0] else str(v)
    lo, hi = lab.split("~", 1)
    if v == rng[0] and lo and not re.fullmatch(r"[-+]?\d+", lo):
        return lo
    if v == rng[1] and hi and len(lab) < 19 and not re.fullmatch(r"[-+]?\d+", hi):
        return hi
    return str(v)


def decode_compact(obj: ObjType, data: bytes, query: str | list[str] | None = None, groups: list[str] | None = None,
                   skip_defaults: bool = False, limit: int = 150) -> dict:
    """Un parámetro por línea 'Nombre = valor'. query (texto o lista): union de coincidencias.
    Sin query se omiten los grupos pesados (KARMA, Drum Track…) salvo los pedidos en groups."""
    qs = [query.lower()] if isinstance(query, str) else [q.lower() for q in (query or [])]
    gs = [g.lower() for g in (groups or [])]
    lines, total = [], 0
    for f, base, label in iter_fields(obj, data):
        low = label.lower()
        if qs and not any(q in low for q in qs):
            continue
        if gs and not any(g in low for g in gs):
            continue
        if not qs and not gs and any(low.startswith(h) or f" {h}" in low.split(" / ")[0] for h in HEAVY_GROUPS):
            continue
        v = read(data, f, base)
        if skip_defaults and (v in (0, "", None) or v == "0" * len(str(v))):
            continue
        if not f.get("name"):
            continue
        total += 1
        if len(lines) < limit:
            lines.append(f"{label} = {value_text(f, v)}")
    res: dict = {"params": lines}
    if total > len(lines):
        res["truncated"] = f"{len(lines)} de {total}: usa query/groups para acotar"
    return res


def summary(obj: ObjType, data: bytes) -> dict:
    """Resumen legible: nombre, motores/efectos activos."""
    res: dict = {"map": map_name(obj, len(data))}
    m = load(res["map"])
    name_f = next((f for f in m["fields"] if f.get("ascii") and f["ofs"] == 0), None)
    if name_f:
        res["name"] = read(data, name_f)
    effects = load("effects")
    fx = []
    for blk in m.get("effect_blocks", []):
        t = read(data, blk["selector"])
        if t:
            fx.append({"slot": blk["selector_group"], "type": t, "effect": effects.get(str(t), {}).get("name", "?")})
    if fx:
        res["effects"] = fx
    engines = load("exi_engines")
    exi = []
    for blk in m.get("exi_blocks", []):
        a = read(data, blk["selector"])
        exi.append({"slot": blk["selector_group"].replace(" Common", ""), "engine": engines.get(str(a), {}).get("name", "Off" if not a else str(a))})
    if exi:
        res["exi"] = exi
    return res


# --- búsqueda y Parameter ID ------------------------------------------------------------
SYNONYMS = {
    "mono": ["Assign Mode", "Legato"],
    "voice assign": ["Assign Mode", "Legato"],
    "voice mode": ["Assign Mode", "Legato"],
    "poly": ["Assign Mode", "Poly Legato"],
    "glide": ["Portamento"],
    "portamento": ["Portamento"],
    "cutoff": ["Cutoff", "Frequency"],
    "freq": ["Cutoff", "Frequency"],
    "vibrato": ["LFO"],
    "tremolo": ["LFO"],
    "resonance": ["Resonance"],
    "reso": ["Resonance"],
    "attack": ["Attack Time"],
    "release": ["Release Time"],
    "decay": ["Decay Time"],
    "volume": ["Level", "Volume"],
    "pan": ["Panpot", "Pan"],
    "reverb": ["Master Effect", "Return"],
    "delay": ["Delay", "Master Effect"],
}
_MAPS = ["program_hd1", "program_exi", "combination", "global", "set_list", "song_control", "drum_kit", "wave_seq"]


def _candidates(maps: list[str]):
    """(texto de búsqueda en minúsculas, resultado) de todos los parámetros con nombre."""
    for name in maps:
        for f in load(name)["fields"]:
            label = _label(f)
            yield label.lower(), {"map": name, "param": label, "ofs": f["ofs"], "id": _pid(f), "data": f.get("data"), "range": f.get("value")}
    for e_id, e in load("effects").items():
        for f in e["fields"]:
            yield f.get("name", "").lower(), {"map": "effects", "effect": f"{e_id}:{e['name']}", "param": f.get("name", ""), "rel_ofs": f["ofs"], "pid": f.get("id", {}).get("PID"), "range": f.get("value")}
    for e_id, e in load("exi_engines").items():  # los parámetros de los motores EXi no están en los mapas de Program
        for f in e["fields"]:
            yield f.get("name", "").lower(), {"map": "exi_engines", "engine": e["name"], "param": f.get("name", ""), "rel_ofs": f["ofs"], "range": f.get("value")}


def _search(terms: list[str], maps: list[str], limit: int, all_words: bool = False) -> list[dict]:
    out, seen = [], set()
    words = [w for t in terms for w in (t.split() if all_words else [t])]
    for low, item in _candidates(maps):
        if (all(w in low for w in words) if all_words else any(t in low for t in terms)):
            key = (item["map"], item.get("engine"), item["param"], item.get("ofs", item.get("rel_ofs")))
            if key not in seen:
                seen.add(key)
                out.append(item)
    # los grupos pesados (KARMA, Drum Track…) al final
    out.sort(key=lambda i: i["param"].lower().startswith(HEAVY_GROUPS))
    return out[:limit]


def find(text: str, maps: list[str] | None = None, limit: int = 40) -> list[dict]:
    """Busca por nombre: subcadena → todas las palabras (en cualquier orden) → sinónimos
    ('mono' → Legato, 'glide' → Portamento…). Sin resultados devuelve los 5 nombres más parecidos."""
    t = text.lower().strip()
    maps = maps or _MAPS
    res = _search([t], maps, limit) or _search([t], maps, limit, all_words=True)
    if res:
        return res
    alts = SYNONYMS.get(t) or [alt for k, v in SYNONYMS.items() if k in t for alt in v]
    if alts:
        res = _search([a.lower() for a in alts], maps, limit)
        if res:
            return res
    import difflib

    names = {}
    for low, item in _candidates(maps):
        names.setdefault(low.split(" / ")[-1], item["param"])
    close = difflib.get_close_matches(t, list(names), n=5, cutoff=0.4)
    return [{"no_match": text, "did_you_mean": [names[c] for c in close]}]


@lru_cache(maxsize=1)
def _id_index() -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for name in ("program_hd1", "program_exi", "combination", "global", "set_list", "song_control"):
        for f in load(name)["fields"]:
            pid = _pid(f)
            if pid and pid.count(",") == 4:
                lst = idx.setdefault(pid, [])
                lab = f"{name}: {_label(f)}"
                if lab not in lst and len(lst) < 6:
                    lst.append(lab)
    return idx


def lookup_id(typ: int, soc: int, sub: int, pid: int, idx: int) -> list[str]:
    return _id_index().get(f"{typ},{soc},{sub},{pid},{idx}", [])


def decode_param_change(payload: bytes) -> dict | None:
    """Payload de 0x43/0x41/0x6D/0x6E (TYP SOC SUB PID IDX vH vM vL) → ids, valor y nombre(s)."""
    if len(payload) != 8:
        return None
    typ, soc, sub, pid, idx, vh, vm, vl = payload
    val = (vh << 14) | (vm << 7) | vl
    if val >> 20 & 1:  # 21 bits en complemento a 2
        val -= 1 << 21
    return {"TYP": typ, "SOC": soc, "SUB": sub, "PID": pid, "IDX": idx, "value": val, "names": lookup_id(typ, soc, sub, pid, idx) if available() else []}


def encode_value(value: int) -> tuple[int, int, int]:
    v = value & ((1 << 21) - 1)
    return (v >> 14) & 0x7F, (v >> 7) & 0x7F, v & 0x7F


def _field_bytes(f: dict, base: int) -> range | set:
    if "chunks" in f:
        return {c[0] + base for c in f["chunks"]}
    return range(f["ofs"] + base, f["ofs"] + base + f.get("size", 1))


def annotate_changes(obj: ObjType, before: bytes, after: bytes, max_items: int = 60) -> list[dict]:
    """Qué parámetros (con nombre oficial) cambian entre dos versiones de un objeto."""
    if len(before) != len(after):
        return [{"note": f"tamaños distintos ({len(before)} → {len(after)}): ¿cambió el motor EXi/HD-1?"}]
    changed = {i for i in range(len(after)) if before[i] != after[i]}
    if not changed:
        return []
    out, seen = [], set()
    for f, base, label in iter_fields(obj, after):
        fb = _field_bytes(f, base)
        if changed.isdisjoint(fb):
            continue
        old, new = read(before, f, base), read(after, f, base)
        if old == new or label in seen:
            continue
        seen.add(label)
        item = {"param": label, "old": old, "new": new, "ofs": f["ofs"] + base}
        if "chunks" in f:
            item["bits"] = f["chunks"]
        if f.get("value"):
            item["range"] = f["value"]
        if (pid := _pid(f)) is not None:
            item["id"] = pid
        out.append(item)
        if len(out) >= max_items:
            break
    return out

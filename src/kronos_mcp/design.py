"""Diseño de sonidos: aplicar cambios con nombre a un objeto (Program/Combi) usando los mapas oficiales.

Operaciones (lista de dicts):
  {"param": "EXi1 [AL-1] / [Filter A] Cutoff", "value": 55}   nombre exacto, o sufijo/fragmento único
  {"copy_effect": "Insert Effect2", "from": <bytes del objeto origen>, "to": "Insert Effect1"}
      keep_routing (por defecto true): conserva Chain Switch / Chain To / Bus Select del slot destino.
      Master Effect1/2: copia también su Return (copy_return=false lo evita).
  {"copy_range": [1048, 1050], "from": <bytes>}                bytes crudos (p. ej. Returns de MFX)
  {"name": "NUEVO NOMBRE"}
Los valores son los valores internos del mapa (columna data). Para la mayoría (0~99, -99~+99, 0~127,
tune en cents) coinciden con lo que se ve en pantalla; las listas (formas de onda, tipos) van por índice.
"""
from __future__ import annotations

import re

from . import params as P
from .protocol.constants import PROGRAM_SIZE_EXI, PROGRAM_SIZE_HD1, ObjType

NAME_LEN = 24
ROUTING = ("Chain Switch", "Chain To", "Bus Select")


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("/", " / ").split())


def resolve(obj: ObjType, data: bytes, query: str):
    """(campo, base, etiqueta) para un nombre de parámetro. Exacto > sufijo > fragmento; debe ser único."""
    q = _norm(query)
    items = [(f, b, lab) for f, b, lab in P.iter_fields(obj, data) if f.get("name")]
    for test in (lambda l: l == q, lambda l: l.endswith(q), lambda l: q in l):
        hit = [(f, b, lab) for f, b, lab in items if test(_norm(lab))]
        # mismo parámetro repetido (mismo offset/bits) cuenta como uno
        uniq = {(f["ofs"] + b, str(f.get("chunks")), f.get("size")): (f, b, lab) for f, b, lab in hit}
        if len(uniq) == 1:
            return next(iter(uniq.values()))
        if len(uniq) > 1:
            raise ValueError(f"'{query}' es ambiguo: " + "; ".join(sorted({lab for _, _, lab in uniq.values()})[:12]))
    raise KeyError(f"Parámetro no encontrado: '{query}'")


def effect_extent(map_name: str, group: str) -> tuple[int, int]:
    m = P.load(map_name)
    for blk in m.get("effect_blocks", []):
        if blk["selector_group"].lower() == group.lower():
            tail = 10 if blk["selector_group"].startswith("Insert") else 4
            return blk["base"], blk["selector_ofs"] + tail
    raise KeyError(f"Bloque de efecto desconocido: {group}")


def _src_obj(obj: ObjType, size: int) -> ObjType:
    """El origen de un copy_effect puede ser un Program aunque el destino sea una Combi."""
    return ObjType.PROGRAM if size in (PROGRAM_SIZE_EXI, PROGRAM_SIZE_HD1) else obj


def _group_fields(map_name: str, group: str, names: tuple[str, ...]) -> list[dict]:
    return [f for f in P.load(map_name)["fields"] if f.get("group", "").lower() == group.lower() and f.get("name") in names]


def _return_field(map_name: str, group: str) -> dict | None:
    """Master Effect1 → 'Return 1' (grupo 'Master Effect')."""
    if not group.startswith("Master Effect") or not group[-1].isdigit():
        return None
    hit = [f for f in _group_fields(map_name, "Master Effect", (f"Return {group[-1]}",))]
    return hit[0] if hit else None


def _insert_slot(group: str) -> bool:
    return group.startswith("Insert")


def normalized(obj: ObjType, data: bytes) -> bytes:
    """Copia con los campos que el Kronos normaliza puestos a 0 (Chain To con Chain Switch = Off),
    para comparar lo escrito con lo releído sin falsos 'no coincide'."""
    buf = bytearray(data)
    m = P.load(P.map_name(obj, len(buf)))
    by_group: dict[str, dict[str, dict]] = {}
    for f in m["fields"]:
        if f.get("name") in ("Chain Switch", "Chain To") and f.get("group", "").startswith("Insert"):
            by_group.setdefault(f["group"], {})[f["name"]] = f
    for g in by_group.values():
        if "Chain Switch" in g and "Chain To" in g and not P.read(bytes(buf), g["Chain Switch"]):
            P.write(buf, g["Chain To"], 0)
    return bytes(buf)


def same(obj: ObjType, a: bytes, b: bytes) -> bool:
    return len(a) == len(b) and normalized(obj, a) == normalized(obj, b)


def unused_timbre_ops(obj: ObjType, data: bytes, ops: list[dict]) -> tuple[list[dict], list[int]]:
    """Status = Off para los timbres que `ops` no toca y que siguen como en una Combi inicializada
    (Program 0 del banco INT-A). Devuelve (ops, timbres omitidos por parecer en uso)."""
    touched = {int(m.group(1)) for op in ops if "param" in op for m in [re.match(r"\s*timbre\s*(\d+)\b", op["param"], re.I)] if m}
    out, skipped = [], []
    for n in range(1, 17):
        if n in touched:
            continue
        f_st, b_st, _ = resolve(obj, data, f"Timbre{n} / Status")
        if not P.read(data, f_st, b_st):
            continue
        fp, bp, _ = resolve(obj, data, f"Timbre{n} / Program Number")
        fb, bb, _ = resolve(obj, data, f"Timbre{n} / Bank Number")
        if P.read(data, fp, bp) or P.read(data, fb, bb):
            skipped.append(n)
            continue
        out.append({"param": f"Timbre{n} / Status", "value": 0})
    return out, skipped


def apply(obj: ObjType, data: bytes, ops: list[dict]) -> tuple[bytes, list[dict]]:
    """Aplica ops; el log solo contiene lo que cambia de verdad (old != new)."""
    buf = bytearray(data)
    log = []
    mname = P.map_name(obj, len(buf))
    for op in ops:
        if "name" in op and len(op) == 1:
            old = bytes(buf[:NAME_LEN]).split(b"\x00")[0].decode("latin-1").rstrip()
            buf[:NAME_LEN] = op["name"].encode("latin-1", "replace")[:NAME_LEN].ljust(NAME_LEN, b"\x00")
            if old != op["name"][:NAME_LEN]:
                log.append({"param": "Name", "old": old, "new": op["name"][:NAME_LEN]})
        elif "copy_effect" in op:
            src = op["from"]
            smap = P.map_name(_src_obj(obj, len(src)), len(src))
            to = op.get("to", op["copy_effect"])
            s0, s1 = effect_extent(smap, op["copy_effect"])
            d0, d1 = effect_extent(mname, to)
            if s1 - s0 != d1 - d0:
                raise ValueError("Bloques de efecto de distinto tamaño")
            before = bytes(buf)
            keep = {}
            if op.get("keep_routing", True):
                keep = {f["name"]: (f, P.read(before, f)) for f in _group_fields(mname, to, ROUTING)}
            buf[d0:d1] = src[s0:s1]
            for f, v in keep.values():
                P.write(buf, f, v)
            entry = {"copy_effect": op["copy_effect"], "to": to,
                     "effect": P.load("effects").get(str(src[s1 - (10 if _insert_slot(op["copy_effect"]) else 4)]), {}).get("name")}
            if keep:
                entry["kept_routing"] = {k: v for k, (_, v) in keep.items()}
            if op.get("copy_return", True) and not _insert_slot(to):
                fs, fd = _return_field(smap, op["copy_effect"]), _return_field(mname, to)
                if fs and fd:
                    P.write(buf, fd, P.read(src, fs))
                    entry["return"] = P.read(src, fs)
            if bytes(buf) != before:
                log.append(entry)
        elif "copy_range" in op:
            a, b = op["copy_range"]
            if bytes(buf[a:b]) != op["from"][a:b]:
                log.append({"copy_range": [a, b]})
            buf[a:b] = op["from"][a:b]
        elif "param" in op:
            f, base, label = resolve(obj, bytes(buf), op["param"])
            value = int(op["value"])
            rng = P.data_range(f)
            if rng and not rng[0] <= value <= rng[1]:
                raise ValueError(f"{label}: {value} fuera de rango {rng} ({f.get('value', '')})")
            old = P.read(bytes(buf), f, base)
            P.write(buf, f, value, base)
            if old != value:
                log.append({"param": label, "old": old, "new": value})
        else:
            raise ValueError(f"Operación no reconocida: {op}")
    return bytes(buf), log

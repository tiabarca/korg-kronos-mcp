"""Diseño de sonidos: aplicar cambios con nombre a un objeto (Program/Combi) usando los mapas oficiales.

Operaciones (lista de dicts):
  {"param": "EXi1 [AL-1] / [Filter A] Cutoff", "value": 55}   nombre exacto, o sufijo/fragmento único
  {"copy_effect": "Insert Effect2", "from": <bytes del objeto origen>, "to": "Insert Effect1"}
  {"copy_range": [1048, 1050], "from": <bytes>}                bytes crudos (p. ej. Returns de MFX)
  {"name": "NUEVO NOMBRE"}
Los valores son los valores internos del mapa (columna data). Para la mayoría (0~99, -99~+99, 0~127,
tune en cents) coinciden con lo que se ve en pantalla; las listas (formas de onda, tipos) van por índice.
"""
from __future__ import annotations

from . import params as P
from .protocol.constants import ObjType

NAME_LEN = 24


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


def apply(obj: ObjType, data: bytes, ops: list[dict]) -> tuple[bytes, list[dict]]:
    buf = bytearray(data)
    log = []
    mname = P.map_name(obj, len(buf))
    for op in ops:
        if "name" in op and len(op) == 1:
            old = bytes(buf[:NAME_LEN]).split(b"\x00")[0].decode("latin-1").rstrip()
            buf[:NAME_LEN] = op["name"].encode("latin-1", "replace")[:NAME_LEN].ljust(NAME_LEN, b"\x00")
            log.append({"param": "Name", "old": old, "new": op["name"][:NAME_LEN]})
        elif "copy_effect" in op:
            src = op["from"]
            s0, s1 = effect_extent(P.map_name(obj, len(src)), op["copy_effect"])
            d0, d1 = effect_extent(mname, op.get("to", op["copy_effect"]))
            if s1 - s0 != d1 - d0:
                raise ValueError("Bloques de efecto de distinto tamaño")
            buf[d0:d1] = src[s0:s1]
            fx = P.load("effects").get(str(src[s1 - (10 if op["copy_effect"].startswith("Insert") else 4)]), {}).get("name")
            log.append({"copy_effect": op["copy_effect"], "to": op.get("to", op["copy_effect"]), "effect": fx})
        elif "copy_range" in op:
            a, b = op["copy_range"]
            buf[a:b] = op["from"][a:b]
            log.append({"copy_range": [a, b]})
        elif "param" in op:
            f, base, label = resolve(obj, bytes(buf), op["param"])
            value = int(op["value"])
            rng = P.data_range(f)
            if rng and not rng[0] <= value <= rng[1]:
                raise ValueError(f"{label}: {value} fuera de rango {rng} ({f.get('value', '')})")
            old = P.read(bytes(buf), f, base)
            P.write(buf, f, value, base)
            log.append({"param": label, "old": old, "new": value, "range": f.get("value")})
        else:
            raise ValueError(f"Operación no reconocida: {op}")
    return bytes(buf), log

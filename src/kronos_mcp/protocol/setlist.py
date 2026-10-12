"""Parseo de la estructura Set List (69 416 bytes internos)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .constants import SET_LIST_INTERNAL_LEN, SLOT_COMMENTS_LEN, SLOT_INTERNAL_LEN, SLOT_NAME_LEN

# ✅ comprobado con el teclado real: 0 = Combi, 1 = Program, 2 = Song (como la func 0x33)
PERF_TYPES = {0: "Combination", 1: "Program", 2: "Song"}


# ✅ nombres oficiales del diálogo "Slot Color" del Kronos (OS 3.1.3), comprobados pintando 0-15
COLOR_NAMES = {
    0: "Default", 1: "Charcoal", 2: "Brick", 3: "Burgundy", 4: "Ivy", 5: "Olive", 6: "Gold", 7: "Cacao",
    8: "Indigo", 9: "Navy", 10: "Rose", 11: "Lavender", 12: "Azure", 13: "Denim", 14: "Silver", 15: "Slate",
}
# alias en castellano/descriptivos
_COLOR_ALIASES = {
    "gris": 0, "sin color": 0, "por defecto": 0, "gris oscuro": 1, "carbon": 1, "carbón": 1,
    "rojo": 2, "ladrillo": 2, "red": 2, "rojo oscuro": 3, "granate": 3, "burdeos": 3,
    "amarillo": 4, "verde claro": 4, "lima": 4, "amarillo verdoso": 4, "hiedra": 4,
    "verde": 5, "verde oscuro": 5, "oliva": 5, "ocre": 6, "dorado": 6, "oro": 6, "mostaza": 6,
    "marron": 7, "marrón": 7, "cacao": 7, "brown": 7, "azul": 8, "añil": 8, "indigo": 8, "índigo": 8, "blue": 8,
    "azul oscuro": 9, "azul marino": 9, "marino": 9, "rosa": 10, "lila": 10, "violeta": 11, "lavanda": 11,
    "morado": 11, "purple": 11, "azul claro": 12, "celeste": 12, "azur": 12, "vaquero": 13, "azul acero": 13,
    "plata": 14, "plateado": 14, "gris azulado": 14, "pizarra": 15,
}


def color_code(value) -> int:
    """Acepta 0-15 o un nombre ('rojo', 'azul oscuro', 'red'...)."""
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        return int(value)
    v = str(value).strip().lower()
    for code, name in COLOR_NAMES.items():
        if v in (name.lower(), f"<{name.lower()}>"):
            return code
    if v in _COLOR_ALIASES:
        return _COLOR_ALIASES[v]
    raise ValueError(f"Color desconocido {value!r}. Usa 0-15 o uno de: {list(COLOR_NAMES.values())}")


COMBI_BANKS = [f"INT-{c}" for c in "ABCDEFG"] + [f"USER-{c}" for c in "ABCDEFG"]


def _bank_name(b: int, ptype: int = 1) -> str:
    if ptype == 0:  # Combi: 0-6 INT-A..G, 7-13 USER-A..G
        return COMBI_BANKS[b] if b < len(COMBI_BANKS) else f"?{b:02X}"
    if b <= 0x05:
        return f"INT-{'ABCDEF'[b]}"
    if b == 0x06:
        return "GM"
    if 0x07 <= b <= 0x0F:
        return f"g({b - 6})"
    if b == 0x10:
        return "g(d)"
    if 0x11 <= b <= 0x17:
        return f"USER-{'ABCDEFG'[b - 0x11]}"
    if 0x18 <= b <= 0x1E:
        return f"USER-{'ABCDEFG'[b - 0x18] * 2}"
    return f"?{b:02X}"


def _text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("latin-1").rstrip()


@dataclass
class Slot:
    index: int
    name: str
    perf_type: str
    bank: str
    perf_index: int
    color: int
    color_name: str
    font: int
    transpose: int
    volume: int
    hold_time: int
    kbd_track: int
    comments: str


def parse_slot(index: int, raw: bytes) -> Slot:
    o = SLOT_NAME_LEN
    ptype, pbank, pidx, hold, vol, kbd = raw[o : o + 6]
    xpose_raw = ((pbank >> 5) & 0x07) << 3 | ((kbd >> 5) & 0x07)
    transpose = xpose_raw - 64 if xpose_raw & 0x20 else xpose_raw  # 6 bits con signo ⚠️
    return Slot(
        index=index,
        name=_text(raw[:SLOT_NAME_LEN]),
        perf_type=PERF_TYPES.get(ptype & 0x03, str(ptype & 0x03)),
        bank=_bank_name(pbank & 0x1F, ptype & 0x03),
        perf_index=pidx,
        color=(ptype >> 2) & 0x0F,
        color_name=COLOR_NAMES[(ptype >> 2) & 0x0F],
        font=((kbd >> 4) & 0x01) << 2 | ((ptype >> 6) & 0x03),
        transpose=transpose,
        volume=vol,
        hold_time=hold,
        kbd_track=kbd & 0x0F,
        comments=_text(raw[o + 6 : o + 6 + SLOT_COMMENTS_LEN]),
    )


def parse_set_list(internal: bytes) -> dict:
    if len(internal) < SET_LIST_INTERNAL_LEN:
        raise ValueError(f"Set List corto: {len(internal)} < {SET_LIST_INTERNAL_LEN}")
    if internal[0] == 0xFF:
        return {"initialized": False}
    slots = [
        parse_slot(i, internal[24 + i * SLOT_INTERNAL_LEN : 24 + (i + 1) * SLOT_INTERNAL_LEN])
        for i in range(128)
    ]
    tail = 24 + 128 * SLOT_INTERNAL_LEN
    return {
        "initialized": True,
        "name": _text(internal[:24]),
        "eq_bypass": bool(internal[tail]),
        "band_levels": list(internal[tail + 1 : tail + 10]),
        "control_surface_mode": internal[tail + 10],
        "control_surface_assign_from": "Set List" if internal[tail + 11] else "Slot",
        "slots_per_page": {0: 16, 1: 8, 2: 4}.get(internal[tail + 12], internal[tail + 12]),
        "slots": [asdict(s) for s in slots],
    }


# --- Edición ---------------------------------------------------------------------
# Códigos de banco DENTRO del slot (5 bits), distintos de los de Object Dump.
PROG_SLOT_BANKS = (["INT-A", "INT-B", "INT-C", "INT-D", "INT-E", "INT-F", "GM"] + [f"g({i})" for i in range(1, 10)] + ["g(d)"]
                   + [f"USER-{c}" for c in "ABCDEFG"] + [f"USER-{c * 2}" for c in "ABCDEFG"])
TYPE_CODES = {"COMBI": 0, "COMBINATION": 0, "PROGRAM": 1, "PROG": 1, "SONG": 2}


def slot_offset(slot: int) -> int:
    if not 0 <= slot < 128:
        raise ValueError("slot debe ser 0..127")
    return 24 + slot * SLOT_INTERNAL_LEN


def slot_bank_to_object_bank(ptype: int, b: int) -> int | None:
    """Banco del slot → banco de Object Dump (para leer el nombre del Program/Combi)."""
    if ptype == 0:
        return b if b < 7 else (0x40 + b - 7 if b < 14 else None)
    if ptype == 1:
        if b < 6:
            return b
        if b <= 16:
            return 0x10 + (b - 6)
        return 0x40 + (b - 17) if b <= 30 else None
    return None


def parse_perf(text: str) -> tuple[int, int, int]:
    """'COMBI INT-F 108' · 'PROGRAM USER-AA 5' · 'SONG 12' → (tipo, banco del slot, índice)."""
    parts = text.upper().replace(",", " ").split()
    if not parts or parts[0] not in TYPE_CODES:
        raise ValueError(f"Performance no válida: {text!r} (usa 'COMBI INT-F 108', 'PROGRAM USER-A 5', 'SONG 3')")
    t = TYPE_CODES[parts[0]]
    if t == 2:
        return 2, 0, int(parts[1])
    bank, idx = parts[1], int(parts[2])
    banks = COMBI_BANKS if t == 0 else [b.upper() for b in PROG_SLOT_BANKS]
    if bank not in banks:
        raise ValueError(f"Banco {bank} no válido para {parts[0]}: {banks}")
    if not 0 <= idx <= 127:
        raise ValueError("índice 0..127")
    return t, banks.index(bank), idx


def get_slot_raw(data: bytes, slot: int) -> dict:
    o = slot_offset(slot)
    pt, pb, pi, hold, vol, kbd = data[o + 24 : o + 30]
    x = ((pb >> 5) << 3) | (kbd >> 5)
    return {
        "name": _text(data[o : o + 24]), "type": pt & 3, "bank": pb & 31, "index": pi,
        "color": (pt >> 2) & 15, "font": ((kbd >> 4) & 1) << 2 | (pt >> 6),
        "transpose": x - 64 if x & 32 else x, "hold_time": hold, "volume": vol, "kbd_track": kbd & 15,
        "comments": _text(data[o + 30 : o + 30 + SLOT_COMMENTS_LEN]),
    }


def set_slot(data: bytearray, slot: int, **f) -> None:
    """Modifica campos de un slot. Campos: name, perf ('COMBI INT-F 108'), volume (0-127),
    transpose (-24..+24), color (0-15 o nombre: 'Brick', 'Navy', 'rojo'...), font (0-4), hold_time (0-22), kbd_track (0-15), comments."""
    cur = get_slot_raw(bytes(data), slot)
    if "perf" in f and f["perf"] is not None:
        cur["type"], cur["bank"], cur["index"] = parse_perf(f.pop("perf"))
    if f.get("color") is not None:
        f["color"] = color_code(f["color"])
    for k, v in f.items():
        if v is None:
            continue
        if k not in cur:
            raise ValueError(f"Campo desconocido: {k}")
        cur[k] = v
    lim = {"volume": (0, 127), "transpose": (-24, 24), "color": (0, 15), "font": (0, 4), "hold_time": (0, 22), "kbd_track": (0, 15)}
    for k, (lo, hi) in lim.items():
        if not lo <= int(cur[k]) <= hi:
            raise ValueError(f"{k}={cur[k]} fuera de rango {lo}..{hi}")
    o = slot_offset(slot)
    data[o : o + 24] = str(cur["name"]).encode("latin-1", "replace")[:24].ljust(24, b"\x00")
    x = int(cur["transpose"]) & 0x3F
    font = int(cur["font"])
    data[o + 24] = (cur["type"] & 3) | ((int(cur["color"]) & 15) << 2) | ((font & 3) << 6)
    data[o + 25] = (cur["bank"] & 31) | ((x >> 3) << 5)
    data[o + 26] = int(cur["index"]) & 0xFF
    data[o + 27] = int(cur["hold_time"])
    data[o + 28] = int(cur["volume"])
    data[o + 29] = (int(cur["kbd_track"]) & 15) | (((font >> 2) & 1) << 4) | ((x & 7) << 5)
    data[o + 30 : o + 30 + SLOT_COMMENTS_LEN] = str(cur["comments"]).encode("latin-1", "replace")[:SLOT_COMMENTS_LEN].ljust(SLOT_COMMENTS_LEN, b"\x00")


def copy_slot(dst: bytearray, dst_slot: int, src: bytes, src_slot: int) -> None:
    a, b = slot_offset(dst_slot), slot_offset(src_slot)
    dst[a : a + SLOT_INTERNAL_LEN] = src[b : b + SLOT_INTERNAL_LEN]


def set_list_name(data: bytearray, name: str) -> None:
    data[0:24] = name.encode("latin-1", "replace")[:24].ljust(24, b"\x00")

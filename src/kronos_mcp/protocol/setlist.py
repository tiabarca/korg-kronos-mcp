"""Parseo de la estructura Set List (69 416 bytes internos)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .constants import SET_LIST_INTERNAL_LEN, SLOT_COMMENTS_LEN, SLOT_INTERNAL_LEN, SLOT_NAME_LEN

PERF_TYPES = {0: "Program", 1: "Combination", 2: "Song"}  # ⚠️ por confirmar


def _bank_name(b: int) -> str:
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
        bank=_bank_name(pbank & 0x1F),
        perf_index=pidx,
        color=(ptype >> 2) & 0x0F,
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

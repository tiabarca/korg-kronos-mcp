"""Catálogo de Programs y Combis del usuario: nombres y qué Programs usa cada Combi.

Se construye desde un backup completo (backup_all) sin tocar el teclado, y se puede refrescar
leyendo solo los bancos que han cambiado (digests).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .protocol.codec import decode_7bit
from .protocol.constants import ObjType
from .protocol.messages import parse
from .protocol.setlist import PROG_SLOT_BANKS, slot_bank_to_object_bank

NAME_LEN = 24
TIMBRE_BASE, TIMBRE_STRIDE, TIMBRES = 4802, 188, 16
# offsets relativos dentro de cada timbre (mapa oficial CombiAndSongTimbreSet)
T_PROG, T_BANK, T_CHST, T_VOL, T_XPOSE, T_KTOP, T_KBOT = 0, 1, 2, 5, 7, 37, 38
STATUS = {0: "Off", 1: "INT", 2: "Off?", 3: "EXT", 4: "EX2"}  # ⚠️ 2 sin confirmar
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

PROG_BANK_LABEL = {**{i: f"INT-{c}" for i, c in enumerate("ABCDEF")},
                   **{0x40 + i: f"USER-{c}" for i, c in enumerate("ABCDEFG")},
                   **{0x47 + i: f"USER-{c * 2}" for i, c in enumerate("ABCDEFG")}}
COMBI_BANK_LABEL = {**{i: f"INT-{c}" for i, c in enumerate("ABCDEFG")},
                    **{0x40 + i: f"USER-{c}" for i, c in enumerate("ABCDEFG")}}


def note_name(n: int) -> str:
    return f"{NOTES[n % 12]}{n // 12 - 1}"


def _name(data: bytes) -> str:
    return data[:NAME_LEN].split(b"\x00", 1)[0].decode("latin-1").rstrip()


def iter_syx(path: Path):
    """(obj, bank, index, datos 8-bit) de cada Object Dump (73) del fichero."""
    raw = path.read_bytes()
    for m in re.finditer(rb"\xF0[^\xF7]*\xF7", raw, re.S):
        msg = parse(m.group(0))
        if msg is None or msg.func != 0x73 or len(msg.data) < 5:
            continue
        t, b, ih, il = msg.data[:4]
        yield t, b, ih << 7 | il, decode_7bit(msg.data[5:])


def combi_timbres(data: bytes, global_channel: int = 1) -> list[dict]:
    out = []
    for t in range(TIMBRES):
        o = TIMBRE_BASE + t * TIMBRE_STRIDE
        if o + 40 > len(data):
            break
        st = (data[o + T_CHST] >> 5) & 7
        if st == 0:
            continue
        pbank = data[o + T_BANK]
        xp = data[o + T_XPOSE]
        ch = data[o + T_CHST] & 0x1F
        out.append({
            "timbre": t + 1,
            "status": STATUS.get(st, str(st)),
            "program_bank_code": pbank,
            "program_bank": PROG_SLOT_BANKS[pbank] if pbank < len(PROG_SLOT_BANKS) else f"?{pbank}",
            "program_number": data[o + T_PROG],
            "midi_channel": "Gch" if ch == 16 else ch + 1,
            # suena al tocar el teclado: INT y en el canal global (los demás solo responden a MIDI externo)
            "keyboard": st == 1 and (ch == 16 or ch + 1 == global_channel),
            "volume": data[o + T_VOL],
            "transpose": xp - 256 if xp > 127 else xp,
            "key_zone": f"{note_name(data[o + T_KBOT])}-{note_name(data[o + T_KTOP])}",
        })
    return out


def add_object(cat: dict, t: int, b: int, i: int, data: bytes, global_channel: int = 1) -> None:
    if t == int(ObjType.PROGRAM) and b in PROG_BANK_LABEL:
        cat["programs"][f"{PROG_BANK_LABEL[b]} {i:03d}"] = {"name": _name(data), "engine": "EXi" if len(data) == 4960 else "HD-1"}
    elif t == int(ObjType.COMBINATION) and b in COMBI_BANK_LABEL:
        cat["combis"][f"{COMBI_BANK_LABEL[b]} {i:03d}"] = {"name": _name(data), "timbres": combi_timbres(data, global_channel)}


def build_from_backup(folder: Path, global_channel: int = 1) -> dict:
    import hashlib

    cat = {"source": str(folder), "programs": {}, "combis": {}, "gm_names": {}, "bank_sha1": {}}
    for pattern in ("PROGRAM_*.syx", "COMBINATION_*.syx"):
        for f in sorted(folder.glob(pattern)):
            h, key = hashlib.sha1(), None
            for t, b, i, data in iter_syx(f):
                add_object(cat, t, b, i, data, global_channel)
                h.update(data)
                key = f"{t:02X}:{b:02X}"
            if key:
                cat["bank_sha1"][key] = h.hexdigest()
    resolve(cat)
    return cat


def program_key(bank_code: int, number: int) -> str | None:
    """Clave del Program para bancos de usuario/INT; None para GM y g(n) (solo lectura, no van en el backup)."""
    ob = slot_bank_to_object_bank(1, bank_code)
    if ob is None or ob not in PROG_BANK_LABEL:
        return None
    return f"{PROG_BANK_LABEL[ob]} {number:03d}"


def gm_refs(cat: dict) -> set[tuple[int, int]]:
    """(banco de Object Dump, nº) de los Programs GM/g(n) que usan las Combis desde el teclado."""
    out = set()
    for c in cat["combis"].values():
        for t in c["timbres"]:
            if t.get("keyboard") and program_key(t["program_bank_code"], t["program_number"]) is None:
                ob = slot_bank_to_object_bank(1, t["program_bank_code"])
                if ob is not None:
                    out.add((ob, t["program_number"]))
    return out


def resolve(cat: dict) -> None:
    """Pone el nombre de cada Program dentro de las Combis y el índice inverso Program → Combis."""
    used: dict[str, list[str]] = {}
    for ckey, c in cat["combis"].items():
        for t in c["timbres"]:
            pk = program_key(t["program_bank_code"], t["program_number"])
            t["program"] = pk or f"{t['program_bank']} {t['program_number']:03d}"
            t["program_name"] = cat["programs"].get(pk, {}).get("name") if pk else cat.get("gm_names", {}).get(t["program"])
            if pk and t.get("keyboard"):
                used.setdefault(pk, [])
                if ckey not in used[pk]:
                    used[pk].append(ckey)
    for pk, p in cat["programs"].items():
        p["used_in_combis"] = used.get(pk, [])


def save(cat: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")


def load(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def to_markdown(cat: dict) -> str:
    lines = ["# Catálogo Kronos", "", f"Fuente: `{cat.get('source')}`",
             "", "Solo se listan los timbres que suenan desde el teclado (INT en canal global).", "", "## Combis", ""]
    for k, c in cat["combis"].items():
        lines.append(f"### {k} — {c['name']}")
        for t in c["timbres"]:
            if not t.get("keyboard"):
                continue
            xp = f" · transp {t['transpose']:+d}" if t["transpose"] else ""
            lines.append(f"- T{t['timbre']:02d} · {t['program']} {t.get('program_name') or ''} · vol {t['volume']} · {t['key_zone']}{xp}")
        lines.append("")
    lines += ["## Programs", "", "| Program | Nombre | Motor | Usado en |", "|---|---|---|---|"]
    for k, p in cat["programs"].items():
        lines.append(f"| {k} | {p['name']} | {p['engine']} | {', '.join(p['used_in_combis'])} |")
    return "\n".join(lines) + "\n"

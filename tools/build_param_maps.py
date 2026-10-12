"""Convierte la documentación oficial de Korg (docs/korg_oficial/*.txt) en mapas JSON
que el MCP usa para decodificar dumps y traducir Parameter Change (0x43).

Uso:  python tools/build_param_maps.py [carpeta_txt] [carpeta_salida]
      (por defecto docs/korg_oficial → data/params)

Formato de cada campo en el JSON:
  {"ofs": 92, "size": 1}                       → byte(s) completos (size>1 = multibyte)
  {"ofs": 1, "chunks": [[1,5,7],[2,0,3]]}      → campo de bits; chunks = (offset, bit_lo, bit_hi),
                                                  el primero aporta los bits bajos
  + group, name, data, value, signed, bits, id (TYP,SOC,SUB,PID,IDX u otras columnas)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ID_COLS = {"TYP", "SOC", "SUB", "PID", "IDX", "NOTE", "VSP"}


def _cells(line: str) -> list[str]:
    parts = line.split("|")
    return [p.strip() for p in parts[1:]]


def _int(s: str) -> int | None:
    s = s.strip()
    return int(s) if re.fullmatch(r"\d+", s) else None


def _bits(s: str) -> tuple[int, int] | None:
    s = s.strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d)\s*~\s*(\d)", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))
    if re.fullmatch(r"\d", s):
        return (int(s), int(s))
    return None


def parse_tables(text: str) -> list[dict]:
    """Devuelve secciones: [{title, fields, reserved}] (una por tabla)."""
    lines = text.replace("\r", "").splitlines()
    sections: list[dict] = []
    title = ""
    cols: list[str] | None = None
    cur: dict | None = None
    last: dict | None = None
    pending_gap = False
    star_start: int | None = None  # fila '*' que abre un tramo  "N * / | | / M *"
    star_gap = False

    def new_section():
        nonlocal cur
        cur = {"title": title, "fields": [], "reserved": [], "ranges": [], "columns": cols}
        sections.append(cur)

    for raw in lines:
        line = raw.rstrip()
        if not line.startswith("|"):
            if line.startswith("+"):
                continue
            if line.strip():
                title = line.strip()
                cols = None
            continue
        cells = _cells(line)
        if cells and cells[0].upper().startswith("OFS"):
            names = []
            pcount = 0
            for c in cells:
                u = c.upper()
                if u.startswith("PARAMETER"):
                    pcount += 1
                    names.append("_param")
                else:
                    names.append(u)
            # dos columnas "parameter" → grupo + nombre
            seen = 0
            fixed = []
            for n in names:
                if n == "_param":
                    seen += 1
                    fixed.append("GROUP" if pcount == 2 and seen == 1 else "NAME")
                else:
                    fixed.append(n)
            cols = fixed
            new_section()
            last = None
            continue
        if cols is None or cur is None:
            continue
        row = {cols[i]: cells[i] if i < len(cells) else "" for i in range(len(cols))}
        extra = cells[len(cols)] if len(cells) > len(cols) else ""
        ofs_s = row.get("OFS", "")
        ofs = _int(ofs_s)
        if ofs_s and ofs is None:
            # "...", ":", "|    |" (elipsis vertical dentro de un campo multibyte)
            pending_gap = True
            star_gap = star_start is not None
            continue
        bit_s = row.get("BIT", "")
        name = row.get("NAME", "")
        group = row.get("GROUP", "")
        data = row.get("DATA(HEX)", "")
        value = row.get("VALUE", "")
        if name == "*" or group == "*" or bit_s == "*":
            if ofs is None and star_start is not None:  # fila "|    | | * | *" = elipsis del tramo
                star_gap = True
            if ofs is not None:
                cur["reserved"].append(ofs)
                if star_start is not None and star_gap:
                    cur["ranges"].append((star_start, ofs))
                    star_start, star_gap = None, False
                else:
                    star_start, star_gap = ofs, False
            last = None
            continue
        if ofs is not None and star_start is not None:
            cur["ranges"].append((star_start, star_start))
            star_start, star_gap = None, False
        if ofs is None and not bit_s and not name and not group and not data:
            if not value and star_start is not None:
                star_gap = True
                continue
            if value and last is not None:  # el texto de valores sigue en otra línea
                last["value"] = (last.get("value", "") + " " + value).strip()
            elif not value and last is not None and pending_gap:
                pass
            continue
        if ofs is not None:
            cur_ofs = ofs
        else:
            cur_ofs = cur.get("_ofs")
            if cur_ofs is None:
                continue
        cur["_ofs"] = cur_ofs
        bits = _bits(bit_s)
        if not name and not data:
            # continuación del campo anterior (bits o bytes siguientes)
            if last is not None:
                last["_chunks"].append((cur_ofs, *(bits or (0, 7))))
            pending_gap = False
            continue
        ids = {}
        for c in cols:
            if c in ID_COLS:
                v = row.get(c, "").strip()
                if v and v not in ("-",):
                    ids[c] = v
        # columnas "pid" minúsculas (Effect.txt)
        if "PID" not in ids and row.get("PID"):
            ids["PID"] = row["PID"]
        field = {"group": group, "name": name, "data": data, "value": value, "_chunks": [(cur_ofs, *(bits or (0, 7)))]}
        if ids:
            field["id"] = ids
        if extra:
            field["note"] = extra
        # mismo nombre/grupo, byte completo y contiguo → amplía (p. ej. Name 0..23)
        if (
            last is not None and bits is None and last["name"] == name and last["group"] == group
            and all(c[1:] == (0, 7) for c in last["_chunks"]) and last["_chunks"][-1][0] == cur_ofs - 1
            and last.get("id") == field.get("id")
        ):
            last["_chunks"].append((cur_ofs, 0, 7))
            continue
        cur["fields"].append(field)
        last = field
        pending_gap = False
    for s in sections:
        s.pop("_ofs", None)
        s["fields"] = [_finish(f) for f in s["fields"]]
    return sections


def _finish(f: dict) -> dict:
    ch = f.pop("_chunks")
    whole = all(c[1:] == (0, 7) for c in ch)
    out = {"ofs": ch[0][0]}
    if whole:
        out["size"] = ch[-1][0] - ch[0][0] + 1
        width = 8 * out["size"]
    else:
        out["chunks"] = [list(c) for c in ch]
        width = sum(c[2] - c[1] + 1 for c in ch)
    out["bits"] = width
    m = re.match(r"\s*([0-9A-Fa-f]+)\s*~\s*([0-9A-Fa-f]+)", f.get("data", ""))
    if m:
        lo, hi = int(m.group(1), 16), int(m.group(2), 16)
        v = f.get("value", "").strip()
        if width <= 32 and lo >> (width - 1) & 1 and not hi >> (width - 1) & 1 and not re.match(r"\d", v):
            out["signed"] = True
    m2 = re.search(r"\((\d+) ascii", f.get("name", ""))
    if m2:
        out["size"] = int(m2.group(1))
        out.pop("chunks", None)
        out["ascii"] = True
    if f.get("name", "").lower() == "name" or f.get("data", "").lower() == "ascii":
        out["ascii"] = True
    out.update({k: v for k, v in f.items() if v})
    return out


# --- expansiones de bloques abreviados con "..." -------------------------------
def _clone(fields, ofs_delta, rename, id_key=None, id_val=None):
    out = []
    for f in fields:
        g = json.loads(json.dumps(f))
        g["ofs"] += ofs_delta
        if "chunks" in g:
            g["chunks"] = [[c[0] + ofs_delta, c[1], c[2]] for c in g["chunks"]]
        g["group"], g["name"] = rename(g.get("group", "")), rename(g.get("name", ""))
        if id_key and "id" in g:
            g["id"][id_key] = str(id_val)
        out.append(g)
    return out


def expand_set_list(fields):
    tpl = [f for f in fields if 24 <= f["ofs"] <= 565]
    out = list(fields)
    for i in range(1, 128):
        out += _clone(tpl, 542 * i, lambda s, i=i: re.sub(r"Slot\s+0\b", f"Slot {i}", s), "IDX", i)
    return sorted(out, key=lambda f: f["ofs"])


def expand_drum_kit(fields):
    zone = [f for f in fields if 24 <= f["ofs"] <= 57]
    note1 = [f for f in fields if f["ofs"] < 24 or 58 > f["ofs"] >= 24 or 296 <= f["ofs"] <= 322]
    note1 = [f for f in note1 if f["ofs"] >= 24]
    for z in range(2, 9):
        note1 += _clone(zone, 34 * (z - 1), lambda s, z=z: s.replace("Zone1", f"Zone{z}"), "VSP", z - 1)
    note1.sort(key=lambda f: f["ofs"])
    out = [f for f in fields if f["ofs"] < 24] + note1
    for n in range(2, 129):
        out += _clone(note1, 300 * (n - 1), lambda s, n=n: s.replace("Note1 ", f"Note{n} "), "NOTE", n - 1)
    return sorted(out, key=lambda f: f["ofs"])


def expand_wave_seq(fields):
    step = [f for f in fields if 40 <= f["ofs"] <= 73]
    out = [f for f in fields if f["ofs"] < 40]
    for s in range(1, 65):
        out += _clone(step, 34 * (s - 1), lambda t, s=s: t.replace("Step1", f"Step{s}"), "IDX", s - 1)
    return sorted(out, key=lambda f: f["ofs"])


def reserved_ranges(offsets: list[int]) -> list[tuple[int, int]]:
    """Las filas '*' llegan como inicio/fin de tramo; las agrupa en rangos."""
    offs = sorted(set(offsets))
    out, i = [], 0
    while i < len(offs):
        if i + 1 < len(offs) and offs[i + 1] - offs[i] > 1:
            out.append((offs[i], offs[i + 1]))
            i += 2
        else:
            out.append((offs[i], offs[i]))
            i += 1
    return out


def anchors(fields, ranges, name, min_len, mode):
    """Localiza bloques delegados a partir del campo selector.
    mode='before': el bloque son los min_len bytes justo antes del selector (efectos: base = tipo-64).
    mode='after' : el bloque es el primer tramo reservado >= min_len posterior al selector (motores EXi)."""
    rr = sorted(r for r in ranges if r[1] - r[0] + 1 >= min_len)
    out = []
    for f in fields:
        if f.get("name") != name:
            continue
        if mode == "before":
            base = f["ofs"] - min_len
        else:
            nxt = [r for r in rr if r[0] > f["ofs"]]
            if not nxt:
                continue
            base = nxt[0][0]
        out.append({"selector_ofs": f["ofs"], "selector_group": f.get("group", ""), "base": base,
                    "selector": {k: f[k] for k in ("ofs", "size", "chunks") if k in f}})
    return out


def main(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)

    def load(fn):
        return parse_tables((src / fn).read_text(encoding="latin-1"))

    summary = {}
    objects = {
        "PROGRAM_EXI": ("Prog_EXi_Common.txt", None, 4960),
        "PROGRAM_HD1": ("Prog_HD-1.txt", None, 3706),
        "COMBINATION": ("CombiAndSongTimbreSet.txt", None, 7810),
        "GLOBAL": ("Global.txt", None, 24708),
        "SONG_CONTROL": ("SongControl.txt", None, 5264),
        "DRUM_KIT": ("DrumKit.txt", expand_drum_kit, 38424),
        "WAVE_SEQ": ("WaveSequence.txt", expand_wave_seq, 2216),
        "SET_LIST": ("SetList.txt", expand_set_list, 69416),
    }
    for key, (fn, expander, size) in objects.items():
        secs = load(fn)
        fields = [f for s in secs for f in s["fields"]]
        reserved = [tuple(r) for s in secs for r in s["ranges"]]
        if expander:
            fields = expander(fields)
        doc = {"object": key, "source": fn, "size": size, "fields": fields}
        fx = anchors(fields, reserved, "Effect Type", 64, "before")
        if fx:
            doc["effect_blocks"] = fx
        exi = anchors(fields, reserved, "Algorithm Type", 990, "after")
        if exi:
            doc["exi_blocks"] = exi
        (dst / f"{key.lower()}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        summary[key] = {"fields": len(fields), "with_param_id": sum(1 for f in fields if "id" in f),
                        "effect_blocks": len(fx), "exi_blocks": len(exi)}

    # efectos: una tabla por tipo, offsets relativos al bloque de 64 bytes
    eff = {}
    for s in load("Effect.txt"):
        m = re.match(r"(\d+):(.*)", s["title"])
        if m:
            eff[int(m.group(1))] = {"name": m.group(2).strip(), "fields": s["fields"]}
    (dst / "effects.json").write_text(json.dumps(eff, ensure_ascii=False), encoding="utf-8")
    summary["EFFECTS"] = {"types": len(eff), "fields": sum(len(e["fields"]) for e in eff.values())}

    # motores EXi: offsets relativos al bloque del motor
    exi = {}
    for s in load("Prog_EXi.txt"):
        m = re.match(r"(\d+):([^,]+)", s["title"])
        if m:
            exi[int(m.group(1))] = {"name": m.group(2).strip(), "fields": s["fields"]}
    (dst / "exi_engines.json").write_text(json.dumps(exi, ensure_ascii=False), encoding="utf-8")
    summary["EXI_ENGINES"] = {k: len(v["fields"]) for k, v in exi.items()}

    (dst / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "docs" / "korg_oficial"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "data" / "params"
    main(src, dst)

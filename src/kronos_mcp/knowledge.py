"""Base de conocimiento que se construye "en caliente" mientras se explora el Kronos.

data/knowledge/
  snapshots/<id>.bin + <id>.json   fotos del buffer de edición (o de objetos guardados)
  params.json                      mapa de parámetros aprendido {obj_type: [ {offset, size, name, ...} ]}
  notes.jsonl                      notas libres sobre el protocolo (hallazgos, dudas, confirmaciones)
"""
from __future__ import annotations

import json
import struct
import threading
from datetime import datetime
from pathlib import Path


class KnowledgeBase:
    def __init__(self, root: Path):
        self.root = root
        self.snap_dir = root / "snapshots"
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        self.params_path = root / "params.json"
        self.notes_path = root / "notes.jsonl"
        self._lock = threading.Lock()

    # --- snapshots ---------------------------------------------------------
    def save_snapshot(self, obj_type: str, data: bytes, label: str = "", meta: dict | None = None) -> str:
        sid = f"{datetime.now():%Y%m%d_%H%M%S_%f}"[:-3] + f"_{obj_type}"
        (self.snap_dir / f"{sid}.bin").write_bytes(data)
        info = {"id": sid, "obj_type": obj_type, "label": label, "size": len(data), **(meta or {})}
        (self.snap_dir / f"{sid}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        return sid

    def load_snapshot(self, sid: str) -> tuple[bytes, dict]:
        return (self.snap_dir / f"{sid}.bin").read_bytes(), json.loads((self.snap_dir / f"{sid}.json").read_text(encoding="utf-8"))

    def list_snapshots(self, obj_type: str | None = None, limit: int = 50) -> list[dict]:
        out = []
        for p in sorted(self.snap_dir.glob("*.json"), reverse=True):
            info = json.loads(p.read_text(encoding="utf-8"))
            if obj_type is None or info["obj_type"] == obj_type:
                out.append(info)
            if len(out) >= limit:
                break
        return out

    # --- diff --------------------------------------------------------------
    @staticmethod
    def diff(a: bytes, b: bytes, max_runs: int = 64) -> dict:
        """Agrupa los bytes cambiados en tramos contiguos y propone interpretaciones."""
        n = min(len(a), len(b))
        runs, i = [], 0
        while i < n:
            if a[i] != b[i]:
                j = i
                while j < n and (a[j] != b[j] or (j + 1 < n and a[j + 1] != b[j + 1] and j - i < 8)):
                    j += 1
                runs.append((i, j))
                i = j
            else:
                i += 1
        out = []
        for s, e in runs[:max_runs]:
            old, new = a[s:e], b[s:e]
            r = {"offset": s, "offset_hex": f"0x{s:04X}", "len": e - s, "old": old.hex(" "), "new": new.hex(" ")}
            if e - s == 1:
                r.update(
                    old_u8=old[0], new_u8=new[0],
                    old_s8=struct.unpack("b", old)[0], new_s8=struct.unpack("b", new)[0],
                    bits_changed=f"{old[0] ^ new[0]:08b}",
                )
            elif e - s in (2, 4):
                fmt = {2: "h", 4: "i"}[e - s]
                r.update(
                    le_old=struct.unpack("<" + fmt, old)[0], le_new=struct.unpack("<" + fmt, new)[0],
                    be_old=struct.unpack(">" + fmt, old)[0], be_new=struct.unpack(">" + fmt, new)[0],
                )
            if s < 24:
                r["hint"] = "zona de nombre (bytes 0-23)"
            out.append(r)
        return {"size_a": len(a), "size_b": len(b), "changed_runs": len(runs), "runs": out, "truncated": len(runs) > max_runs}

    # --- parámetros ----------------------------------------------------------
    def _load_params(self) -> dict:
        if self.params_path.exists():
            return json.loads(self.params_path.read_text(encoding="utf-8"))
        return {}

    def learn_param(self, obj_type: str, name: str, offset: int, size: int = 1, **extra) -> dict:
        with self._lock:
            params = self._load_params()
            lst = params.setdefault(obj_type, [])
            entry = {"name": name, "offset": offset, "offset_hex": f"0x{offset:04X}", "size": size, **{k: v for k, v in extra.items() if v not in (None, "", [])}}
            entry["updated"] = datetime.now().isoformat(timespec="seconds")
            lst[:] = [p for p in lst if not (p["name"] == name or (p["offset"] == offset and p.get("bits") == entry.get("bits")))]
            lst.append(entry)
            lst.sort(key=lambda p: (p["offset"], str(p.get("bits", ""))))
            self.params_path.write_text(json.dumps(params, ensure_ascii=False, indent=1), encoding="utf-8")
            return entry

    def params(self, obj_type: str | None = None) -> dict:
        p = self._load_params()
        return {obj_type: p.get(obj_type, [])} if obj_type else p

    def decode(self, obj_type: str, data: bytes) -> list[dict]:
        """Aplica el mapa aprendido a unos datos y devuelve valores legibles."""
        out = []
        for p in self._load_params().get(obj_type, []):
            o, sz = p["offset"], p.get("size", 1)
            if o + sz > len(data):
                continue
            raw = data[o : o + sz]
            signed = p.get("signed", False)
            endian = "<" if p.get("endian", "le") == "le" else ">"
            fmt = {1: "b", 2: "h", 4: "i"}.get(sz)
            val = struct.unpack(endian + (fmt if signed else fmt.upper()), raw)[0] if fmt else raw.hex(" ")
            if isinstance(val, int) and p.get("bits"):
                lo, hi = (int(x) for x in str(p["bits"]).split("-")) if "-" in str(p["bits"]) else (int(p["bits"]),) * 2
                val = (val >> lo) & ((1 << (hi - lo + 1)) - 1)
            item = {"name": p["name"], "value": val}
            if isinstance(val, int) and p.get("values"):
                item["label"] = p["values"].get(str(val))
            out.append(item)
        return out

    # --- notas ---------------------------------------------------------------
    def add_note(self, text: str, topic: str = "general", evidence: str = "") -> dict:
        entry = {"t": datetime.now().isoformat(timespec="seconds"), "topic": topic, "text": text}
        if evidence:
            entry["evidence"] = evidence
        with self._lock, self.notes_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def notes(self, topic: str | None = None) -> list[dict]:
        if not self.notes_path.exists():
            return []
        items = [json.loads(l) for l in self.notes_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [n for n in items if topic is None or n["topic"] == topic]

    # --- exportación -----------------------------------------------------------
    def export_markdown(self, path: Path) -> Path:
        lines = ["# Mapa de parámetros aprendido (generado)", "", f"_Actualizado: {datetime.now():%Y-%m-%d %H:%M}_", ""]
        for obj, plist in sorted(self._load_params().items()):
            lines += [f"## {obj}", "", "| Offset | Bytes | Bits | Nombre | Rango / valores | Notas |", "|---|---|---|---|---|---|"]
            for p in plist:
                vals = p.get("values")
                rng = ", ".join(f"{k}={v}" for k, v in list(vals.items())[:8]) if vals else p.get("range", "")
                lines.append(f"| {p['offset_hex']} | {p.get('size', 1)} | {p.get('bits', '')} | {p['name']} | {rng} | {p.get('notes', '')} |")
            lines.append("")
        notes = self.notes()
        if notes:
            lines += ["## Notas de protocolo", ""]
            lines += [f"- **{n['topic']}** ({n['t'][:10]}): {n['text']}" for n in notes]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

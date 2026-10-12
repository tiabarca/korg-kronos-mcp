"""Monitor MIDI: registra todo lo que entra y sale, con decodificación y log persistente.

Cada mensaje recibe un número de secuencia (seq) para poder pedir "lo que ha llegado desde X",
p. ej. los Parameter Change que el Kronos transmite cuando mueves un control en el teclado.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .protocol.constants import Func, ObjType
from .protocol.messages import parse, to_hex

IGNORED = {0xF8, 0xFE}  # clock y active sensing: ruido


def describe(raw: bytes) -> dict:
    """Decodifica un mensaje MIDI a algo legible para el LLM."""
    if not raw:
        return {"kind": "empty"}
    st = raw[0]
    if st == 0xF0:
        msg = parse(raw)
        if msg is None:
            if raw[:2] == b"\xF0\x7E":
                return {"kind": "universal_sysex", "sub": to_hex(raw[3:5])}
            return {"kind": "sysex_other", "manufacturer": f"{raw[1]:02X}" if len(raw) > 1 else None}
        d = {"kind": "kronos", "func": msg.func_name, "func_code": f"{msg.func:02X}", "len": len(raw)}
        data = msg.data
        try:
            if msg.func in (Func.OBJECT_DUMP, Func.OBJECT_DUMP_REQ) and len(data) >= 4:
                d.update(obj=_obj(data[0]), bank=f"{data[1]:02X}", index=data[2] << 7 | data[3])
                if msg.func == Func.OBJECT_DUMP and len(data) > 4:
                    d["version"] = data[4]
            elif msg.func in (Func.CURR_OBJECT_DUMP, Func.CURR_OBJECT_DUMP_REQ, Func.STORE_BANK_REQ) and data:
                d["obj"] = _obj(data[0])
            elif msg.func == Func.REPLY and data:
                d["code"] = f"{data[0]:02X}"
            elif msg.func in (Func.MODE_DATA, Func.MODE_CHANGE) and data:
                d["mode"] = data[0] & 0x0F
            elif msg.func in (Func.PARAM_CHANGE_INT, Func.SEQ_PARAM_CHANGE, Func.KARMA_PARAM_CHANGE, Func.DRUM_TRACK_PARAM_CHANGE):
                from .params import decode_param_change

                pc = decode_param_change(data)
                if pc:
                    d["param"] = pc
            elif msg.func == Func.CURR_PERF_ID and len(data) >= 4:
                d["performance"] = {"type": {0: "combi", 1: "program", 2: "song"}.get(data[0], data[0]), "bank": data[1], "number": data[2] << 7 | data[3]}
            elif msg.func == Func.BANK_DIGEST and len(data) >= 2:
                d.update(obj=_obj(data[0]), bank=f"{data[1]:02X}", note="banco modificado en el teclado (escritura/PCG)")
            elif msg.func == Func.EDIT_IN_CONTEXT_NOTIFY and len(data) >= 4:
                d["edit_in_context"] = {"enter": bool(data[0]), "edit_mode": data[1] & 0x0F, "context_mode": data[2] & 0x0F, "data": data[3]}
            elif msg.func == Func.STORE_BANK_REQ and len(data) >= 2:
                d["bank"] = f"{data[1]:02X}"
        except Exception:  # noqa: BLE001 - describir nunca debe romper
            pass
        if len(data) <= 32:
            d["payload"] = to_hex(data)
        return d
    hi, ch = st & 0xF0, (st & 0x0F) + 1
    names = {0x80: "note_off", 0x90: "note_on", 0xA0: "poly_at", 0xB0: "cc", 0xC0: "program", 0xD0: "chan_at", 0xE0: "pitch"}
    if hi in names:
        return {"kind": names[hi], "ch": ch, "data": list(raw[1:])}
    return {"kind": "system", "status": f"{st:02X}"}


def _obj(code: int) -> str:
    try:
        return ObjType(code).name
    except ValueError:
        return f"0x{code:02X}"


class Monitor:
    def __init__(self, log_dir: Path | None = None, maxlen: int = 5000):
        self._buf: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        self.enabled = True
        self._fh = None
        self._log_dir = log_dir
        # el fichero se abre con el primer mensaje: sin tráfico no se crean logs vacíos
        self.log_path = log_dir / f"{datetime.now():%Y%m%d_%H%M%S}_midi.jsonl" if log_dir else None

    def record(self, direction: str, raw: bytes) -> None:
        if not self.enabled or not raw or raw[0] in IGNORED:
            return
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq, "t": round(time.time(), 3), "dir": direction, **describe(raw)}
            entry["hex"] = to_hex(raw) if len(raw) <= 64 else to_hex(raw[:48]) + f" … ({len(raw)} bytes)"
            self._buf.append(entry)
            if self.log_path and self._fh is None:
                self._log_dir.mkdir(parents=True, exist_ok=True)
                self._fh = self.log_path.open("a", encoding="utf-8")
            if self._fh:
                self._fh.write(json.dumps({**entry, "hex": to_hex(raw)}) + "\n")
                self._fh.flush()

    @property
    def seq(self) -> int:
        return self._seq

    def since(self, seq: int = 0, direction: str | None = None, only_unsolicited: bool = False, limit: int = 200) -> list[dict]:
        with self._lock:
            items = [e for e in self._buf if e["seq"] > seq and (direction is None or e["dir"] == direction)]
        if only_unsolicited:
            items = [e for e in items if e["dir"] == "in" and e.get("func") not in ("REPLY", "OBJECT_DUMP", "CURR_OBJECT_DUMP", "MODE_DATA")]
        return items[-limit:]

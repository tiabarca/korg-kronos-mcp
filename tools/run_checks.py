"""Comprobaciones contra el Kronos real, sin pasar por el cliente MCP.

Uso (Windows):  .venv\\Scripts\\python.exe tools\\run_checks.py
Escribe logs\\checks_<fecha>.json con el resultado de cada prueba (y el error si falla).
Solo lectura: no modifica nada en el teclado.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kronos_mcp import server as s  # noqa: E402


def run(name, fn, out):
    try:
        out[name] = fn()
    except Exception as e:  # noqa: BLE001
        out[name] = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-800:]}


def main() -> None:
    out: dict = {"started": datetime.now().isoformat(timespec="seconds")}
    run("list_midi_ports", s.list_midi_ports, out)
    run("system_status", s.system_status, out)
    run("bank_status_KRONOS", lambda: s.bank_status("KRONOS"), out)
    run("bank_status_NewKRONOS", lambda: {k: v for k, v in s.bank_status("NewKRONOS").items() if k != "all"}, out)
    run("decode_current_ifx1", lambda: s.decode_object("program", query="Insert Effect1", limit=40), out)
    run("monitor", lambda: s.monitor(limit=60), out)
    out["finished"] = datetime.now().isoformat(timespec="seconds")
    path = ROOT / "logs" / f"checks_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"OK -> {path}")


if __name__ == "__main__":
    main()

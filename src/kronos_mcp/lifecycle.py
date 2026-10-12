"""Ciclo de vida del proceso: que no queden servers colgados reteniendo el puerto MIDI.

- Cada instancia deja logs/pids/<pid>.json con su padre y su ejecutable.
- Al arrancar se terminan las instancias huérfanas (su padre ya no existe) del mismo ejecutable.
- Un hilo vigila al proceso padre: si desaparece, el server se cierra.
- main() termina con os._exit para que los hilos de rtmidi no mantengan vivo el proceso.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

_IS_WIN = os.name == "nt"


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _IS_WIN:
        import ctypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return k.GetLastError() == 5  # acceso denegado: existe
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def exe_of(pid: int) -> str | None:
    if _IS_WIN:
        import ctypes
        from ctypes import wintypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ok = k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k.CloseHandle(h)
        return buf.value if ok else None
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def terminate(pid: int) -> bool:
    try:
        if _IS_WIN:
            import ctypes

            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if not h:
                return False
            ok = k.TerminateProcess(h, 1)
            k.CloseHandle(h)
            return bool(ok)
        import signal

        os.kill(pid, signal.SIGTERM)
        return True
    except Exception:  # noqa: BLE001
        return False


def _same_exe(a: str | None, b: str | None) -> bool:
    return bool(a and b) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def clean_orphans(pid_dir: Path) -> list[dict]:
    """Termina instancias anteriores cuyo cliente MCP (proceso padre) ya no existe."""
    killed = []
    pid_dir.mkdir(parents=True, exist_ok=True)
    for f in pid_dir.glob("*.json"):
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            f.unlink(missing_ok=True)
            continue
        pid = int(info.get("pid", 0))
        if pid == os.getpid():
            continue
        if not alive(pid):
            f.unlink(missing_ok=True)
            continue
        # Solo si es el mismo ejecutable (evita matar otro proceso que haya reutilizado el PID)
        if not alive(int(info.get("ppid", 0))) and _same_exe(exe_of(pid), info.get("exe")):
            if terminate(pid):
                killed.append(info)
                f.unlink(missing_ok=True)
    return killed


def register(pid_dir: Path) -> Path:
    pid_dir.mkdir(parents=True, exist_ok=True)
    p = pid_dir / f"{os.getpid()}.json"
    p.write_text(json.dumps({"pid": os.getpid(), "ppid": os.getppid(), "exe": sys.executable,
                             "started": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
    return p


def watch_parent(on_exit, interval: float = 3.0) -> None:
    ppid = os.getppid()

    def loop():
        while True:
            time.sleep(interval)
            if not alive(ppid):
                on_exit()

    threading.Thread(target=loop, name="parent-watchdog", daemon=True).start()


def list_instances(pid_dir: Path) -> list[dict]:
    out = []
    for f in pid_dir.glob("*.json"):
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        info["alive"] = alive(int(info.get("pid", 0)))
        info["parent_alive"] = alive(int(info.get("ppid", 0)))
        info["this"] = info.get("pid") == os.getpid()
        out.append(info)
    return out

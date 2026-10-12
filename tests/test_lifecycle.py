"""Puertos bajo demanda, reconexión automática y limpieza de procesos huérfanos."""
import json
import os
import subprocess
import sys
import time

from kronos_mcp import lifecycle
from kronos_mcp.client import KronosClient, KronosTimeout
from kronos_mcp.transport.mock import MockKronos


class Counting:
    def __init__(self):
        self.opened, self.closed = 0, 0

    def __call__(self):
        self.opened += 1
        t = MockKronos(0)
        orig = t.close

        def close():
            self.closed += 1
            orig()

        t.close = close
        return t


def test_ports_open_on_demand_and_release_when_idle():
    f = Counting()
    c = KronosClient(None, 0, timeout=1, factory=f)
    assert not c.is_open and f.opened == 0
    c.get_mode()
    assert c.is_open and f.opened == 1
    c.start_idle_release(0.5)
    time.sleep(2.0)
    assert not c.is_open and f.closed == 1
    c.get_mode()  # se reabre solo
    assert f.opened == 2


def test_hold_keeps_ports_open():
    f = Counting()
    c = KronosClient(None, 0, timeout=1, factory=f)
    c.get_mode()
    c.hold = True
    c.start_idle_release(0.3)
    time.sleep(1.5)
    assert c.is_open
    c.hold = False
    time.sleep(1.5)
    assert not c.is_open


def test_timeout_reopens_ports_and_retries():
    f = Counting()
    c = KronosClient(None, 0, timeout=0.3, factory=f)
    dead = c.t
    dead.handle = lambda raw: []  # puerto "muerto": no contesta nada
    dead.send = lambda raw: None
    assert c.get_mode() is not None  # reintenta con puertos nuevos
    assert f.opened == 2 and f.closed == 1


def test_timeout_without_factory_raises():
    t = MockKronos(0)
    t.send = lambda raw: None
    c = KronosClient(t, 0, timeout=0.2)
    try:
        c.get_mode()
    except KronosTimeout:
        pass
    else:
        raise AssertionError("debía fallar")


def test_clean_orphans(tmp_path):
    # proceso "huérfano": su ppid registrado no existe
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / f"{p.pid}.json").write_text(json.dumps({"pid": p.pid, "ppid": 999999, "exe": sys.executable}))
        # instancia muerta: se borra su fichero
        (tmp_path / "999998.json").write_text(json.dumps({"pid": 999998, "ppid": os.getpid(), "exe": sys.executable}))
        killed = lifecycle.clean_orphans(tmp_path)
        assert [k["pid"] for k in killed] == [p.pid]
        p.wait(timeout=5)
        assert not (tmp_path / "999998.json").exists()
    finally:
        p.kill()


def test_does_not_kill_other_executables(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / f"{p.pid}.json").write_text(json.dumps({"pid": p.pid, "ppid": 999999, "exe": "/otro/programa"}))
        assert lifecycle.clean_orphans(tmp_path) == []
        assert p.poll() is None
    finally:
        p.kill()

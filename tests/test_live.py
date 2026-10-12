import pytest

from kronos_mcp.knowledge import KnowledgeBase
from kronos_mcp.monitor import Monitor, describe
from kronos_mcp.protocol.constants import Func, ObjType
from kronos_mcp.protocol.messages import build


@pytest.fixture
def srv(monkeypatch, tmp_path):
    monkeypatch.setenv("KRONOS_MOCK", "1")
    import kronos_mcp.server as s

    s.KB = KnowledgeBase(tmp_path / "kb")
    s.MONITOR = Monitor(tmp_path / "logs")
    s._client = None
    s.PROJECT_DIR = tmp_path
    (tmp_path / "docs").mkdir()
    c = s.client()
    prog = bytearray(b"Test Program".ljust(3706, b"\0"))
    prog[0x100] = 10
    c.t.objects[ObjType.PROGRAM] = bytes(prog)
    return s


def test_describe():
    d = describe(build(0, Func.OBJECT_DUMP_REQ, 0, 0x41, 0, 5))
    assert d["func"] == "OBJECT_DUMP_REQ" and d["obj"] == "PROGRAM" and d["index"] == 5
    assert describe(bytes([0xB2, 7, 100]))["kind"] == "cc"


def test_monitor_logs_traffic(srv):
    srv.get_mode()
    m = srv.monitor()
    assert [e["dir"] for e in m["messages"]] == ["out", "in"]
    assert srv.MONITOR.log_path.read_text().count("\n") == 2


def test_learn_cycle(srv):
    c = srv.client()
    start = srv.learn_start("program", "Filter cutoff")
    # el usuario mueve el knob: cambia el buffer y el Kronos emite un Parameter Change
    prog = bytearray(c.t.objects[ObjType.PROGRAM])
    prog[0x100] = 42
    c.t.objects[ObjType.PROGRAM] = bytes(prog)
    c.t.inject(build(0, Func.PARAM_CHANGE_INT, 0x00, 0x01, 0x02, 0x2A))
    res = srv.learn_finish(start["session"])
    run = res["diff"]["runs"][0]
    assert res["diff"]["changed_runs"] == 1 and run["offset"] == 0x100 and run["new_u8"] == 42
    assert res["kronos_sent"][0]["func"] == "PARAM_CHANGE_INT"

    srv.learn_param("program", "Filter A Cutoff", 0x100, value_range="0-99", evidence=start["session"])
    dec = srv.decode_current("program")
    assert dec["values"] == [{"name": "Filter A Cutoff", "value": 42}] and dec["name"] == "Test Program"
    srv.note("0x43 se emite al mover knobs", evidence="seq 3")
    md = srv.export_knowledge()
    assert "Filter A Cutoff" in open(md, encoding="utf-8").read()


def test_bits_and_values(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.learn_param("PROGRAM", "Mono", 5, bits="3", values={"0": "Poly", "1": "Mono"})
    assert kb.decode("PROGRAM", bytes([0, 0, 0, 0, 0, 0b1000])) == [{"name": "Mono", "value": 1, "label": "Mono"}]


def test_probe_is_read_only(srv):
    assert srv.probe("12", expect_hex="42")["reply"]["func"] == "MODE_DATA"
    with pytest.raises(PermissionError):
        srv.probe("76", "0D 00")

"""Mapas oficiales: lectura de campos, cambios de parámetro y funciones nuevas (contra el mock)."""
import hashlib

import pytest

from kronos_mcp import params as P
from kronos_mcp.client import KronosClient
from kronos_mcp.protocol.constants import ObjType
from kronos_mcp.transport.mock import MockKronos

pytestmark = pytest.mark.skipif(not P.available(), reason="falta data/params (python tools/build_param_maps.py)")


def test_maps_loaded():
    s = P.load("summary")
    assert s["EXI_ENGINES"] == {"2": 609, "3": 151, "4": 707, "5": 512, "6": 322, "7": 1108, "8": 23, "9": 183}
    assert s["EFFECTS"]["types"] == 197
    blocks = P.load("program_exi")["exi_blocks"]
    assert [(b["selector_ofs"], b["base"]) for b in blocks] == [(2857, 2908), (3909, 3960)]
    fx = P.load("program_hd1")["effect_blocks"]
    assert all(b["base"] == b["selector_ofs"] - 64 for b in fx) and len(fx) == 16


def test_bitfield_spanning_bytes_lsb_first():
    f = {"ofs": 1, "chunks": [[1, 5, 7], [2, 0, 3]], "bits": 7}
    data = bytes([0, 0b10100000, 0b00001001])  # bajos=101, altos=1001 → 1001101
    assert P.read(data, f) == 0b1001101
    buf = bytearray(3)
    P.write(buf, f, 0b1001101)
    assert bytes(buf) == data


def test_signed_and_little_endian():
    f = {"ofs": 0, "size": 2, "bits": 16, "signed": True, "data": "FB50~04B0"}
    assert P.read((-1200).to_bytes(2, "little", signed=True), f) == -1200
    assert P.data_range(f) == (-1200, 1200)
    narrow = {"ofs": 0, "chunks": [[0, 0, 5]], "bits": 6, "signed": True, "data": "E2~1E"}
    assert P.data_range(narrow) == (-30, 30)


def test_param_change_roundtrip():
    from kronos_mcp.protocol.constants import Func
    from kronos_mcp.protocol.messages import build

    msg = build(0, Func.PARAM_CHANGE_INT, 0x0F, 0, 0, 0x34, 0, *P.encode_value(-5))
    pc = P.decode_param_change(msg[5:-1])
    assert pc["value"] == -5 and pc["PID"] == 0x34


def test_lookup_id_global_master_tune():
    names = P.lookup_id(15, 0, 0, 0, 0)
    assert any("Master Tune" in n for n in names)


def _hd1_program(name=b"Test HD1"):
    return name.ljust(3706, b"\0")


def test_decode_and_set_parameter():
    t = MockKronos(0)
    t.objects[ObjType.PROGRAM] = _hd1_program()
    c = KronosClient(t, 0, timeout=0.3)
    before = c.read_current_object(ObjType.PROGRAM)
    cand = [(f, b, l) for f, b, l in P.iter_fields(ObjType.PROGRAM, before) if l.endswith("Insert Effect1 / Send1")]
    f, base, label = cand[0]
    ids = [int(f["id"][k]) for k in ("TYP", "SOC", "SUB", "PID", "IDX")]
    c.set_parameter(*ids, 77)
    after = c.read_current_object(ObjType.PROGRAM)
    ch = P.annotate_changes(ObjType.PROGRAM, before, after)
    assert ch and ch[0]["param"] == label and ch[0]["new"] == 77


def test_official_functions_on_mock():
    t = MockKronos(0)
    c = KronosClient(t, 0, timeout=0.3)
    assert c.current_performance() == {"type": "program", "bank_code": 17, "number": 3}
    bt = c.program_bank_types()
    assert bt["edit buffer"] == "EXi" and bt["INT-A"] == "EXi" and bt["USER-A"] == "HD-1"
    assert [p["name"] for p in c.piano_types()] == ["German D", "Berlin D"]
    objs = [o for (tt, b, i), o in sorted(t.bank_objects.items()) if (tt, b) == (0, 0x40)]
    assert c.bank_digest(ObjType.PROGRAM, 0x40) == hashlib.sha1(b"".join(objs)).hexdigest()
    coll = c.digest_collection()
    assert coll[0]["obj"] == 0 and coll[0]["bank"] == 0x40
    got = c.dump_bank(ObjType.PROGRAM, 0x40, 128)
    assert len(got) == 128 and got[5][1].startswith(b"Mock Prog 005")


def test_server_new_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("KRONOS_MOCK", "1")
    import kronos_mcp.server as s
    from kronos_mcp.knowledge import KnowledgeBase
    from kronos_mcp.monitor import Monitor

    s.KB, s.MONITOR, s._client, s.BACKUP_DIR = KnowledgeBase(tmp_path / "kb"), Monitor(None), None, tmp_path
    c = s.client()
    c.t.objects[ObjType.PROGRAM] = _hd1_program(b"Mi Piano")
    st = s.system_status()
    assert st["identity"]["os_version"] == "3.1.3" and st["mode"]["mode"] == "PROGRAM"
    dec = s.decode_object("program", query="Insert Effect1 / Send", limit=5)
    assert dec["name"] == "Mi Piano" and dec["params"]
    r = s.set_parameter(64, param="Insert Effect1 / Send2", confirm=True)
    assert r["verified_changes"][0]["new"] == 64
    assert s.find_parameter("Master Tune", scope="global")[0]["id"] == "15,0,0,0,0"
    b = s.backup_bank("program", "USER-A")
    assert b["objects"] == 128
    s.ALLOW_WRITE = True
    r = s.restore_syx(b["file"], confirm=True)
    assert r["banks_stored"] == ["PROGRAM USER-A"] and (0, 0x40) in c.t.stored

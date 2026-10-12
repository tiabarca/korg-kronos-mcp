import os

import pytest

from kronos_mcp.client import KronosClient, KronosTimeout
from kronos_mcp.protocol.codec import decode_7bit, encode_7bit, internal_len, midi_len
from kronos_mcp.protocol.constants import Func, Mode, ObjType
from kronos_mcp.protocol.messages import KronosReplyError, build, parse, to_hex
from kronos_mcp.transport.mock import MockKronos


@pytest.mark.parametrize("n", [0, 1, 6, 7, 8, 542, 69416])
def test_codec_roundtrip(n):
    data = os.urandom(n)
    m = encode_7bit(data)
    assert decode_7bit(m) == data
    assert all(b < 0x80 for b in m)
    assert len(m) == midi_len(n) and internal_len(len(m)) == n


def test_build_mode_change():
    assert to_hex(build(0, Func.MODE_CHANGE, Mode.PROGRAM)) == "F0 42 30 68 4E 02 F7"
    assert to_hex(build(1, Func.MODE_CHANGE, Mode.COMBINATION)) == "F0 42 31 68 4E 00 F7"


def test_build_rejects_8bit():
    with pytest.raises(ValueError):
        build(0, Func.CURR_OBJECT_DUMP, 0x80)


def test_parse():
    m = parse(bytes.fromhex("F0 42 30 68 42 02 F7"))
    assert m.func == Func.MODE_DATA and m.data == b"\x02" and m.channel == 0
    assert parse(bytes.fromhex("F0 41 30 68 42 02 F7")) is None


@pytest.fixture
def kc():
    return KronosClient(MockKronos(0), channel=0, timeout=0.3)


def test_mode(kc):
    kc.set_mode(Mode.SET_LIST)
    assert kc.get_mode() == Mode.SET_LIST


def test_identify(kc):
    ident = kc.identify()
    assert ident["device_channel"] == 1 and ident["os_version"] == "3.1.3" and ident["member"] == "0x0E" and ident["is_kronos"]


def test_read_set_list(kc):
    from kronos_mcp.protocol.setlist import parse_set_list

    sl = parse_set_list(kc.read_current_object(ObjType.SET_LIST))
    assert sl["name"] == "Mock Set"
    assert sl["slots"][0]["name"] == "Slot Uno"


def test_error_reply(kc):
    with pytest.raises(KronosReplyError):
        kc.read_current_object(ObjType.DRUM_KIT)


def test_wrong_channel_times_out():
    kc = KronosClient(MockKronos(0), channel=3, timeout=0.2)
    with pytest.raises(KronosTimeout):
        kc.get_mode()


def test_store(kc):
    kc.store_bank(ObjType.SET_LIST)
    assert kc.t.stored == [(ObjType.SET_LIST, 0)]


def test_bank_codes():
    from kronos_mcp.protocol.constants import bank_code, bank_label

    assert bank_code("INT-A") == 0 and bank_code("USER-B") == 0x41 and bank_code("USER-GG") == 0x4D
    assert bank_label(0x47) == "USER-AA"


def test_read_write_object(kc):
    data, raw = kc.read_object(ObjType.PROGRAM, 0x40, 3)
    assert data.startswith(b"Mock Prog 003") and len(data) == 4960
    assert raw[:10] == bytes.fromhex("F0 42 30 68 73 00 40 00 03 05")
    kc.write_object(ObjType.PROGRAM, 0x40, 3, b"Nuevo".ljust(3706, b"\0"))
    assert kc.read_object(ObjType.PROGRAM, 0x40, 3)[0].startswith(b"Nuevo")


def test_server_tools_mock(monkeypatch, tmp_path):
    monkeypatch.setenv("KRONOS_MOCK", "1")
    import kronos_mcp.server as s

    s._client = None
    s.BACKUP_DIR = tmp_path
    assert s.read_object("program", "USER-A", 1)["engine"] == "EXi"
    assert len(s.list_bank("program", "USER-A", 0, 4)) == 4
    b = s.backup_bank("program", "USER-A")
    assert b["objects"] == 128
    s.ALLOW_WRITE = True
    assert s.restore_syx(b["file"], confirm=True)["objects_written"] == 128


def test_setlist_color_names():
    from kronos_mcp.protocol import setlist as sl

    assert sl.color_code("rojo") == 2 and sl.color_code("Azul Oscuro") == 9 and sl.color_code("Burgundy") == 3
    assert [sl.COLOR_NAMES[i] for i in (0, 7, 15)] == ["Default", "Cacao", "Slate"] and sl.color_code("navy") == 9
    assert sl.color_code(7) == 7 and sl.color_code("12") == 12
    data = bytearray(sl.SET_LIST_INTERNAL_LEN if hasattr(sl, "SET_LIST_INTERNAL_LEN") else 69416)
    sl.set_slot(data, 5, color="violeta")
    assert sl.get_slot_raw(bytes(data), 5)["color"] == 11
    try:
        sl.color_code("fucsia")
    except ValueError:
        pass
    else:
        raise AssertionError

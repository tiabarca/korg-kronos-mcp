"""Diseño de Programs con los mapas oficiales."""
import pytest

from kronos_mcp import design as D, params as P
from kronos_mcp.protocol.constants import ObjType


def _exi_al1():
    d = bytearray(4960)
    d[2857] = 2  # EXi1 = AL-1
    return bytes(d)


def test_apply_params_name_and_ranges():
    new, log = D.apply(ObjType.PROGRAM, _exi_al1(), [
        {"param": "Filter A] Cutoff", "value": 55},
        {"param": "OSC2 Pitch] Tune", "value": -8},
        {"param": "EG1 (Filter)] Sustain Level", "value": -20},
        {"name": "80s STRINGS"},
    ])
    f, b, _ = D.resolve(ObjType.PROGRAM, new, "OSC2 Pitch] Tune")
    assert P.read(new, f, b) == -8
    f, b, _ = D.resolve(ObjType.PROGRAM, new, "Filter A] Cutoff")
    assert P.read(new, f, b) == 55
    assert new[:11] == b"80s STRINGS" and len(log) == 4
    with pytest.raises(ValueError):
        D.apply(ObjType.PROGRAM, _exi_al1(), [{"param": "Filter A] Cutoff", "value": 150}])
    with pytest.raises(KeyError):
        D.apply(ObjType.PROGRAM, _exi_al1(), [{"param": "Filtro inventado", "value": 1}])


def test_copy_effect_between_slots_and_sizes():
    src = bytearray(3706)  # HD-1 de origen
    s0, s1 = D.effect_extent("program_hd1", "Insert Effect2")
    src[s0:s1] = bytes(range(s1 - s0))
    src[162] = 47  # Polysix Ensemble
    new, log = D.apply(ObjType.PROGRAM, _exi_al1(), [{"copy_effect": "Insert Effect2", "from": bytes(src), "to": "Insert Effect1", "keep_routing": False}])
    d0, d1 = D.effect_extent("program_exi", "Insert Effect1")
    assert new[d0:d1] == bytes(src[s0:s1]) and log[0]["effect"] == "Polysix Ensemble"
    assert new[88] == 47

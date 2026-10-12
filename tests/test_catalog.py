"""Catálogo: Programs que usa cada Combi."""
from kronos_mcp import catalog as C


def _combi(name, timbres):
    d = bytearray(7810)
    d[:len(name)] = name.encode()
    for t, (status, ch, bank, num) in timbres.items():
        o = C.TIMBRE_BASE + (t - 1) * C.TIMBRE_STRIDE
        d[o + C.T_PROG], d[o + C.T_BANK] = num, bank
        d[o + C.T_CHST] = (status << 5) | ch
        d[o + C.T_VOL], d[o + C.T_KTOP], d[o + C.T_KBOT] = 100, 127, 0
    return bytes(d)


def test_combi_programs_and_usage():
    cat = {"programs": {}, "combis": {}, "gm_names": {"GM 010": "Music Box"}, "bank_sha1": {}}
    prog = bytearray(4960)
    prog[:9] = b"Resoshape"
    C.add_object(cat, 0, 0x41, 7, bytes(prog))  # USER-B 007
    # T1 USER-B 007 en Gch, T2 GM 010 en Gch, T3 en canal 5 (solo MIDI externo), T4 Off
    data = _combi("TEST", {1: (1, 16, 18, 7), 2: (1, 16, 6, 10), 3: (1, 4, 18, 7), 4: (0, 16, 0, 0)})
    C.add_object(cat, 1, 0x40, 3, data)  # USER-A 003
    C.resolve(cat)
    ts = cat["combis"]["USER-A 003"]["timbres"]
    assert [t["timbre"] for t in ts] == [1, 2, 3]
    assert ts[0]["program"] == "USER-B 007" and ts[0]["program_name"] == "Resoshape" and ts[0]["keyboard"]
    assert ts[1]["program"] == "GM 010" and ts[1]["program_name"] == "Music Box"
    assert ts[2]["keyboard"] is False
    assert cat["programs"]["USER-B 007"]["used_in_combis"] == ["USER-A 003"]
    assert C.gm_refs(cat) == {(0x10, 10)}
    assert "Resoshape" in C.to_markdown(cat)

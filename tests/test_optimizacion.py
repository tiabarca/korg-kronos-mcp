"""docs/optimizacion_mcp.md: decode compacto, voice_summary, design ligero, catálogo coherente, play_notes, find."""
import pytest

from kronos_mcp import catalog as C, design as D, params as P, play, voice
from kronos_mcp.protocol.constants import ObjType


def _exi_al1():
    d = bytearray(4960)
    d[2857] = 2  # EXi1 = AL-1
    d[88] = 5
    return bytes(d)


# --- P1 decode compacto -------------------------------------------------------------
def test_decode_compact_vs_full():
    d = _exi_al1()
    full = P.decode(ObjType.PROGRAM, d, "[Filter A] Cutoff")
    comp = P.decode_compact(ObjType.PROGRAM, d, "[Filter A] Cutoff")["params"]
    assert len(comp) == len(full) and all(" = " in x and "ofs" not in x for x in comp)
    assert comp[0].startswith("EXi1 [AL-1] / [Filter A] Cutoff = ")


def test_decode_compact_query_list_and_translation():
    d = _exi_al1()
    res = P.decode_compact(ObjType.PROGRAM, d, ["[Filter A] Cutoff", "Amplifier EG]", "Legato"])["params"]
    assert 0 < len(res) < 60
    assert any("Cutoff" in x for x in res) and any("Amplifier EG" in x for x in res) and any("Common / Legato =" in x for x in res)
    assert "Common / Legato = Normal" in res  # 0 → extremo de la lista
    assert "Common / Assign Mode = Poly" in P.decode_compact(ObjType.PROGRAM, d, "Assign Mode")["params"]


def test_decode_compact_without_query_is_small_and_skips_heavy_groups():
    d = _exi_al1()
    res = P.decode_compact(ObjType.PROGRAM, d, skip_defaults=True, limit=150)
    assert len(res["params"]) <= 150
    assert not any(x.startswith(("KARMA", "Drum Track", "Vector Control", "Audio Input")) for x in res["params"])
    karma = P.decode_compact(ObjType.PROGRAM, d, groups=["KARMA Common"], limit=5)["params"]
    assert karma and all(x.startswith("KARMA Common") for x in karma)


def test_decode_compact_skip_defaults():
    d = bytearray(_exi_al1())
    f, b, _ = D.resolve(ObjType.PROGRAM, bytes(d), "Filter A] Cutoff")
    P.write(d, f, 55, b)
    res = P.decode_compact(ObjType.PROGRAM, bytes(d), "Filter A] ", skip_defaults=True)["params"]
    assert res == ["EXi1 [AL-1] / [Filter A] Cutoff = 55"]


def test_decode_object_tool(srv_mock):
    s = srv_mock
    comp = s.decode_object("PROGRAM", query="Legato")
    assert "size" not in comp and all(isinstance(x, str) for x in comp["params"])
    full = s.decode_object("PROGRAM", query=["Legato", "Assign Mode"], format="full", header=True)
    assert "size" in full and {"param", "value", "ofs"} <= set(full["params"][0])
    assert len(s.decode_object("PROGRAM")["params"]) <= 150


# --- P2 voice_summary ---------------------------------------------------------------------
def test_voice_summary_program_exi():
    d = bytearray(_exi_al1())
    f, b, _ = D.resolve(ObjType.PROGRAM, bytes(d), "Filter A] Cutoff")
    P.write(d, f, 77, b)
    lines = voice.program(bytes(d))
    assert len(lines) <= 40
    text = "\n".join(lines)
    assert "AL-1" in text and "cutoff=77" in text and "Amp EG: A=" in text and "voz Poly" in text
    assert "IFX1 " in text  # efecto de inserción 5


def test_voice_summary_voice_modes():
    d = bytearray(_exi_al1())
    f, b, _ = D.resolve(ObjType.PROGRAM, bytes(d), "Common / Assign Mode")
    P.write(d, f, 1, b)
    assert "voz Mono" in voice.program(bytes(d))[0]
    f, b, _ = D.resolve(ObjType.PROGRAM, bytes(d), "Common / Legato")
    P.write(d, f, 1, b)
    assert "voz Legato" in voice.program(bytes(d))[0]


def test_voice_summary_combi():
    new, _ = D.apply(ObjType.COMBINATION, bytes(7810), [
        {"param": "Timbre1 / Status", "value": 1}, {"param": "Timbre1 / MIDI Channel", "value": 16},
        {"param": "Timbre1 / Volume", "value": 100}, {"name": "MI COMBI"}])
    lines = voice.combi(new, 1, {"INT-A 000": "Piano"})
    assert lines[0] == "COMBI «MI COMBI»" and "T01" in lines[1] and "Piano" in lines[1]


# --- P3 design -----------------------------------------------------------------------------
def _fx_src(chain_to=7):
    src = bytearray(3706)
    s0, s1 = D.effect_extent("program_hd1", "Insert Effect2")
    src[s0:s1] = bytes(range(1, s1 - s0 + 1))
    src[162] = 47
    src[163] = 0x80  # Chain Switch On en el donante
    src[164] = chain_to
    src[167] = 0x05  # Bus Select del donante
    return bytes(src)


def test_copy_effect_keeps_routing_by_default():
    dst = bytearray(_exi_al1())
    dst[89], dst[90], dst[93] = 0x00, 0x00, 0x02  # destino: Chain Off, Bus Select 2
    new, log = D.apply(ObjType.PROGRAM, bytes(dst), [{"copy_effect": "Insert Effect2", "from": _fx_src(), "to": "Insert Effect1"}])
    assert new[88] == 47 and new[89] >> 7 == 0 and new[90] & 0x0F == 0 and new[93] & 0x0F == 2
    assert log[0]["kept_routing"]["Bus Select"] == 2
    raw, _ = D.apply(ObjType.PROGRAM, bytes(dst), [{"copy_effect": "Insert Effect2", "from": _fx_src(), "to": "Insert Effect1", "keep_routing": False}])
    assert raw[89] >> 7 == 1 and raw[93] & 0x0F == 5


def test_copy_effect_master_copies_return():
    src = bytearray(_exi_al1())
    src[976], src[1048], src[1049] = 20, 90, 70
    dst = _exi_al1()
    new, log = D.apply(ObjType.PROGRAM, dst, [{"copy_effect": "Master Effect1", "from": bytes(src)}])
    assert new[976] == 20 and new[1048] == 90 and new[1049] == 0 and log[0]["return"] == 90
    new2, _ = D.apply(ObjType.PROGRAM, dst, [{"copy_effect": "Master Effect2", "from": bytes(src), "copy_return": False}])
    assert new2[1049] == 0


def test_applied_omits_unchanged():
    d = _exi_al1()
    f, b, _ = D.resolve(ObjType.PROGRAM, d, "Filter A] Cutoff")
    _, log = D.apply(ObjType.PROGRAM, d, [{"param": "Filter A] Cutoff", "value": P.read(d, f, b)}, {"param": "Filter A] Resonance", "value": 9}])
    assert [x["param"].endswith("Resonance") for x in log] == [True]


def test_verify_ignores_chain_to_when_chain_off():
    a = bytearray(_exi_al1())
    b = bytearray(a)
    b[90] = 0x05  # Chain To distinto con Chain Switch Off → normalizado por el Kronos
    assert bytes(a) != bytes(b) and D.same(ObjType.PROGRAM, bytes(a), bytes(b))
    a[89] = b[89] = 0x80  # con Chain On sí importa
    assert not D.same(ObjType.PROGRAM, bytes(a), bytes(b))


def test_init_unused_timbres():
    base = bytearray(7810)
    for n in range(1, 17):  # Combi "inicializada": todos los timbres en INT, Program 0
        o = C.TIMBRE_BASE + (n - 1) * C.TIMBRE_STRIDE
        base[o + C.T_CHST] = 1 << 5
    # el timbre 5 está en uso (Program 12)
    base[C.TIMBRE_BASE + 4 * C.TIMBRE_STRIDE + C.T_PROG] = 12
    ops = [{"param": "Timbre1 / Volume", "value": 100}]
    more, skipped = D.unused_timbre_ops(ObjType.COMBINATION, bytes(base), ops)
    nums = {int(m["param"].split("/")[0].replace("Timbre", "")) for m in more}
    assert nums == set(range(2, 17)) - {5} and skipped == [5]
    new, log = D.apply(ObjType.COMBINATION, bytes(base), ops + more)
    assert [t["timbre"] for t in C.combi_timbres(new)] == [1, 5]


# --- P4 catálogo ------------------------------------------------------------------------------
def test_catalog_update_entry_after_store():
    cat = {"programs": {}, "combis": {}, "gm_names": {}, "bank_sha1": {}}
    prog = bytearray(4960)
    prog[:5] = b"Viejo"
    C.update_entry(cat, 0, 0x41, 7, bytes(prog))
    combi = bytearray(7810)
    combi[:6] = b"Combi1"
    o = C.TIMBRE_BASE
    combi[o + C.T_PROG], combi[o + C.T_BANK], combi[o + C.T_CHST], combi[o + C.T_VOL], combi[o + C.T_KTOP] = 7, 18, (1 << 5) | 16, 100, 127
    C.update_entry(cat, 1, 0x40, 3, bytes(combi))
    assert cat["combis"]["USER-A 003"]["timbres"][0]["program_name"] == "Viejo"
    assert cat["programs"]["USER-B 007"]["used_in_combis"] == ["USER-A 003"]
    prog[:5] = b"Nuevo"
    C.update_entry(cat, 0, 0x41, 7, bytes(prog))
    assert cat["combis"]["USER-A 003"]["timbres"][0]["program_name"] == "Nuevo"


# --- P5 play_notes ------------------------------------------------------------------------------
class _T:
    def __init__(self, boom_at=None):
        self.sent, self.boom_at = [], boom_at

    def send(self, raw):
        if self.boom_at is not None and len(self.sent) == self.boom_at:
            self.boom_at = None
            raise RuntimeError("boom")
        self.sent.append(raw)


class _C:
    channel = 0
    hold = False

    def __init__(self, t):
        self.t = t


def test_note_numbers():
    assert [play.note_number(x) for x in (60, "C4", "D5", "F#3", "Bb2")] == [60, 60, 74, 54, 46]
    with pytest.raises(ValueError):
        play.note_number("H4")


def test_play_events_and_all_notes_off():
    t = _T()
    c = _C(t)
    waits = []
    res = play.play(c, [{"n": "D5", "dur": 0.5, "vel": 90}, {"dur": 1}, {"n": [50, 54], "dur": 1}], bpm=120, sleep=waits.append)
    assert t.sent[0] == bytes([0x90, 74, 90]) and bytes([0x80, 74, 0]) in t.sent
    assert t.sent.count(bytes([0x90, 50, 100])) == 1 and t.sent[-1] == bytes([0xB0, 123, 0])
    assert res["notes"] == 3 and res["seconds"] == 1.25
    assert c.hold is False


def test_play_all_notes_off_on_error_and_limit():
    t = _T(boom_at=2)
    c = _C(t)
    with pytest.raises(RuntimeError):
        play.play(c, [{"n": 60}, {"n": 62}, {"n": 64}], sleep=lambda s: None)
    assert t.sent[-1] == bytes([0xB0, 123, 0]) and bytes([0x80, 60, 0]) in t.sent and c.hold is False
    with pytest.raises(ValueError):
        play.play(_C(_T()), [{"n": 60, "dur": 200}], bpm=120, sleep=lambda s: None)


def test_play_select_sends_bank_and_program_change():
    class Cl(_C):
        def get_mode(self):
            from kronos_mcp.protocol.constants import Mode
            return Mode.PROGRAM

        def set_mode(self, m):
            self.mode = m

    c = Cl(_T())
    play.play(c, [{"n": 60}], select="INT-B 025", sleep=lambda s: None)
    assert c.t.sent[:3] == [bytes([0xB0, 0, 0]), bytes([0xB0, 0x20, 1]), bytes([0xC0, 25])]
    assert c.mode.name == "COMBINATION"


# --- P6 find_parameter -----------------------------------------------------------------------------
def test_find_covers_exi_engines_and_synonyms():
    assert any(r.get("map") == "exi_engines" for r in P.find("Portamento", limit=200))
    mono = P.find("mono")
    assert mono and "no_match" not in mono[0]
    assert any("Legato" in r["param"] for r in P.find("voice assign"))
    assert any("Portamento" in r["param"] for r in P.find("glide"))


def test_find_no_match_suggests():
    res = P.find("kutoff")
    assert res[0]["no_match"] == "kutoff" and 0 < len(res[0]["did_you_mean"]) <= 5
    assert any("Cutoff" in x for x in res[0]["did_you_mean"])


@pytest.fixture
def srv_mock(monkeypatch, tmp_path):
    from kronos_mcp.knowledge import KnowledgeBase
    from kronos_mcp.monitor import Monitor

    monkeypatch.setenv("KRONOS_MOCK", "1")
    import kronos_mcp.server as s

    s.KB = KnowledgeBase(tmp_path / "kb")
    s.MONITOR = Monitor(tmp_path / "logs")
    s._client = None
    c = s.client()
    prog = bytearray(4960)
    prog[2857] = 2
    c.t.objects[ObjType.PROGRAM] = bytes(prog)
    return s

"""voice_summary: resumen legible (~30 líneas) de un Program o una Combi con los mapas oficiales.

Detalle completo para el motor AL-1 (osciladores, filtro, EGs). Otros motores EXi y HD-1 muestran
voz, efectos y los parámetros no nulos de cada sección principal.
"""
from __future__ import annotations

import re

from . import catalog as C
from . import params as P
from .protocol.constants import ObjType


class _View:
    """Acceso por etiqueta exacta a los parámetros de un objeto."""

    def __init__(self, obj: ObjType, data: bytes):
        self.data = data
        self.items: dict[str, tuple[dict, int]] = {}
        for f, base, label in P.iter_fields(obj, data):
            if f.get("name"):
                self.items.setdefault(label, (f, base))

    def get(self, label: str):
        it = self.items.get(label)
        return None if it is None else P.read(self.data, it[0], it[1])

    def text(self, label: str):
        it = self.items.get(label)
        return None if it is None else P.value_text(it[0], P.read(self.data, it[0], it[1]))


def _kv(pairs: list[tuple[str, object]]) -> str:
    return " ".join(f"{k}={v}" for k, v in pairs if v is not None)


def _voice_mode(v: _View) -> str:
    mono = v.get("Common / Assign Mode")
    legato = v.get("Common / Legato")
    poly_leg = v.get("Common / Poly Legato")
    if mono:
        return "Legato" if legato else "Mono"
    return "Poly Legato" if poly_leg else "Poly"


def _eg(v: _View, pre: str, sec: str) -> str:
    g = lambda n: v.get(f"{pre} / [{sec}] {n}")  # noqa: E731
    adsr = _kv([("A", g("Attack Time")), ("D", g("Decay Time")), ("S", g("Slope Time")), ("R", g("Release Time"))])
    lv = "/".join(str(g(n)) for n in ("Start Level", "Attack Level", "Break Level", "Sustain Level", "Release Level") if g(n) is not None)
    return f"{adsr} · niveles {lv}"


def _al1(v: _View, pre: str) -> list[str]:
    p = lambda sec, n="": f"{pre} / [{sec}] {n}".rstrip()  # noqa: E731
    out = []
    for o in (1, 2):
        pairs = [("wave", v.get(p(f"OSC{o}", "Waveform"))), ("oct", v.get(p(f"OSC{o} Pitch", "Octave"))),
                 ("tune", v.get(p(f"OSC{o} Pitch", "Tune"))), ("level", v.get(p(f"OSC{o}", "Level")))]
        if o == 2:
            pairs += [("sync", v.text(p("OSC2", "Sync"))), ("FM", v.get(p("OSC2", "FM Amount")))]
        out.append(f"  OSC{o}: {_kv(pairs)}")
    for fl in ("Filter A", "Filter B"):
        if v.get(p(fl, "Bypass")) == 1 and fl == "Filter B":
            continue
        pairs = [("type", v.get(p(fl, "Filter Type"))), ("cutoff", v.get(p(fl, "Cutoff"))), ("reso", v.get(p(fl, "Resonance"))),
                 ("keytrack", v.get(p(fl, "Keyboard Track"))), ("vel", v.get(p(fl, "Velocity Intensity")))]
        egint = [x for x in v.items if x.startswith(f"{pre} / [{fl}] ") and "EG" in x and "Intensity" in x.rsplit("] ", 1)[1] and "Mod" not in x]
        pairs += [("EGint", v.get(egint[0]))] if egint else []
        out.append(f"  {fl}: {_kv(pairs)}" + (f" routing={v.get(p('Filter', 'Routing'))}" if fl == "Filter A" else ""))
    out.append(f"  Amp EG: {_eg(v, pre, 'Amplifier EG')}")
    out.append(f"  EG1 (filtro): {_eg(v, pre, 'EG1 (Filter)')}")
    lfo = []
    for label in v.items:
        if label.startswith(pre) and "LFO" in label and not re.search(r"\[LFO\d\]", label) and v.get(label):
            lfo.append(f"{label.split('/ ', 1)[1]}={v.get(label)}")
    if lfo:
        out.append("  LFO activo: " + "; ".join(lfo[:6]))
    return out


def _generic_engine(v: _View, pre: str) -> list[str]:
    secs: dict[str, list[str]] = {}
    for label in v.items:
        if label.startswith(pre + " / "):
            val = v.get(label)
            m = re.match(r"\[(.*?)\] (.*)", label[len(pre) + 3:])
            if m and val not in (0, None) and not m.group(1).startswith(("LFO", "Step", "EG")) and len(secs.setdefault(m.group(1), [])) < 4:
                secs[m.group(1)].append(f"{m.group(2)}={v.get(label)}")
    return [f"  {k}: " + ", ".join(vals) for k, vals in list(secs.items())[:8]]


def _effects(v: _View, obj: ObjType, data: bytes) -> list[str]:
    s = P.summary(obj, data).get("effects", [])
    ifx, mfx, tfx = [], [], []
    for e in s:
        slot = e["slot"]
        n = re.sub(r"\D", "", slot)
        if slot.startswith("Insert"):
            to, on = v.get(f"{slot} / Chain To"), v.get(f"{slot} / Chain Switch")
            bus = v.get(f"{slot} / Bus Select")
            ifx.append(f"IFX{n} {e['effect']}" + (f" (→IFX{to})" if on else "") + (f" bus={bus}" if bus else ""))
        elif slot.startswith("Master"):
            mfx.append(f"MFX{n} {e['effect']} ret={v.get('Master Effect / Return ' + n)}")
        else:
            tfx.append(f"TFX{n} {e['effect']}")
    lines = []
    for title, items in (("Insert", ifx), ("Master", mfx), ("Total", tfx)):
        if items:
            lines.append(f"{title}: " + " | ".join(items))
    return lines or ["Efectos: ninguno"]


def program(data: bytes) -> list[str]:
    obj = ObjType.PROGRAM
    v = _View(obj, data)
    summ = P.summary(obj, data)
    lines = [f"PROGRAM «{summ.get('name', '')}» · {summ['map']} · voz {_voice_mode(v)}"]
    engines = summ.get("exi", [])
    if not engines:
        lines.append("  (HD-1: usa decode_object con query para el detalle)")
    for e in engines:
        slot = e["slot"]  # 'EXi1'
        lines.append(f"{slot}: {e['engine']}")
        pre = next((k.split(" / ")[0] for k in v.items if k.startswith(f"{slot} [")), None)
        if pre is None:
            continue
        lines += _al1(v, pre) if e["engine"] == "AL-1" else _generic_engine(v, pre)
    lines += _effects(v, obj, data)
    return lines


def combi(data: bytes, global_channel: int = 1, program_names: dict[str, str] | None = None) -> list[str]:
    obj = ObjType.COMBINATION
    v = _View(obj, data)
    summ = P.summary(obj, data)
    lines = [f"COMBI «{summ.get('name', '')}»"]
    for t in C.combi_timbres(data, global_channel):
        key = f"{t['program_bank']} {t['program_number']:03d}"
        nm = (program_names or {}).get(key, "")
        bus = v.get(f"Timbre{t['timbre']} / Bus Select")
        lines.append(f"  T{t['timbre']:02d} {t['status']} · {key} {nm} · ch {t['midi_channel']} · vol {t['volume']} · "
                     f"transp {t['transpose']:+d} · zona {t['key_zone']} · bus {bus}")
    lines += _effects(v, obj, data)
    return lines

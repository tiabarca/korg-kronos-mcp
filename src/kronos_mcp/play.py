"""play_notes: toca una lista de eventos por MIDI (notas sueltas, acordes, silencios)."""
from __future__ import annotations

import re
import time

MAX_SECONDS = 60.0
_NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def note_number(n: int | str) -> int:
    """60 | 'C4' | 'F#3' | 'Bb2' → número MIDI (C4 = 60)."""
    if isinstance(n, int):
        num = n
    else:
        m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", n.strip())
        if not m:
            raise ValueError(f"Nota no válida: {n!r} (usa 60 o 'C4', 'F#3', 'Bb2')")
        num = _NOTE[m.group(1).upper()] + (m.group(2) == "#") - (m.group(2) == "b") + (int(m.group(3)) + 1) * 12
    if not 0 <= num <= 127:
        raise ValueError(f"Nota fuera de rango 0-127: {n!r}")
    return num


def schedule(events: list[dict], bpm: float = 120, gate: float = 0.9) -> tuple[list[tuple[float, int, int, int]], float]:
    """Eventos → [(segundos, orden, nota, velocidad)] (velocidad 0 = note off) y duración total.
    Cada evento: n (nota, 'D5' o lista = acorde; omitido/None = silencio), dur (negras, 1), vel (100),
    at (inicio en negras; sin él sigue al evento anterior)."""
    if bpm <= 0:
        raise ValueError("bpm debe ser > 0")
    beat = 60.0 / bpm
    msgs, cursor, end = [], 0.0, 0.0
    for ev in events:
        dur = float(ev.get("dur", 1))
        if dur <= 0:
            raise ValueError(f"dur debe ser > 0: {ev}")
        start = float(ev["at"]) if ev.get("at") is not None else cursor
        if ev.get("at") is None:
            cursor = start + dur
        end = max(end, start + dur)
        n = ev.get("n")
        if n is None:
            continue
        vel = int(ev.get("vel", 100))
        if not 1 <= vel <= 127:
            raise ValueError(f"vel fuera de 1-127: {ev}")
        for num in (note_number(x) for x in (n if isinstance(n, (list, tuple)) else [n])):
            msgs.append((start * beat, 1, num, vel))
            msgs.append(((start + dur * gate) * beat, 0, num, 0))
    msgs.sort(key=lambda m: (m[0], m[1]))  # a igual instante, primero los note off
    return msgs, end * beat


def play(client, events: list[dict], bpm: float = 120, channel: int | None = None, select: str | None = None,
         select_mode: str = "COMBINATION", tail: float = 1.0, sleep=time.sleep, max_seconds: float = MAX_SECONDS) -> dict:
    """Toca `events` por el canal MIDI (1-16; por defecto el global). select: 'INT-B 025' → Bank Select + Program Change
    antes de tocar, pasando antes a COMBINATION o PROGRAM si el Kronos no está en ese modo. Siempre acaba con All Notes Off."""
    from .protocol.constants import Mode, bank_code

    msgs, total = schedule(events, bpm)
    if total + tail > max_seconds:
        raise ValueError(f"Duración {total + tail:.1f} s por encima del límite de {max_seconds:.0f} s")
    ch = (client.channel if channel is None else channel - 1) & 0x0F
    sel = None
    if select:
        parts = select.upper().replace(",", " ").split()
        if len(parts) != 2:
            raise ValueError(f"select: formato 'INT-B 025' (recibido {select!r})")
        sel = (bank_code(parts[0]), int(parts[1]))
    client.hold = True
    t = None
    sounding: set[int] = set()
    sent = 0
    try:
        if sel:
            want = Mode[select_mode.upper()]
            if client.get_mode() != want:
                client.set_mode(want)
                sleep(0.5)
        t = client.t
        if sel:
            t.send(bytes([0xB0 | ch, 0x00, 0x00]))        # Bank MSB
            t.send(bytes([0xB0 | ch, 0x20, sel[0] & 0x7F]))  # Bank LSB (⚠️ USER-* sin confirmar; INT-A..G ✅)
            t.send(bytes([0xC0 | ch, sel[1] & 0x7F]))
            sleep(0.8)
        now = 0.0
        for at, order, num, vel in msgs:
            if at > now:
                sleep(at - now)
                now = at
            t.send(bytes([(0x90 if order else 0x80) | ch, num, vel]))
            (sounding.add if order else sounding.discard)(num)
            sent += order
        if total > now:
            sleep(total - now)
        sleep(tail)  # deja sonar delay y reverb
    finally:
        try:
            t = t or client.t
            for num in sorted(sounding):
                try:
                    t.send(bytes([0x80 | ch, num, 0]))
                except Exception:  # noqa: BLE001 - que un fallo no impida el All Notes Off
                    pass
            t.send(bytes([0xB0 | ch, 123, 0]))  # All Notes Off
        finally:
            client.hold = False
    return {"notes": sent, "channel": ch + 1, "seconds": round(total, 2), "selected": select}

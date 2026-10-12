"""Transporte MIDI real con mido + python-rtmidi (USB o interfaz MIDI)."""
from __future__ import annotations

import queue

import mido
import rtmidi

from .base import Transport


def list_ports() -> dict[str, list[str]]:
    return {"inputs": mido.get_input_names(), "outputs": mido.get_output_names()}


def _guess(names: list[str], hint: str) -> str | None:
    hint = hint.lower()
    for n in names:
        if hint in n.lower():
            return n
    return None


class MidiTransport(Transport):
    def __init__(self, in_name: str | None = None, out_name: str | None = None, hint: str = "kronos"):
        ins, outs = mido.get_input_names(), mido.get_output_names()
        in_name = in_name or _guess(ins, hint)
        out_name = out_name or _guess(outs, hint)
        if not in_name or not out_name:
            raise RuntimeError(f"No encuentro puertos '{hint}'. Entradas={ins} Salidas={outs}")
        super().__init__()
        self.in_name, self.out_name = in_name, out_name
        self._q: queue.Queue[bytes] = queue.Queue()
        self._out = mido.open_output(out_name)
        # rtmidi directo: WinMM entrega los SysEx grandes en trozos (buffer de 1 KB) y
        # mido descarta los trozos al no ser mensajes válidos. Aquí se reensamblan.
        self._frag = bytearray()
        self._in = rtmidi.MidiIn()
        self._in.ignore_types(sysex=False, timing=True, active_sense=True)
        ports = self._in.get_ports()
        idx = next((i for i, p in enumerate(ports) if p == in_name), None)
        if idx is None:
            idx = next((i for i, p in enumerate(ports) if hint.lower() in p.lower()), None)
        if idx is None:
            raise RuntimeError(f"No encuentro la entrada '{in_name}' en rtmidi: {ports}")
        self._in.open_port(idx)
        self._in.set_callback(self._on_data)

    def _on_data(self, event, _data=None) -> None:
        data, _delta = event
        if self._frag or data[0] == 0xF0:
            self._frag.extend(data)
            if self._frag[-1] != 0xF7:
                return  # SysEx incompleto: esperar el siguiente trozo
            raw, self._frag = bytes(self._frag), bytearray()
            self._emit("in", raw)
            self._q.put(raw)
            return
        self._emit("in", bytes(data))

    def send(self, raw: bytes) -> None:
        self._emit("out", raw)
        if raw[0] == 0xF0:
            self._out.send(mido.Message("sysex", data=list(raw[1:-1])))
        else:
            self._out.send(mido.Message.from_bytes(list(raw)))

    def receive(self, timeout: float) -> bytes | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while not self._q.empty():
            self._q.get_nowait()

    def close(self) -> None:
        self._in.cancel_callback()
        self._in.close_port()
        self._in.delete()
        self._out.close()

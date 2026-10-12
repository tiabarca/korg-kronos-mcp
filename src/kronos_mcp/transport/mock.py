"""Kronos simulado para desarrollar y testear sin el teclado conectado."""
from __future__ import annotations

from collections import deque

from ..protocol.codec import decode_7bit, encode_7bit
from ..protocol.constants import OBJ_VERSION, PROGRAM_SIZE_EXI, PROGRAM_SIZE_HD1, SET_LIST_INTERNAL_LEN, Func, Mode, ObjType
from ..protocol.messages import build, parse
from .base import Transport


class MockKronos(Transport):
    def __init__(self, channel: int = 0):
        super().__init__()
        self.channel = channel
        self.mode = Mode.PROGRAM
        self.sent: list[bytes] = []
        self._out: deque[bytes] = deque()
        sl = bytearray(SET_LIST_INTERNAL_LEN)
        sl[:9] = b"Mock Set\x00"
        sl[24 : 24 + 10] = b"Slot Uno\x00\x00"
        self.objects: dict[int, bytes] = {ObjType.SET_LIST: bytes(sl), ObjType.SET_LIST_SLOT_NAME: b"Slot Uno"}
        self.stored: list[tuple[int, int]] = []
        self.bank_objects: dict[tuple[int, int, int], bytes] = {}
        self.param_changes: list[tuple] = []
        for i in range(128):
            size = PROGRAM_SIZE_EXI if i % 2 else PROGRAM_SIZE_HD1
            self.bank_objects[(0x00, 0x40, i)] = f"Mock Prog {i:03d}".encode().ljust(size, b"\x00")

    def _reply(self, code: int = 0) -> None:
        self._out.append(build(self.channel, Func.REPLY, code))

    def _digest(self, t: int, b: int) -> bytes:
        import hashlib

        h = hashlib.sha1()
        for (tt, bb, i), obj in sorted(self.bank_objects.items()):
            if (tt, bb) == (t, b):
                h.update(obj)
        return h.digest()

    def _apply_param(self, d: bytes) -> None:
        """Simula el efecto de un 0x43 sobre el buffer de edición usando el mapa oficial."""
        from .. import params as P

        typ, soc, sub, pid, idx = d[:5]
        val = P.decode_param_change(bytes(d))["value"]
        prog = self.objects.get(ObjType.PROGRAM)
        if prog is None or not P.available():
            return
        buf = bytearray(prog)
        want = {"TYP": str(typ), "SOC": str(soc), "SUB": str(sub), "PID": str(pid), "IDX": str(idx)}
        for f, base, _ in P.iter_fields(ObjType.PROGRAM, prog):
            if f.get("id") == want:
                P.write(buf, f, val, base)
                break
        self.objects[ObjType.PROGRAM] = bytes(buf)

    def inject(self, raw: bytes) -> None:
        """Simula un mensaje que el Kronos envía por iniciativa propia (p. ej. al mover un knob)."""
        self._emit("in", raw)  # llega por sí solo: el monitor lo ve, el cliente no lo espera

    def send(self, raw: bytes) -> None:
        self._emit("out", raw)
        self.sent.append(raw)
        if raw[:6] == b"\xF0\x7E\x7F\x06\x01\xF7":
            self._out.append(bytes([0xF0, 0x7E, self.channel, 0x06, 0x02, 0x42, 0x68, 0x00, 0x0E, 0x00, 0x03, 0x01, 0x03, 0x00, 0xF7]))
            return
        msg = parse(raw)
        if msg is None or msg.channel != self.channel:
            return  # el Kronos real ignora canal incorrecto
        f, d = msg.func, msg.data
        if f == Func.MODE_REQUEST:
            self._out.append(build(self.channel, Func.MODE_DATA, int(self.mode)))
        elif f == Func.MODE_CHANGE:
            try:
                self.mode = Mode(d[0])
                self._reply()
            except ValueError:
                self._reply(0x06)
        elif f == Func.CURR_OBJECT_DUMP_REQ:
            obj = self.objects.get(d[0])
            if obj is None:
                self._reply(0x04)
            else:
                self._out.append(build(self.channel, Func.CURR_OBJECT_DUMP, d[0], 0, encode_7bit(obj)))
        elif f == Func.OBJECT_DUMP_REQ:
            t, b, ih, il = d[:4]
            obj = self.bank_objects.get((t, b, ih << 7 | il))
            if obj is None:
                self._reply(0x04)
            else:
                self._out.append(build(self.channel, Func.OBJECT_DUMP, t, b, ih, il, OBJ_VERSION.get(t, 0), encode_7bit(obj)))
        elif f == Func.OBJECT_DUMP:
            t, b, ih, il = d[:4]
            self.bank_objects[(t, b, ih << 7 | il)] = decode_7bit(d[5:])
            self._reply()
        elif f == Func.CURR_OBJECT_DUMP:
            self._reply()
        elif f == Func.DUMP_BANK_REQ:
            t, b = d[:2]
            for (tt, bb, i), obj in sorted(self.bank_objects.items()):
                if (tt, bb) == (t, b):
                    self._out.append(build(self.channel, Func.OBJECT_DUMP, t, b, i >> 7, i & 0x7F, OBJ_VERSION.get(t, 0), encode_7bit(obj)))
        elif f == Func.BANK_DIGEST_REQ:
            t, b = d[:2]
            self._out.append(build(self.channel, Func.BANK_DIGEST, t, b, encode_7bit(self._digest(t, b))))
        elif f == Func.BANK_DIGEST_COLLECTION_REQ:
            keys = sorted({(t, b) for t, b, _ in self.bank_objects})
            raw = b"".join(bytes([t, b]) + self._digest(t, b) for t, b in keys)
            self._out.append(build(self.channel, Func.BANK_DIGEST_COLLECTION, len(keys) >> 7, len(keys) & 0x7F, encode_7bit(raw)))
        elif f == Func.CURR_PERF_ID_REQ:
            self._out.append(build(self.channel, Func.CURR_PERF_ID, 1, 17, 0, 3))
        elif f == Func.PROG_BANK_TYPES_REQ:
            self._out.append(build(self.channel, Func.PROG_BANK_TYPES, 21, 0b0000011, 0b0000010, 0))
        elif f == Func.CURR_PIANO_TYPES_REQ:
            raw = b"\x00\x00" + b"German D".ljust(25, b"\x00") + b"\x00\x01" + b"Berlin D".ljust(25, b"\x00")
            m = encode_7bit(raw)
            self._out.append(build(self.channel, Func.CURR_PIANO_TYPES, 0, 0, len(m) >> 7, len(m) & 0x7F, m))
        elif f == Func.PARAM_CHANGE_INT:
            self.param_changes.append(tuple(d))
            self._apply_param(d)
        elif f == Func.STORE_BANK_REQ:
            self.stored.append((d[0], d[1]))
            self._reply()
        else:
            self._reply(0x02)

    def receive(self, timeout: float) -> bytes | None:
        if not self._out:
            return None
        raw = self._out.popleft()
        self._emit("in", raw)
        return raw

    def drain(self) -> None:
        pass  # en el mock las respuestas se generan en send(); no hay restos que limpiar

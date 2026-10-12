"""Cliente de alto nivel: petición/respuesta SysEx contra el Kronos."""
from __future__ import annotations

import threading
import time

from .protocol.codec import decode_7bit, encode_7bit
from .protocol.constants import OBJ_VERSION, Func, Mode, ObjType
from .protocol.messages import (
    IDENTITY_REQUEST,
    KronosMessage,
    build,
    check_reply,
    is_identity_reply,
    parse,
    to_hex,
)
from .transport.base import Transport

# 75: [tipo, versión, datos 7-bit...]  ✅ confirmado con el preload del Kronos Editor
CURR_DUMP_DATA_OFFSET = 2
# 73: [tipo, banco, idH, idL, versión, datos 7-bit...]  ✅
OBJ_DUMP_DATA_OFFSET = 5


class KronosTimeout(Exception):
    pass


class KronosClient:
    def __init__(self, transport: Transport, channel: int = 0, timeout: float = 5.0):
        self.t = transport
        self.channel = channel
        self.timeout = timeout
        self.log: list[tuple[str, str]] = []  # (dir, hex) para depuración
        self.lock = threading.RLock()  # una petición/respuesta cada vez

    # --- E/S básica -------------------------------------------------------
    def _send(self, raw: bytes) -> None:
        self.log.append((">>", to_hex(raw)))
        self.t.drain()
        self.t.send(raw)

    def _wait(self, expect: int) -> KronosMessage:
        deadline = time.monotonic() + self.timeout
        while (left := deadline - time.monotonic()) > 0:
            raw = self.t.receive(min(left, 0.1))
            if raw is None:
                continue
            self.log.append(("<<", to_hex(raw)))
            msg = parse(raw)
            if msg is None:
                continue
            if msg.func == Func.REPLY:
                check_reply(msg)  # lanza si hay error
                if expect == Func.REPLY:
                    return msg
            elif msg.func == expect:
                return msg
        raise KronosTimeout(
            f"Sin respuesta 0x{expect:02X}. ¿Canal global correcto ({self.channel + 1})? "
            "¿Global P1 > MIDI Filter > Exclusive activado?"
        )

    def call(self, func: int, *payload, expect: int = Func.REPLY) -> KronosMessage:
        with self.lock:
            self._send(build(self.channel, func, *payload))
            return self._wait(expect)

    def send_raw(self, raw: bytes, expect: int | None = None) -> KronosMessage | None:
        with self.lock:
            self._send(raw)
            return self._wait(expect) if expect is not None else None

    # --- Operaciones ------------------------------------------------------
    def identify(self) -> dict:
        self._send(IDENTITY_REQUEST)
        deadline = time.monotonic() + self.timeout
        while (left := deadline - time.monotonic()) > 0:
            raw = self.t.receive(min(left, 0.1))
            if raw and is_identity_reply(raw):
                self.log.append(("<<", to_hex(raw)))
                # F0 7E 0g 06 02 42 | famLSB famMSB | memLSB memMSB | v1 v2 v3 v4 | F7  (✅ capturado de un Kronos real)
                fam, mem, ver = raw[6] | raw[7] << 7, raw[8] | raw[9] << 7, raw[10:14]
                return {
                    "device_channel": raw[2] + 1,
                    "family": f"0x{fam:02X}",
                    "member": f"0x{mem:02X}",
                    "os_version": ".".join(str(v) for v in ver[:3]) + (f".{ver[3]}" if len(ver) > 3 and ver[3] else ""),
                    "is_kronos": fam == 0x68,
                    "raw": to_hex(raw),
                }
        raise KronosTimeout("Sin respuesta a Identity Request")

    def get_mode(self) -> Mode:
        msg = self.call(Func.MODE_REQUEST, expect=Func.MODE_DATA)
        return Mode(msg.data[0] & 0x0F)

    def set_mode(self, mode: Mode) -> None:
        self.call(Func.MODE_CHANGE, int(mode))

    def read_current_object(self, obj: ObjType) -> bytes:
        """Devuelve el objeto actual (buffer de edición) ya decodificado a 8 bits."""
        msg = self.call(Func.CURR_OBJECT_DUMP_REQ, int(obj), expect=Func.CURR_OBJECT_DUMP)
        return decode_7bit(msg.data[CURR_DUMP_DATA_OFFSET:])

    def read_object(self, obj: ObjType, bank: int, index: int) -> tuple[bytes, bytes]:
        """Object Dump Request (72) → Object Dump (73). Devuelve (datos 8 bits, mensaje SysEx crudo)."""
        msg = self.call(
            Func.OBJECT_DUMP_REQ, int(obj), bank, (index >> 7) & 0x7F, index & 0x7F, expect=Func.OBJECT_DUMP
        )
        t, b, ih, il = msg.data[:4]
        if (t, b, ih << 7 | il) != (int(obj), bank, index):
            raise ValueError(f"Dump inesperado: tipo {t:02X} banco {b:02X} id {ih << 7 | il}")
        raw = build(self.channel, Func.OBJECT_DUMP, msg.data)
        return decode_7bit(msg.data[OBJ_DUMP_DATA_OFFSET:]), raw

    def write_object(self, obj: ObjType, bank: int, index: int, data: bytes) -> None:
        """Object Dump (73) hacia el Kronos: escribe directamente en la memoria del banco (¡destructivo!)."""
        ver = OBJ_VERSION.get(int(obj), 0)
        self.call(Func.OBJECT_DUMP, int(obj), bank, (index >> 7) & 0x7F, index & 0x7F, ver, encode_7bit(data))

    def write_current_object(self, obj: ObjType, data: bytes, version: int | None = None) -> None:
        """Current Object Dump (75 tt ver datos) al buffer de edición."""
        ver = OBJ_VERSION.get(int(obj), 0) if version is None else version
        self.call(Func.CURR_OBJECT_DUMP, int(obj), ver, encode_7bit(data))

    def write_current_string(self, obj: ObjType, text: str, length: int) -> None:
        raw = text.encode("latin-1")[:length].ljust(length, b"\x00")
        self.write_current_object(obj, raw, version=0)

    def store_bank(self, obj: ObjType, bank: int = 0) -> None:
        """Persiste en memoria no volátil (¡escribe en el Kronos!)."""
        self.call(Func.STORE_BANK_REQ, int(obj), bank)

    def goto_set_list(self, n: int) -> None:
        self.set_mode(Mode.SET_LIST)
        ch = self.channel
        self.t.send_channel(0xB0 | ch, 0x00, 0x00)
        self.t.send_channel(0xB0 | ch, 0x20, n & 0x7F)
        self.t.send_channel(0xC0 | ch, 0x00)

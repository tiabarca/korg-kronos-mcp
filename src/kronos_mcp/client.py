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
        """Object Dump (73) hacia el Kronos. Según la doc oficial NO se guarda hasta enviar
        Store Bank Request (76) para ese banco (puedes mandar varios objetos y un solo 76)."""
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

    # --- Funciones de la doc oficial (KRONOS_MIDI_SysEx.txt v1.26) ------------------
    def mode_data(self) -> dict:
        """Func 12 → 42: modo + opciones + protección de memoria."""
        d = self.call(Func.MODE_REQUEST, expect=Func.MODE_DATA).data
        d = d + bytes(5 - len(d)) if len(d) < 5 else d
        mode, opt, s1, s2, s3 = d[:5]
        clocks = {0: "Internal", 1: "External MIDI", 2: "Auto MIDI", 3: "External USB", 4: "Auto USB"}
        prot = ["Program", "Combi", "Song", "Drum Kit", "Wave Seq", "KARMA GE", "Internal HDD Save"]
        try:
            mname = Mode(mode & 0x0F).name
        except ValueError:
            mname = str(mode & 0x0F)
        return {
            "mode": mname,
            "note_receive": {0: "All", 1: "Even", 2: "Odd"}.get(s1 & 0x03, s1 & 0x03),
            "midi_clock": clocks.get((s1 >> 2) & 0x07, (s1 >> 2) & 0x07),
            "memory_protect": {name: bool(s2 >> i & 1) for i, name in enumerate(prot)} | {"Set List": bool(s3 & 1)},
            "raw": to_hex(d[:5]),
        }

    def collect(self, func: int, until: callable, idle: float = 1.5, total: float = 120.0) -> list[KronosMessage]:
        """Recoge varios mensajes de una función (p. ej. 73 tras un Dump Bank Request)."""
        out: list[KronosMessage] = []
        start = last = time.monotonic()
        while time.monotonic() - start < total and time.monotonic() - last < (idle if out else self.timeout):
            raw = self.t.receive(0.1)
            if raw is None:
                continue
            self.log.append(("<<", to_hex(raw[:64])))
            msg = parse(raw)
            if msg is None:
                continue
            if msg.func == Func.REPLY:
                check_reply(msg)
                continue
            if msg.func == func:
                out.append(msg)
                last = time.monotonic()
                if until(out):
                    break
        return out

    def dump_bank(self, obj: ObjType, bank: int, expected: int) -> list[tuple[int, bytes, bytes]]:
        """Func 77: el Kronos envía todo el banco como 73. Devuelve [(índice, datos, sysex)]."""
        with self.lock:
            self._send(build(self.channel, Func.DUMP_BANK_REQ, int(obj), bank))
            msgs = self.collect(Func.OBJECT_DUMP, lambda m: len(m) >= expected)
        res = []
        for m in msgs:
            t, b, ih, il = m.data[:4]
            if t == int(obj) and b == bank:
                res.append((ih << 7 | il, decode_7bit(m.data[OBJ_DUMP_DATA_OFFSET:]), build(self.channel, Func.OBJECT_DUMP, m.data)))
        return sorted(res)

    def bank_digest(self, obj: ObjType, bank: int) -> str:
        """Func 37 → 38: SHA-1 del banco completo (detecta cambios sin descargarlo)."""
        m = self.call(Func.BANK_DIGEST_REQ, int(obj), bank, expect=Func.BANK_DIGEST)
        return decode_7bit(m.data[2:])[:20].hex()

    def digest_collection(self) -> list[dict]:
        """Func 39 → 3A: digests de todos los bancos en un solo mensaje."""
        m = self.call(Func.BANK_DIGEST_COLLECTION_REQ, expect=Func.BANK_DIGEST_COLLECTION)
        n = m.data[0] << 7 | m.data[1]
        raw = decode_7bit(m.data[2:])
        return [{"obj": raw[i * 22], "bank": raw[i * 22 + 1], "sha1": raw[i * 22 + 2 : i * 22 + 22].hex()} for i in range(n) if len(raw) >= i * 22 + 22]

    def current_performance(self, ptype: int | None = None) -> dict:
        """Func 32 → 33: Program/Combi/Song activo."""
        m = self.call(Func.CURR_PERF_ID_REQ, *(() if ptype is None else (ptype,)), expect=Func.CURR_PERF_ID)
        t, b, nh, nl = m.data[:4]
        return {"type": {0: "combi", 1: "program", 2: "song"}.get(t, t), "bank_code": b, "number": nh << 7 | nl}

    def program_bank_types(self) -> dict:
        """Func 60 → 61: qué bancos de Program son EXi (1) o HD-1 (0)."""
        m = self.call(Func.PROG_BANK_TYPES_REQ, expect=Func.PROG_BANK_TYPES)
        nbits, data = m.data[0], m.data[1:]
        names = ["edit buffer"] + [f"INT-{c}" for c in "ABCDEF"] + [f"USER-{c}" for c in "ABCDEFG"] + [f"USER-{c * 2}" for c in "ABCDEFG"]
        out = {}
        for i in range(nbits):
            byte, bit = divmod(i, 7)
            if byte < len(data):
                out[names[i] if i < len(names) else f"bank{i}"] = "EXi" if data[byte] >> bit & 1 else "HD-1"
        return out

    def piano_types(self) -> list[dict]:
        """Func 34 → 35: pianos (SGX) instalados."""
        m = self.call(Func.CURR_PIANO_TYPES_REQ, expect=Func.CURR_PIANO_TYPES)
        raw = decode_7bit(m.data[4:])
        return [{"index": raw[i] << 8 | raw[i + 1], "name": raw[i + 2 : i + 27].split(b"\x00")[0].decode("latin-1")} for i in range(0, len(raw) - 26, 27)]

    def sample_banks(self) -> list[dict]:
        """Func 30 kind 0 → 31: bancos de multisamples (ROM, EXs, usuario)."""
        m = self.call(Func.CURR_SAMPLE_INFO_REQ, 0, bytes(19), expect=Func.CURR_SAMPLE_INFO)
        d = m.data[1 + 19 + 4 :]
        raw = decode_7bit(d)
        types = {0: "ROM", 1: "Sampling", 2: "EXs", 3: "User", 4: "Unknown", 5: "Legacy RAM"}
        out = []
        for i in range(0, len(raw) - 176, 177):
            e = raw[i : i + 177]
            out.append({"uuid": e[:16].hex(), "type": types.get(e[16], e[16]),
                        "short": e[17:97].split(b"\x00")[0].decode("latin-1"), "long": e[97:177].split(b"\x00")[0].decode("latin-1")})
        return out

    def set_parameter(self, typ: int, soc: int, sub: int, pid: int, idx: int, value: int, func: int = Func.PARAM_CHANGE_INT) -> None:
        """Func 43 (o 41/6D/6E): cambia un parámetro del buffer de edición. Solo responde si hay error."""
        from .params import encode_value

        with self.lock:
            self._send(build(self.channel, func, typ, soc, sub, pid, idx, *encode_value(value)))
            end = time.monotonic() + 0.4
            while time.monotonic() < end:
                raw = self.t.receive(0.05)
                if raw and (msg := parse(raw)) and msg.func == Func.REPLY:
                    self.log.append(("<<", to_hex(raw)))
                    check_reply(msg)

    def set_current_object(self, kind: int, linear_index: int) -> None:
        """Func 71: selecciona drum kit (kind 0) o wave seq (kind 1) a editar, índice lineal."""
        with self.lock:
            self._send(build(self.channel, Func.SET_CURRENT_OBJECT, kind, (linear_index >> 7) & 0x7F, linear_index & 0x7F))

    def goto_set_list(self, n: int) -> None:
        self.set_mode(Mode.SET_LIST)
        ch = self.channel
        self.t.send_channel(0xB0 | ch, 0x00, 0x00)
        self.t.send_channel(0xB0 | ch, 0x20, n & 0x7F)
        self.t.send_channel(0xC0 | ch, 0x00)

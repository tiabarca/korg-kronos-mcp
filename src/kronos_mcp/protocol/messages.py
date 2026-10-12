"""Construcción y parseo de mensajes SysEx del Kronos."""
from __future__ import annotations

from dataclasses import dataclass

from .constants import (
    CHANNEL_BASE,
    KORG_ID,
    KRONOS_MODEL_ID,
    REPLY_CODES,
    SYSEX_END,
    SYSEX_START,
    Func,
)


def header(channel: int) -> bytes:
    if not 0 <= channel <= 15:
        raise ValueError("canal global debe ser 0..15 (canal MIDI 1..16)")
    return bytes([SYSEX_START, KORG_ID, CHANNEL_BASE + channel, KRONOS_MODEL_ID])


def build(channel: int, func: int, *payload: int | bytes) -> bytes:
    body = bytearray()
    for p in payload:
        body.extend(p if isinstance(p, (bytes, bytearray)) else [p])
    if any(b > 0x7F for b in body):
        raise ValueError("payload SysEx con bytes > 0x7F; ¿falta encode_7bit?")
    return header(channel) + bytes([func]) + bytes(body) + bytes([SYSEX_END])


@dataclass
class KronosMessage:
    channel: int
    func: int
    data: bytes  # bytes entre el código de función y F7

    @property
    def func_name(self) -> str:
        try:
            return Func(self.func).name
        except ValueError:
            return f"0x{self.func:02X}"


def parse(raw: bytes) -> KronosMessage | None:
    """Devuelve el mensaje si es SysEx Korg Kronos, si no None."""
    if len(raw) < 6 or raw[0] != SYSEX_START or raw[-1] != SYSEX_END:
        return None
    if raw[1] != KORG_ID or raw[3] != KRONOS_MODEL_ID or (raw[2] & 0xF0) != CHANNEL_BASE:
        return None
    return KronosMessage(channel=raw[2] & 0x0F, func=raw[4], data=bytes(raw[5:-1]))


class KronosReplyError(Exception):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"Kronos error 0x{code:02X}: {REPLY_CODES.get(code, 'desconocido')}")


def check_reply(msg: KronosMessage) -> None:
    if msg.func == Func.REPLY and msg.data and msg.data[0] != 0:
        raise KronosReplyError(msg.data[0])


def to_hex(raw: bytes) -> str:
    return " ".join(f"{b:02X}" for b in raw)


def from_hex(text: str) -> bytes:
    return bytes.fromhex(text.replace(",", " "))


# --- Universal SysEx -------------------------------------------------------
IDENTITY_REQUEST = bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])


def is_identity_reply(raw: bytes) -> bool:
    return len(raw) > 6 and raw[:2] == b"\xF0\x7E" and raw[3:5] == b"\x06\x02" and raw[5] == KORG_ID

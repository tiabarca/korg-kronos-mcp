"""Codificación 7→8 bits usada por Korg en los dumps de objeto.

Cada grupo de 7 bytes internos se envía como 8 bytes MIDI: el primero lleva
los MSB (bit i = MSB del byte i) y le siguen los 7 bytes con el MSB a cero.
"""


def encode_7bit(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 7):
        chunk = data[i : i + 7]
        out.append(sum(1 << j for j, b in enumerate(chunk) if b & 0x80))
        out.extend(b & 0x7F for b in chunk)
    return bytes(out)


def decode_7bit(midi: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(midi), 8):
        chunk = midi[i : i + 8]
        hi = chunk[0]
        for j, b in enumerate(chunk[1:]):
            out.append(b | (0x80 if hi & (1 << j) else 0))
    return bytes(out)


def midi_len(internal_len: int) -> int:
    return internal_len + (internal_len + 6) // 7


def internal_len(midi_len_: int) -> int:
    rem = midi_len_ % 8
    return (midi_len_ // 8) * 7 + (rem - 1 if rem else 0)

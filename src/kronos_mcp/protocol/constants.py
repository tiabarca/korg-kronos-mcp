"""Constantes del protocolo SysEx del Korg Kronos.

Fuente: docs/sysex/kronos_sysex_referencia.md (basado en kronut + doc oficial Korg).
"""
from enum import IntEnum

SYSEX_START = 0xF0
SYSEX_END = 0xF7
KORG_ID = 0x42
KRONOS_MODEL_ID = 0x68
CHANNEL_BASE = 0x30  # 0x30 + canal global (0..15)


class Func(IntEnum):
    MODE_REQUEST = 0x12
    SONG_SELECT = 0x13
    REPLY = 0x24
    CURR_SAMPLE_INFO_REQ = 0x30
    CURR_SAMPLE_INFO = 0x31
    CURR_PERF_ID_REQ = 0x32
    CURR_PERF_ID = 0x33
    CURR_PIANO_TYPES_REQ = 0x34
    CURR_PIANO_TYPES = 0x35
    NOTIFY_PIANO_TYPES_CHANGED = 0x36
    BANK_DIGEST_REQ = 0x37
    BANK_DIGEST = 0x38
    BANK_DIGEST_COLLECTION_REQ = 0x39
    BANK_DIGEST_COLLECTION = 0x3A
    SEQ_PARAM_CHANGE = 0x41
    MODE_DATA = 0x42
    PARAM_CHANGE_INT = 0x43
    PARAM_CHANGE_BINARY = 0x44
    MODE_CHANGE = 0x4E
    DRUM_KIT_PARAM_INT = 0x53
    DRUM_KIT_PARAM_BINARY = 0x54
    WAVE_SEQ_PARAM_INT = 0x55
    WAVE_SEQ_PARAM_BINARY = 0x56
    PROG_BANK_TYPES_REQ = 0x60
    PROG_BANK_TYPES = 0x61
    KARMA_PARAM_CHANGE = 0x6D
    DRUM_TRACK_PARAM_CHANGE = 0x6E
    SET_CURRENT_OBJECT = 0x71
    OBJECT_DUMP_REQ = 0x72
    OBJECT_DUMP = 0x73
    CURR_OBJECT_DUMP_REQ = 0x74
    CURR_OBJECT_DUMP = 0x75
    STORE_BANK_REQ = 0x76
    DUMP_BANK_REQ = 0x77
    RESET_CONTROLLER = 0x78
    SMF_DATA_DUMP_REQ = 0x79
    SMF_DATA_DUMP = 0x7A
    CHANGE_PROG_BANK_TYPE = 0x7C
    KARMA_CONTROL = 0x7F


class ObjType(IntEnum):
    PROGRAM = 0x00
    COMBINATION = 0x01
    SONG_TIMBRE_SET = 0x02
    GLOBAL = 0x03
    DRUM_KIT = 0x04
    WAVE_SEQ = 0x05
    KARMA_GE = 0x06
    KARMA_TEMPLATE = 0x07
    SONG_CONTROL = 0x08
    SONG_EVENT = 0x09
    SONG_REGION = 0x0A
    KARMA_GE_RTP_INFO = 0x0C
    SET_LIST = 0x0D
    DRUM_TRACK_PATTERN = 0x0E
    DRUM_TRACK_PATTERN_EVENT = 0x0F
    SET_LIST_SLOT_COMMENTS = 0x10
    SET_LIST_SLOT_NAME = 0x11
    COMBI_NAME = 0x12
    PROGRAM_NAME = 0x13
    SONG_NAME = 0x14
    WAVE_SEQ_NAME = 0x15
    DRUM_KIT_NAME = 0x16
    SET_LIST_NAME = 0x17
    SONG = 0x18


class Mode(IntEnum):
    COMBINATION = 0
    PROGRAM = 2
    SEQUENCER = 4
    SAMPLING = 6
    GLOBAL = 7
    DISK = 8
    SET_LIST = 9


# Códigos de Reply (func 0x24). 0x40-0x42 según doc; kronut usa 0x64-0x66,
# los aceptamos ambos hasta confirmarlo con una respuesta real.
REPLY_CODES: dict[int, str] = {
    0x00: "sin error",
    0x01: "tipo de parámetro incorrecto para el modo actual",
    0x02: "tipo de mensaje, ID de parámetro o índice desconocido",
    0x03: "mensaje corto o malformado",
    0x04: "objeto destino no encontrado",
    0x05: "recursos insuficientes",
    0x06: "valor fuera de rango",
    0x07: "error interno",
    0x40: "banco de programas de tipo incorrecto / datos de patrón inválidos",
    0x41: "objeto destino protegido",
    0x42: "desbordamiento de memoria",
}
REPLY_CODES.update({0x64: REPLY_CODES[0x40], 0x65: REPLY_CODES[0x41], 0x66: REPLY_CODES[0x42]})

SET_LIST_INTERNAL_LEN = 69416
SLOT_INTERNAL_LEN = 542
SLOT_NAME_LEN = 24
SLOT_COMMENTS_LEN = 512


# --- Descubierto por ingeniería inversa del Kronos Editor 3.1 (preload .SQS) ---
# Versión de formato que acompaña a cada dump (73: tt bank idH idL VER / 75: tt VER).
OBJ_VERSION: dict[int, int] = {
    0x00: 5,  # Program
    0x01: 3,  # Combination
    0x02: 3,  # Song Timbre Set
    0x03: 2,  # Global
    0x04: 3,  # Drum Kit
    0x05: 1,  # Wave Sequence
    0x06: 0,  # KARMA GE
    0x08: 0,  # Song Control
    0x0D: 0,  # Set List
    0x0E: 0,  # Drum Track Pattern
}

# Tamaño del objeto decodificado (bytes internos).
OBJ_SIZE: dict[int, tuple[int, ...]] = {
    0x00: (4960, 3706),  # Program EXi / HD-1
    0x01: (7810,),
    0x02: (7810,),
    0x03: (24708,),
    0x04: (38424,),
    0x05: (2216,),
    0x06: (2544,),
    0x08: (5264,),
    0x0D: (69416,),
}
PROGRAM_SIZE_EXI = 4960
PROGRAM_SIZE_HD1 = 3706
NAME_LEN = 24  # nombre en los primeros 24 bytes (Program, Combi, Drum Kit, Wave Seq, KARMA GE, Set List)

# Nº de objetos por banco
BANK_SLOTS = {
    0x00: 128,  # Program
    0x01: 128,  # Combination
    0x04: None,  # Drum Kit: INT=40, USER=16
    0x05: None,  # Wave Seq: INT=150, USER=32
    0x06: 128,  # KARMA GE
    0x0D: 128,  # Set List (bank 0, index = set list)
}
SONGS = 200
DRUM_TRACK_PATTERNS = 1000


def bank_code(name: str) -> int:
    """'INT-A'..'INT-G' → 0x00..0x06 · 'USER-A'..'USER-G' → 0x40..0x46 · 'USER-AA'..'USER-GG' → 0x47..0x4D."""
    n = name.upper().replace("_", "-").replace(" ", "")
    if n in ("INT", "I"):
        return 0x00
    pre, _, suf = n.partition("-")
    if pre in ("INT", "I") and len(suf) == 1:
        return "ABCDEFG".index(suf)
    if pre in ("USER", "U"):
        if len(suf) == 1:
            return 0x40 + "ABCDEFG".index(suf)
        if len(suf) == 2 and suf[0] == suf[1]:
            return 0x47 + "ABCDEFG".index(suf[0])
    raise ValueError(f"Banco desconocido: {name}")


def bank_label(code: int) -> str:
    if code < 0x40:
        return f"INT-{'ABCDEFGHIJKL'[code]}" if code < 12 else f"INT-{code:02X}"
    if code <= 0x46:
        return f"USER-{'ABCDEFG'[code - 0x40]}"
    if code <= 0x4D:
        return f"USER-{'ABCDEFG'[code - 0x47] * 2}"
    return f"0x{code:02X}"


def bank_size(obj: int, bank: int) -> int:
    if obj == 0x04:
        return 40 if bank < 0x40 else 16
    if obj == 0x05:
        return 150 if bank < 0x40 else 32
    if obj in (0x02, 0x08):
        return SONGS
    if obj == 0x0E:
        return DRUM_TRACK_PATTERNS
    return 128

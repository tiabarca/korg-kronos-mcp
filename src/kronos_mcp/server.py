"""MCP server (stdio) para mantenimiento del Korg Kronos vía SysEx.

Variables de entorno:
  KRONOS_MIDI_IN / KRONOS_MIDI_OUT  nombre exacto de los puertos (opcional; autodetecta 'kronos')
  KRONOS_CHANNEL                    canal MIDI global del Kronos, 1..16 (por defecto 1)
  KRONOS_ALLOW_WRITE                '1' para permitir operaciones que modifican el Kronos
  KRONOS_MOCK                       '1' para usar un Kronos simulado
  KRONOS_BACKUP_DIR                 carpeta de backups (por defecto ./backups)
  KRONOS_KNOWLEDGE_DIR              base de conocimiento (por defecto ./data/knowledge)
  KRONOS_LOG_DIR                    logs del monitor MIDI (por defecto ./logs)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import hashlib
import uuid
from functools import lru_cache

from .client import KronosClient
from .knowledge import KnowledgeBase
from .monitor import Monitor

from .protocol.constants import (
    NAME_LEN,
    PROGRAM_SIZE_EXI,
    SLOT_COMMENTS_LEN,
    SLOT_NAME_LEN,
    Func,
    Mode,
    ObjType,
    bank_code,
    bank_label,
    bank_size,
)
from .protocol.messages import from_hex, to_hex
from .protocol.setlist import parse_set_list
from . import params as P

INSTRUCTIONS = """Servidor para mantener un Korg Kronos por MIDI SysEx y descubrir su mapa de parámetros en caliente.

Mapas oficiales de Korg cargados (data/params): decode_object, find_parameter y set_parameter
usan los nombres reales de cada parámetro; los diffs indican qué parámetro ha cambiado.

Reglas:
- Antes de escribir en el teclado (tools con confirm), haz backup (backup_bank / read_object save_backup) y pide confirmación al usuario.
- Las peticiones de solo lectura se pueden explorar con `probe` sin permisos de escritura.
- Todo lo que descubras regístralo: parámetros con `learn_param`, observaciones de protocolo con `note`.
  Consulta antes `knowledge` para no repetir trabajo.

Flujo para descubrir un parámetro:
1. learn_start(obj_type, descripción) → foto del buffer de edición + marca en el monitor.
2. Pide al usuario que cambie UN solo parámetro en el teclado (anotando valor antes/después).
3. learn_finish(sesión) → diff de bytes + mensajes que el Kronos haya enviado (p. ej. Parameter Change 0x43).
4. Si el diff es claro, learn_param(...) con offset/tamaño/bits y valores; si es ambiguo, repite con otro valor.
"""

mcp = FastMCP("korg-kronos", instructions=INSTRUCTIONS)
_client: KronosClient | None = None
PROJECT_DIR = Path(__file__).resolve().parents[2]
MONITOR = Monitor(Path(os.getenv("KRONOS_LOG_DIR", PROJECT_DIR / "logs")))
KB = KnowledgeBase(Path(os.getenv("KRONOS_KNOWLEDGE_DIR", PROJECT_DIR / "data" / "knowledge")))
_learn_sessions: dict[str, dict] = {}
# Peticiones que no modifican nada en el Kronos → exploración libre
SAFE_REQUESTS = {0x12, 0x30, 0x32, 0x34, 0x37, 0x39, 0x60, 0x72, 0x74, 0x77, 0x79}

ALLOW_WRITE = os.getenv("KRONOS_ALLOW_WRITE") == "1"
# rutas absolutas: el cliente MCP puede arrancar el server desde cualquier carpeta
BACKUP_DIR = Path(os.getenv("KRONOS_BACKUP_DIR", PROJECT_DIR / "backups"))
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
NAMED_TYPES = {ObjType.PROGRAM, ObjType.COMBINATION, ObjType.DRUM_KIT, ObjType.WAVE_SEQ, ObjType.KARMA_GE, ObjType.SET_LIST, ObjType.SONG_TIMBRE_SET}


def _name(data: bytes) -> str:
    return data[:NAME_LEN].split(b"\x00", 1)[0].decode("latin-1").rstrip()


def _label(obj: int, b: int) -> str:
    return "INT" if b == 0 and int(obj) in (0x02, 0x04, 0x05, 0x08, 0x0D, 0x0E) else bank_label(b)


def _bank(bank: str | int) -> int:
    return bank if isinstance(bank, int) else bank_code(bank)


@lru_cache(maxsize=1)
def _factory_digests() -> dict:
    p = DATA_DIR / "factory_digests.json"
    return json.loads(p.read_text(encoding="utf-8"))["digests"] if p.exists() else {}


def _summary(obj: ObjType, data: bytes) -> dict:
    try:
        return P.summary(obj, data) if P.available() and (obj in P.MAP_FOR or obj == ObjType.PROGRAM) else {}
    except Exception as e:  # noqa: BLE001
        return {"summary_error": str(e)}


@lru_cache(maxsize=1)
def _factory() -> dict:
    p = DATA_DIR / "preload_index.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))["preloads"]


def client() -> KronosClient:
    global _client
    if _client is None:
        channel = int(os.getenv("KRONOS_CHANNEL", "1")) - 1
        if os.getenv("KRONOS_MOCK") == "1":
            from .transport.mock import MockKronos

            transport = MockKronos(channel)
        else:
            from .transport.midi import MidiTransport

            transport = MidiTransport(os.getenv("KRONOS_MIDI_IN"), os.getenv("KRONOS_MIDI_OUT"))
        transport.listeners.append(MONITOR.record)
        _client = KronosClient(transport, channel)
    return _client


def _require_write(confirm: bool) -> None:
    if not ALLOW_WRITE:
        raise PermissionError("Escritura deshabilitada. Arranca el server con KRONOS_ALLOW_WRITE=1.")
    if not confirm:
        raise PermissionError("Operación que modifica el Kronos: repite la llamada con confirm=true.")


# --- Diagnóstico -------------------------------------------------------------
@mcp.tool()
def list_midi_ports() -> dict:
    """Lista los puertos MIDI de entrada y salida disponibles en el equipo."""
    from .transport.midi import list_ports

    return list_ports()


@mcp.tool()
def identify() -> dict:
    """Envía un Universal Identity Request y devuelve la identificación del Kronos."""
    return client().identify()


@mcp.tool()
def get_mode() -> str:
    """Devuelve el modo actual del Kronos (PROGRAM, COMBINATION, SET_LIST, ...)."""
    return client().get_mode().name


@mcp.tool()
def set_mode(mode: str) -> str:
    """Cambia el modo del Kronos. Valores: COMBINATION, PROGRAM, SEQUENCER, SAMPLING, GLOBAL, DISK, SET_LIST."""
    client().set_mode(Mode[mode.upper()])
    return f"Modo cambiado a {mode.upper()}"


@mcp.tool()
def sysex_log(last: int = 20) -> list[dict]:
    """Últimos mensajes intercambiados, decodificados (atajo de `monitor`)."""
    client()
    return MONITOR.since(max(0, MONITOR.seq - last), limit=last)


# --- Lectura -----------------------------------------------------------------
@mcp.tool()
def read_current_object(obj_type: str, save_backup: bool = True) -> dict:
    """Lee el objeto actual del buffer de edición (PROGRAM, COMBINATION, GLOBAL, SET_LIST, ...).
    Devuelve tamaño y primeros bytes; opcionalmente guarda el binario en la carpeta de backups."""
    obj = ObjType[obj_type.upper()]
    data = client().read_current_object(obj)
    result = {"type": obj.name, "length": len(data), "head_hex": to_hex(data[:64])}
    if save_backup:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{obj.name}.bin"
        path.write_bytes(data)
        result["backup"] = str(path.resolve())
    return result


@mcp.tool()
def get_set_list(number: int | None = None, include_empty_slots: bool = False) -> dict:
    """Lee un Set List (0..127) o el actual si no se indica, y lo devuelve estructurado."""
    c = client()
    if number is not None:
        c.goto_set_list(number)
    else:
        c.set_mode(Mode.SET_LIST)
    sl = parse_set_list(c.read_current_object(ObjType.SET_LIST))
    if sl.get("initialized") and not include_empty_slots:
        sl["slots"] = [s for s in sl["slots"] if s["name"] or s["comments"]]
    return sl


@mcp.tool()
def read_object(obj_type: str, bank: str, index: int, save_backup: bool = False) -> dict:
    """Lee un objeto almacenado (no el buffer de edición) con Object Dump Request.
    bank: 'INT-A'..'INT-G', 'USER-A'..'USER-G', 'USER-AA'..'USER-GG' (o 'INT' para Drum Kit/Wave Seq/Set List)."""
    obj, b = ObjType[obj_type.upper()], _bank(bank)
    data, raw = client().read_object(obj, b, index)
    res = {"type": obj.name, "bank": _label(obj, b), "index": index, "size": len(data), "md5": hashlib.md5(data).hexdigest()}
    if obj in NAMED_TYPES:
        res["name"] = _name(data)
    if obj == ObjType.PROGRAM:
        res["engine"] = "EXi" if len(data) == PROGRAM_SIZE_EXI else "HD-1"
    res.update({k: v for k, v in _summary(obj, data).items() if k not in ("name",)})
    if save_backup:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{obj.name}_{_label(obj, b)}_{index:03d}.syx"
        path.write_bytes(raw)
        res["backup"] = str(path.resolve())
    return res


@mcp.tool()
def list_bank(obj_type: str, bank: str, start: int = 0, count: int | None = None) -> list[dict]:
    """Lista nombres (y motor, en Programs) de un banco completo leyendo cada objeto. Tarda unos segundos."""
    obj, b = ObjType[obj_type.upper()], _bank(bank)
    end = min(bank_size(obj, b), start + count) if count else bank_size(obj, b)
    out = []
    for i, data in _iter_bank(obj, b, start, end):
        row = {"index": i, "name": _name(data) if obj in NAMED_TYPES else None}
        if obj == ObjType.PROGRAM:
            row["engine"] = "EXi" if len(data) == PROGRAM_SIZE_EXI else "HD-1"
        out.append(row)
    return out


def _bank_raw(obj: ObjType, b: int, start: int = 0, end: int | None = None) -> list[tuple[int, bytes, bytes]]:
    """Banco completo: primero Dump Bank Request (77, un mensaje); si no llega entero, objeto a objeto (72)."""
    n = bank_size(obj, b)
    end = n if end is None else end
    if start == 0 and end == n:
        try:
            got = client().dump_bank(obj, b, n)
            if len(got) == n:
                return got
        except Exception:  # noqa: BLE001 - se cae al método lento
            pass
    out = []
    for i in range(start, end):
        data, raw = client().read_object(obj, b, i)
        out.append((i, data, raw))
    return out


def _iter_bank(obj: ObjType, b: int, start: int, end: int):
    for i, data, _ in _bank_raw(obj, b, start, end):
        yield i, data


@mcp.tool()
def backup_bank(obj_type: str, bank: str) -> dict:
    """Guarda un banco completo como fichero .syx (Object Dumps 0x73 + Store Bank 0x76, restaurable)."""
    obj, b = ObjType[obj_type.upper()], _bank(bank)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{obj.name}_{_label(obj, b)}.syx"
    items = _bank_raw(obj, b)
    with path.open("wb") as fh:
        for _, _, raw in items:
            fh.write(raw)
    return {"file": str(path.resolve()), "objects": len(items), "bytes": path.stat().st_size}


@mcp.tool()
def compare_with_factory(obj_type: str, bank: str, model: str = "KRONOS") -> dict:
    """Compara un banco del teclado con el preload de fábrica (KRONOS o NewKRONOS) por md5.
    Devuelve qué posiciones siguen de fábrica y cuáles se han modificado."""
    obj, b = ObjType[obj_type.upper()], _bank(bank)
    ref = {(e["index"]): e for e in _factory().get(model, []) if e["type_code"] == int(obj) and e["bank"] == b}
    if not ref:
        return {"error": "Sin referencia de fábrica para ese tipo/banco (¿falta data/preload_index.json?)"}
    fd = _factory_digests().get(model, {}).get(f"{int(obj):02X}:{b:02X}")
    if fd:
        try:
            if client().bank_digest(obj, b) == fd["sha1"]:
                return {"bank": _label(obj, b), "factory_identical": len(ref), "modified": [], "method": "digest SHA-1 (0x37)"}
        except Exception:  # noqa: BLE001
            pass
    same, changed = [], []
    current = dict(_iter_bank(obj, b, 0, bank_size(obj, b)))
    for i in sorted(ref):
        data = current.get(i, b"")
        item = {"index": i, "current": _name(data) if obj in NAMED_TYPES else None, "factory": ref[i]["name"]}
        (same if hashlib.md5(data).hexdigest() == ref[i]["md5"] else changed).append(item)
    return {"bank": _label(obj, b), "factory_identical": len(same), "modified": changed}


@mcp.tool()
def search_factory(text: str, obj_type: str | None = None, model: str = "KRONOS", limit: int = 30) -> list[dict]:
    """Busca por nombre en el preload de fábrica (sin teclado conectado)."""
    t = text.lower()
    code = int(ObjType[obj_type.upper()]) if obj_type else None
    res = []
    for e in _factory().get(model, []):
        if t in e["name"].lower() and (code is None or e["type_code"] == code):
            res.append({"type": e["type"], "bank": _label(e["type_code"], e["bank"]), "index": e["index"], "name": e["name"], "engine": e.get("engine")})
            if len(res) >= limit:
                break
    return res


# --- Escritura (protegida) -----------------------------------------------------
@mcp.tool()
def rename_current_slot(name: str, confirm: bool = False) -> str:
    """Renombra el slot actual del Set List en el buffer de edición (no guarda: usa store_set_list)."""
    _require_write(confirm)
    client().write_current_string(ObjType.SET_LIST_SLOT_NAME, name, SLOT_NAME_LEN)
    return f"Slot renombrado a '{name}' (pendiente de store_set_list)"


@mcp.tool()
def set_current_slot_comments(comments: str, confirm: bool = False) -> str:
    """Escribe los comentarios del slot actual en el buffer de edición (no guarda)."""
    _require_write(confirm)
    client().write_current_string(ObjType.SET_LIST_SLOT_COMMENTS, comments, SLOT_COMMENTS_LEN)
    return "Comentarios actualizados (pendiente de store_set_list)"


@mcp.tool()
def store_set_list(confirm: bool = False) -> str:
    """Guarda el Set List actual en la memoria no volátil del Kronos."""
    _require_write(confirm)
    client().store_bank(ObjType.SET_LIST, 0)
    return "Set List guardado"


@mcp.tool()
def restore_syx(file: str, confirm: bool = False) -> dict:
    """Envía al Kronos los Object Dump (0x73) de un .syx creado con backup_bank/read_object.
    Sobrescribe esas posiciones en memoria. Haz antes un backup del estado actual."""
    _require_write(confirm)
    raw = Path(file).read_bytes()
    sent = 0
    banks: set[tuple[int, int]] = set()
    i = 0
    while (start := raw.find(b"\xF0", i)) != -1:
        end = raw.index(b"\xF7", start)
        msg = raw[start : end + 1]
        if len(msg) > 10 and msg[4] == Func.OBJECT_DUMP:
            client().send_raw(msg, Func.REPLY)
            banks.add((msg[5], msg[6]))
            sent += 1
        i = end + 1
    # la doc oficial: los 73 recibidos no se guardan hasta un Store Bank Request por banco
    for t, b in sorted(banks):
        client().store_bank(ObjType(t), b)
    return {"objects_written": sent, "banks_stored": [f"{ObjType(t).name} {_label(t, b)}" for t, b in sorted(banks)]}


@mcp.tool()
def send_raw_sysex(hex_bytes: str, expect_func: str | None = None, confirm: bool = False) -> dict:
    """Envía un SysEx arbitrario (hex, p.ej. 'F0 42 30 68 12 F7'). Para experimentar con funciones
    aún no implementadas. expect_func: código de función de respuesta en hex ('42', '75', '24')."""
    _require_write(confirm)
    raw = from_hex(hex_bytes)
    msg = client().send_raw(raw, int(expect_func, 16) if expect_func else None)
    if msg is None:
        return {"sent": to_hex(raw)}
    return {"sent": to_hex(raw), "reply_func": msg.func_name, "reply_data": to_hex(msg.data[:256])}


# --- Comunicación y exploración en caliente ---------------------------------------
@mcp.tool()
def connection_status() -> dict:
    """Estado de la conexión: puertos, canal, permisos, monitor y base de conocimiento."""
    c = client()
    t = c.t
    return {
        "transport": type(t).__name__,
        "midi_in": getattr(t, "in_name", None),
        "midi_out": getattr(t, "out_name", None),
        "global_channel": c.channel + 1,
        "write_enabled": ALLOW_WRITE,
        "monitor_seq": MONITOR.seq,
        "monitor_log": str(MONITOR.log_path) if MONITOR.log_path else None,
        "knowledge_dir": str(KB.root),
        "known_params": {k: len(v) for k, v in KB.params().items()},
        "notes": len(KB.notes()),
    }


@mcp.tool()
def monitor(since: int = 0, only_unsolicited: bool = False, limit: int = 100) -> dict:
    """Mensajes MIDI registrados (enviados y recibidos) con número de secuencia y decodificación.
    only_unsolicited=true muestra solo lo que el Kronos envió por su cuenta (p. ej. al tocar controles)."""
    client()
    return {"current_seq": MONITOR.seq, "messages": MONITOR.since(since, only_unsolicited=only_unsolicited, limit=limit)}


@mcp.tool()
def monitor_mark() -> int:
    """Devuelve el nº de secuencia actual, para luego pedir con monitor(since=...) solo lo nuevo."""
    client()
    return MONITOR.seq


@mcp.tool()
def probe(func_hex: str, payload_hex: str = "", expect_hex: str | None = None) -> dict:
    """Envía una PETICIÓN de solo lectura al Kronos para explorar el protocolo (no requiere permisos).
    Permitidas: 12, 30, 32, 34, 37, 39, 60, 72, 74, 79 (hex). expect_hex: función de respuesta esperada;
    si se omite, se devuelve lo que llegue en 2 s."""
    func = int(func_hex, 16)
    if func not in SAFE_REQUESTS:
        raise PermissionError(f"0x{func:02X} no está en la lista de peticiones seguras {sorted(f'{x:02X}' for x in SAFE_REQUESTS)}")
    payload = from_hex(payload_hex) if payload_hex.strip() else b""
    c = client()
    mark = MONITOR.seq
    from .protocol.messages import build

    raw = build(c.channel, func, payload)
    try:
        if expect_hex:
            msg = c.send_raw(raw, int(expect_hex, 16))
            reply = {"func": msg.func_name, "data_len": len(msg.data), "data_head": to_hex(msg.data[:96])}
        else:
            c.send_raw(raw)
            import time as _t

            _t.sleep(2)
            reply = None
    except Exception as e:  # noqa: BLE001 - el error también es información
        reply = {"error": str(e)}
    return {"sent": to_hex(raw), "reply": reply, "traffic": MONITOR.since(mark, limit=20)}


def _read(obj: ObjType, bank: str | None, index: int | None) -> tuple[bytes, dict]:
    if bank is None:
        data = client().read_current_object(obj)
        return data, {"source": "edit_buffer"}
    b = _bank(bank)
    data, _ = client().read_object(obj, b, index or 0)
    return data, {"source": "stored", "bank": _label(obj, b), "index": index or 0}


@mcp.tool()
def snapshot(obj_type: str, label: str = "", bank: str | None = None, index: int | None = None) -> dict:
    """Guarda una foto de un objeto (por defecto el buffer de edición; con bank/index, uno almacenado)."""
    obj = ObjType[obj_type.upper()]
    data, meta = _read(obj, bank, index)
    if obj in NAMED_TYPES:
        meta["name"] = _name(data)
    sid = KB.save_snapshot(obj.name, data, label, meta)
    return {"snapshot": sid, "size": len(data), **meta}


@mcp.tool()
def list_snapshots(obj_type: str | None = None, limit: int = 20) -> list[dict]:
    """Lista las fotos guardadas (más recientes primero)."""
    return KB.list_snapshots(obj_type.upper() if obj_type else None, limit)


@mcp.tool()
def diff_snapshots(snapshot_a: str, snapshot_b: str, max_runs: int = 64) -> dict:
    """Compara dos fotos byte a byte: tramos cambiados con interpretaciones u8/s8/16/32 bits."""
    a, ia = KB.load_snapshot(snapshot_a)
    b, ib = KB.load_snapshot(snapshot_b)
    res = {"a": ia, "b": ib, **KB.diff(a, b, max_runs)}
    res["params_changed"] = _annotate(ia["obj_type"], a, b)
    return res


def _annotate(obj_name: str, a: bytes, b: bytes) -> list[dict]:
    try:
        obj = ObjType[obj_name]
        if P.available() and (obj in P.MAP_FOR or obj == ObjType.PROGRAM):
            return P.annotate_changes(obj, a, b)
    except Exception as e:  # noqa: BLE001
        return [{"error": str(e)}]
    return []


@mcp.tool()
def learn_start(obj_type: str = "PROGRAM", description: str = "") -> dict:
    """Paso 1 del aprendizaje: foto del buffer de edición y marca en el monitor.
    Después pide al usuario que cambie UN parámetro en el teclado y llama a learn_finish."""
    obj = ObjType[obj_type.upper()]
    snap = snapshot(obj.name, f"antes: {description}")
    sid = uuid.uuid4().hex[:8]
    _learn_sessions[sid] = {"obj": obj.name, "description": description, "before": snap["snapshot"], "seq": MONITOR.seq}
    return {"session": sid, "before": snap, "next": "Cambia un solo parámetro en el Kronos y avísame para llamar a learn_finish."}


@mcp.tool()
def learn_finish(session: str, max_runs: int = 32) -> dict:
    """Paso 2: foto 'después', diff y mensajes que el Kronos envió mientras se cambiaba el parámetro."""
    s = _learn_sessions[session]
    after = snapshot(s["obj"], f"después: {s['description']}")
    a, _ = KB.load_snapshot(s["before"])
    b, _ = KB.load_snapshot(after["snapshot"])
    d = KB.diff(a, b, max_runs)
    named = _annotate(s["obj"], a, b)
    unsolicited = MONITOR.since(s["seq"], only_unsolicited=True, limit=50)
    s.update(after=after["snapshot"], finished=True)
    hint = (
        "Sin cambios: ¿se tocó el objeto correcto o el cambio no afecta al buffer de edición?"
        if d["changed_runs"] == 0
        else "Un tramo pequeño = candidato claro → learn_param. Muchos tramos = repetir con un cambio más aislado."
    )
    if named and not any("error" in n or "note" in n for n in named):
        hint = "Parámetros identificados con el mapa oficial. Si coincide con lo que cambiaste, regístralo con note() (ya está en el mapa oficial; learn_param solo para lo que falte)."
    return {"session": session, "description": s["description"], "params_changed": named, "diff": d, "kronos_sent": unsolicited, "hint": hint}


@mcp.tool()
def learn_param(
    obj_type: str,
    name: str,
    offset: int,
    size: int = 1,
    bits: str | None = None,
    signed: bool = False,
    endian: str = "le",
    values: dict[str, str] | None = None,
    value_range: str | None = None,
    notes: str | None = None,
    evidence: str | None = None,
    confidence: str = "probable",
) -> dict:
    """Registra un parámetro descubierto en la base de conocimiento.
    bits: '3' o '0-2' si ocupa solo parte del byte. values: {"0":"Off","1":"On"}. confidence: probable|confirmado."""
    return KB.learn_param(
        ObjType[obj_type.upper()].name, name, offset, size,
        bits=bits, signed=signed or None, endian=endian if size > 1 else None, values=values,
        range=value_range, notes=notes, evidence=evidence, confidence=confidence,
    )


@mcp.tool()
def knowledge(obj_type: str | None = None, include_notes: bool = True) -> dict:
    """Lo aprendido hasta ahora: mapa de parámetros por tipo de objeto y notas de protocolo."""
    out = {"params": KB.params(ObjType[obj_type.upper()].name if obj_type else None)}
    if include_notes:
        out["notes"] = KB.notes()[-50:]
    return out


@mcp.tool()
def decode_current(obj_type: str = "PROGRAM") -> dict:
    """Lee el buffer de edición y lo interpreta con el mapa aprendido."""
    obj = ObjType[obj_type.upper()]
    data = client().read_current_object(obj)
    res = {"type": obj.name, "size": len(data), "values": KB.decode(obj.name, data)}
    if obj in NAMED_TYPES:
        res["name"] = _name(data)
    return res


@mcp.tool()
def note(text: str, topic: str = "protocolo", evidence: str = "") -> dict:
    """Guarda una observación (formato de un mensaje, comportamiento, duda resuelta…)."""
    return KB.add_note(text, topic, evidence)


@mcp.tool()
def export_knowledge() -> str:
    """Genera docs/mapa_parametros.md con todo lo aprendido."""
    return str(KB.export_markdown(PROJECT_DIR / "docs" / "mapa_parametros.md"))


@mcp.prompt()
def descubrir_parametro(parametro: str, obj_type: str = "PROGRAM") -> str:
    """Guía para descubrir dónde vive un parámetro dentro de un objeto."""
    return (
        f"Quiero localizar el parámetro '{parametro}' dentro de un {obj_type} del Kronos.\n"
        "1. Consulta `knowledge` por si ya está.\n"
        f"2. Llama a `learn_start(obj_type='{obj_type}', description='{parametro}')`.\n"
        "3. Pídeme que cambie solo ese parámetro y dime qué valor anotar antes y después.\n"
        "4. Cuando te avise, `learn_finish` y analiza el diff y los mensajes del Kronos.\n"
        "5. Si es concluyente, `learn_param` con la evidencia; si no, propón otro cambio para aislarlo.\n"
        "6. Al terminar, `export_knowledge`."
    )


# --- Mapas oficiales y funciones de la doc Korg ---------------------------------------
@mcp.tool()
def system_status() -> dict:
    """Estado del Kronos: identidad, modo, reloj MIDI, protección de memoria, performance activa y tipos de banco."""
    c = client()
    out: dict = {}
    for key, fn in (("identity", c.identify), ("mode", c.mode_data), ("current_performance", c.current_performance), ("program_bank_types", c.program_bank_types)):
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001
            out[key] = {"error": str(e)}
    return out


@mcp.tool()
def system_info(what: str = "pianos") -> list | dict:
    """Inventario: 'pianos' (SGX instalados), 'samples' (bancos de multisamples ROM/EXs/usuario) o 'bank_types'."""
    c = client()
    if what == "pianos":
        return c.piano_types()
    if what == "samples":
        return c.sample_banks()
    if what == "bank_types":
        return c.program_bank_types()
    raise ValueError("what = pianos | samples | bank_types")


@mcp.tool()
def bank_status(model: str = "KRONOS") -> dict:
    """Pide al Kronos los digests SHA-1 de TODOS los bancos (1 mensaje) y los compara con fábrica.
    model: KRONOS (original/X) o NewKRONOS (Kronos 2). Ideal para auditar qué se ha modificado."""
    fd = _factory_digests().get(model, {})
    rows, counts = [], {"factory": 0, "modified": 0, "no_reference": 0}
    for e in client().digest_collection():
        key = f"{e['obj']:02X}:{e['bank']:02X}"
        try:
            oname = ObjType(e["obj"]).name
        except ValueError:
            oname = f"0x{e['obj']:02X}"
        ref = fd.get(key)
        state = "no_reference" if not ref else ("factory" if ref["sha1"] == e["sha1"] else "modified")
        counts[state] += 1
        rows.append({"type": oname, "bank": _label(e["obj"], e["bank"]), "state": state})
    return {"summary": counts, "modified": [r for r in rows if r["state"] == "modified"], "all": rows}


@mcp.tool()
def decode_object(obj_type: str = "PROGRAM", bank: str | None = None, index: int | None = None,
                  query: str | None = None, limit: int = 150) -> dict:
    """Interpreta un objeto con el mapa oficial (nombres reales, efectos y motor EXi incluidos).
    Sin bank/index usa el buffer de edición. query filtra por texto (p. ej. 'Filter', 'Insert Effect1', 'LFO')."""
    obj = ObjType[obj_type.upper()]
    data, meta = _read(obj, bank, index)
    return {**meta, **_summary(obj, data), "size": len(data), "params": P.decode(obj, data, query, limit)}


@mcp.tool()
def find_parameter(text: str, scope: str | None = None, limit: int = 30) -> list[dict]:
    """Busca parámetros por nombre en los mapas oficiales → offset y Parameter ID (TYP,SOC,SUB,PID,IDX).
    scope: program_hd1, program_exi, combination, global, set_list, song_control, drum_kit, wave_seq."""
    return P.find(text, [scope] if scope else None, limit)


@mcp.tool()
def set_parameter(value: int, param: str | None = None, param_id: str | None = None,
                  obj_type: str = "PROGRAM", confirm: bool = False) -> dict:
    """Cambia un parámetro EN EL BUFFER DE EDICIÓN con Parameter Change (0x43). No guarda en memoria:
    el sonido vuelve a su estado al cambiar de Program/Combi salvo que el usuario haga Write.
    param: nombre (se busca en el mapa del objeto actual) · param_id: 'TYP,SOC,SUB,PID,IDX'."""
    if not confirm:
        raise PermissionError("Modifica el sonido actual: repite con confirm=true.")
    obj = ObjType[obj_type.upper()]
    c = client()
    before = c.read_current_object(obj)
    map_name = P.map_name(obj, len(before))
    target = None
    if param_id:
        ids = [int(x) for x in param_id.split(",")]
    else:
        # solo campos con Parameter ID completo: los de Effect.txt traen únicamente PID (TYP/SOC dependen del slot y
        # la doc no los da: se aprenden viendo el 0x43 que emite el Kronos al mover el parámetro)
        cands = [(f, base, lab) for f, base, lab in P.iter_fields(obj, before)
                 if "TYP" in f.get("id", {}) and param and param.lower() in lab.lower()]
        exact = [x for x in cands if x[2].lower() == param.lower()]
        cands = exact or cands
        if len(cands) != 1:
            return {"error": "ambiguo o no encontrado", "candidates": [x[2] for x in cands[:25]], "map": map_name}
        target = cands[0]
        i = target[0]["id"]
        ids = [int(i.get(k, 0)) for k in ("TYP", "SOC", "SUB", "PID", "IDX")]
    c.set_parameter(*ids, value)
    after = c.read_current_object(obj)
    return {"sent_id": ",".join(map(str, ids)), "value": value, "map": map_name,
            "verified_changes": P.annotate_changes(obj, before, after)}


# --- Backup completo y foto de referencia ---------------------------------------------
# (tipo, bancos) a respaldar. Los bancos GM/g(n) son de solo lectura y no se incluyen.
_USER = [0x40 + i for i in range(14)]  # USER-A..G, USER-AA..GG
BACKUP_PLAN: dict[str, list[tuple[ObjType, int]]] = {
    "programs": [(ObjType.PROGRAM, b) for b in list(range(6)) + _USER],
    "combis": [(ObjType.COMBINATION, b) for b in list(range(7)) + _USER[:7]],
    "drumkits": [(ObjType.DRUM_KIT, b) for b in [0] + _USER],
    "waveseqs": [(ObjType.WAVE_SEQ, b) for b in [0] + _USER],
    "setlists": [(ObjType.SET_LIST, 0)],
    "songs": [(ObjType.SONG_TIMBRE_SET, 0), (ObjType.SONG_CONTROL, 0)],
    "drumtrack": [(ObjType.DRUM_TRACK_PATTERN, 0)],
    "karma": [(ObjType.KARMA_GE, b) for b in range(12)],
}
BASELINE = KB.root / "baseline_digests.json"


def _digests() -> dict[str, str]:
    return {f"{e['obj']:02X}:{e['bank']:02X}": e["sha1"] for e in client().digest_collection()}


@mcp.tool()
def backup_all(groups: str = "all", folder: str | None = None) -> dict:
    """Backup completo del Kronos en backups/full_<fecha>/ (un .syx restaurable por banco + Global + manifiesto).
    Verifica cada banco contra el digest SHA-1 del propio Kronos y guarda esos digests como foto de referencia.
    groups: 'all' o lista separada por comas de: programs, combis, drumkits, waveseqs, setlists, songs, drumtrack, karma.
    folder: para continuar un backup a medias en la misma carpeta (las llamadas largas se pueden partir por grupos)."""
    import hashlib as _h
    import time as _t

    sel = list(BACKUP_PLAN) if groups == "all" else [g.strip() for g in groups.split(",")]
    out_dir = Path(folder) if folder else BACKUP_DIR / f"full_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    man_path = out_dir / "manifest.json"
    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {"created": datetime.now().isoformat(timespec="seconds"), "banks": {}}
    t0 = _t.time()
    try:
        dev = _digests()
        manifest["device_digests"] = dev
    except Exception as e:  # noqa: BLE001
        dev = manifest.get("device_digests", {})
        manifest["digest_error"] = str(e)
    if "identity" not in manifest:
        try:
            manifest["identity"] = client().identify()
        except Exception:  # noqa: BLE001
            pass
    for g in sel:
        for obj, b in BACKUP_PLAN[g]:
            key = f"{int(obj):02X}:{b:02X}"
            name = f"{obj.name}_{_label(obj, b)}"
            try:
                items = _bank_raw(obj, b)
                (out_dir / f"{name}.syx").write_bytes(b"".join(raw for _, _, raw in items))
                sha = _h.sha1(b"".join(data for _, data, _ in items)).hexdigest()
                manifest["banks"][key] = {"file": f"{name}.syx", "objects": len(items), "sha1": sha,
                                          "verified": (sha == dev.get(key)) if key in dev else None}
            except Exception as e:  # noqa: BLE001
                manifest["banks"][key] = {"file": None, "error": str(e)}
            man_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    if groups == "all" or "global" in sel:
        try:
            m = client().call(Func.CURR_OBJECT_DUMP_REQ, int(ObjType.GLOBAL), expect=Func.CURR_OBJECT_DUMP)
            from .protocol.messages import build as _build

            (out_dir / "GLOBAL.syx").write_bytes(_build(client().channel, Func.CURR_OBJECT_DUMP, m.data))
            manifest["global"] = {"file": "GLOBAL.syx", "note": "Current Object Dump (75): al restaurar va al buffer; hay que hacer Write en Global."}
        except Exception as e:  # noqa: BLE001
            manifest["global"] = {"error": str(e)}
    manifest["seconds"] = round(manifest.get("seconds", 0) + _t.time() - t0, 1)
    man_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    if dev:
        BASELINE.write_text(json.dumps({"taken": datetime.now().isoformat(timespec="seconds"), "backup": str(out_dir), "digests": dev}, indent=1), encoding="utf-8")
    banks = manifest["banks"]
    return {
        "folder": str(out_dir.resolve()),
        "banks_saved": sum(1 for v in banks.values() if v.get("file")),
        "verified_ok": sum(1 for v in banks.values() if v.get("verified")),
        "verify_mismatch": [k for k, v in banks.items() if v.get("verified") is False],
        "errors": {k: v["error"] for k, v in banks.items() if "error" in v},
        "global": manifest.get("global"),
        "bytes": sum(f.stat().st_size for f in out_dir.glob("*.syx")),
        "seconds": manifest["seconds"],
        "baseline_saved": bool(dev),
    }


@mcp.tool()
def changes_since_baseline() -> dict:
    """Qué bancos han cambiado desde la última foto de referencia (la guarda backup_all o set_baseline)."""
    if not BASELINE.exists():
        return {"error": "No hay foto de referencia: ejecuta backup_all o set_baseline."}
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    now = _digests()
    changed = []
    for k, sha in now.items():
        if base["digests"].get(k) != sha:
            t, b = (int(x, 16) for x in k.split(":"))
            try:
                tn = ObjType(t).name
            except ValueError:
                tn = f"0x{t:02X}"
            changed.append({"type": tn, "bank": _label(t, b), "new_bank": k not in base["digests"]})
    return {"baseline_taken": base["taken"], "baseline_backup": base.get("backup"), "changed": changed, "unchanged": len(now) - len(changed)}


@mcp.tool()
def set_baseline() -> dict:
    """Guarda los digests actuales de todos los bancos como nueva foto de referencia (sin hacer backup)."""
    dev = _digests()
    BASELINE.write_text(json.dumps({"taken": datetime.now().isoformat(timespec="seconds"), "backup": None, "digests": dev}, indent=1), encoding="utf-8")
    return {"banks": len(dev), "file": str(BASELINE)}


# --- Recursos ------------------------------------------------------------------
@mcp.resource("kronos://reference/official-sysex")
def official_sysex() -> str:
    """Especificación oficial de Korg: KRONOS_MIDI_SysEx.txt (v1.26)."""
    p = PROJECT_DIR / "docs" / "korg_oficial" / "KRONOS_MIDI_SysEx.txt"
    return p.read_text(encoding="latin-1") if p.exists() else "No encontrada"


@mcp.resource("kronos://knowledge")
def knowledge_resource() -> str:
    """Mapa de parámetros aprendido y notas."""
    return json.dumps({"params": KB.params(), "notes": KB.notes()}, ensure_ascii=False, indent=1)


@mcp.resource("kronos://reference/sysex")
def sysex_reference() -> str:
    """Referencia del protocolo SysEx del Kronos."""
    p = Path(__file__).resolve().parents[2] / "docs" / "sysex" / "kronos_sysex_referencia.md"
    return p.read_text(encoding="utf-8") if p.exists() else "No encontrada"


@mcp.resource("kronos://reference/constants")
def constants_reference() -> str:
    """Funciones, tipos de objeto y modos conocidos."""
    return json.dumps(
        {
            "functions": {f.name: f"0x{f.value:02X}" for f in Func},
            "object_types": {o.name: f"0x{o.value:02X}" for o in ObjType},
            "modes": {m.name: m.value for m in Mode},
        },
        indent=2,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

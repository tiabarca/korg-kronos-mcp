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
            transport.listeners.append(MONITOR.record)
            _client = KronosClient(transport, channel)
        else:
            # Puertos bajo demanda: se abren al usarlos y se liberan tras KRONOS_IDLE_RELEASE s sin uso
            _client = KronosClient(None, channel, factory=_open_midi)
            _client.start_idle_release(float(os.getenv("KRONOS_IDLE_RELEASE", "15")))
    return _client


def _open_midi():
    from .transport.midi import MidiTransport

    t = MidiTransport(os.getenv("KRONOS_MIDI_IN"), os.getenv("KRONOS_MIDI_OUT"))
    t.listeners.append(MONITOR.record)
    return t


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
def reconnect_midi() -> dict:
    """Cierra y vuelve a abrir los puertos MIDI del Kronos (tras desenchufar el USB, sin reiniciar la app)."""
    c = client()
    ok = c._reconnect()
    return {"reopened": ok, "midi_in": getattr(c._t, "in_name", None), "midi_out": getattr(c._t, "out_name", None)}


@mcp.tool()
def server_instances(kill_orphans: bool = False) -> dict:
    """Procesos kronos-mcp registrados (este y otros). Un huérfano es uno cuyo cliente ya no existe;
    con kill_orphans=true se terminan (solo huérfanos del mismo ejecutable)."""
    from . import lifecycle

    killed = lifecycle.clean_orphans(PID_DIR) if kill_orphans else []
    return {"this_pid": os.getpid(), "instances": lifecycle.list_instances(PID_DIR), "killed": killed}


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
    t = c._t
    return {
        "transport": type(t).__name__ if t else "cerrado (se abre al usarlo)",
        "midi_in": getattr(t, "in_name", None),
        "midi_out": getattr(t, "out_name", None),
        "ports_open": c.is_open,
        "idle_release_s": c.idle_release,
        "times_opened": c.opened,
        "pid": os.getpid(),
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
    client().hold = True  # mantener el puerto abierto para recibir lo que envíe el Kronos
    _ = client().t
    sid = uuid.uuid4().hex[:8]
    _learn_sessions[sid] = {"obj": obj.name, "description": description, "before": snap["snapshot"], "seq": MONITOR.seq}
    return {"session": sid, "before": snap, "next": "Cambia un solo parámetro en el Kronos y avísame para llamar a learn_finish."}


@mcp.tool()
def learn_finish(session: str, max_runs: int = 32) -> dict:
    """Paso 2: foto 'después', diff y mensajes que el Kronos envió mientras se cambiaba el parámetro."""
    s = _learn_sessions[session]
    after = snapshot(s["obj"], f"después: {s['description']}")
    client().hold = any(not v.get("finished") for k, v in _learn_sessions.items() if k != session)
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
                  query: str | list[str] | None = None, limit: int = 150, format: str = "compact",
                  header: bool | None = None, skip_defaults: bool | None = None, groups: list[str] | None = None) -> dict:
    """Interpreta un objeto con el mapa oficial (nombres reales, efectos y motor EXi incluidos).
    Sin bank/index usa el buffer de edición.
    format 'compact' (por defecto): una línea 'Nombre = valor'. 'full': param/value/ofs/range/id como antes.
    query: texto o LISTA de textos ('Filter', ['[Filter A] Cutoff', 'Amplifier EG]', 'Legato']) → una sola llamada.
    header: incluir effects/exi/size (por defecto solo si no hay query).
    skip_defaults: omitir parámetros a 0/vacío (por defecto activo si no hay query).
    groups: en compact sin query se excluyen KARMA, Audio Input, S/PDIF, Drum Track y Vector Control; pídelos aquí."""
    obj = ObjType[obj_type.upper()]
    data, meta = _read(obj, bank, index)
    if format not in ("compact", "full"):
        raise ValueError("format: compact | full")
    if header is None:
        header = not query
    out = dict(meta)
    if header or format == "full":
        out.update(_summary(obj, data))
        out["size"] = len(data)
    elif obj in NAMED_TYPES:
        out["name"] = _name(data)
    if format == "full":
        qs = [query] if isinstance(query, str) or query is None else query
        seen, params = set(), []
        for q in qs or [None]:
            for it in P.decode(obj, data, q, limit):
                if it["param"] not in seen:
                    seen.add(it["param"])
                    params.append(it)
        out["params"] = params[:limit]
        return out
    out.update(P.decode_compact(obj, data, query, groups, (not query) if skip_defaults is None else skip_defaults, limit))
    return out


@mcp.tool()
def voice_summary(ref: str | None = None, obj_type: str = "PROGRAM") -> dict:
    """Resumen legible (~30 líneas) de un Program o Combi en una llamada: voz Poly/Legato, osciladores, filtro,
    Amp EG y EG1 (A/D/S/R + niveles), LFOs activos y cadena de efectos (IFX → MFX con Returns → TFX).
    Detalle completo para AL-1; otros motores EXi muestran sus secciones principales no nulas.
    ref: 'USER-G 028' o 'INT-B 025'; sin ref usa el buffer de edición. obj_type: PROGRAM | COMBINATION."""
    from . import voice

    obj = ObjType[obj_type.upper()]
    if obj not in (ObjType.PROGRAM, ObjType.COMBINATION):
        raise ValueError("obj_type: PROGRAM | COMBINATION")
    data, meta = _read(obj, *_perf_ref(ref)) if ref else _read(obj, None, None)
    if obj == ObjType.PROGRAM:
        lines = voice.program(data)
    else:
        from . import catalog as C

        try:
            names = {k: v["name"] for k, v in (C.load(CATALOG) or {"programs": {}})["programs"].items()}
        except Exception:  # noqa: BLE001
            names = {}
        lines = voice.combi(data, client().channel + 1, names)
    return {"ref": ref or "edit_buffer", "lines": lines}


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


# --- Set Lists: lectura detallada y edición segura ---------------------------------------
_PERF_TYPE = {0: "Combi", 1: "Program", 2: "Song"}


def _read_set_list(n: int) -> tuple[bytes, bytes]:
    return client().read_object(ObjType.SET_LIST, 0, n)


def _perf_label(sl, slot: dict, names: dict) -> dict:
    t, b, i = slot["type"], slot["bank"], slot["index"]
    if t == 2:
        return {"perf": f"SONG {i}", "perf_name": None}
    bank = sl.COMBI_BANKS[b] if t == 0 and b < 14 else (sl.PROG_SLOT_BANKS[b] if t == 1 and b < 31 else f"?{b}")
    key = (t, b, i)
    if key not in names:
        ob = sl.slot_bank_to_object_bank(t, b)
        try:
            data, _ = client().read_object(ObjType.COMBINATION if t == 0 else ObjType.PROGRAM, ob, i)
            names[key] = _name(data)
        except Exception:  # noqa: BLE001
            names[key] = None
    return {"perf": f"{'COMBI' if t == 0 else 'PROGRAM'} {bank} {i}", "perf_name": names[key]}


def _slot_view(sl, data: bytes, slot: int, names: dict, resolve: bool = True) -> dict:
    s = sl.get_slot_raw(data, slot)
    v = {"slot": slot, "name": s["name"]}
    v.update(_perf_label(sl, s, names) if resolve else {"perf": (s["type"], s["bank"], s["index"])})
    v.update({k: s[k] for k in ("volume", "transpose", "color", "font", "hold_time", "kbd_track")})
    v["color_name"] = sl.COLOR_NAMES.get(s["color"], str(s["color"]))
    if s["comments"]:
        v["comments"] = s["comments"]
    return v


def _is_default_slot(sl, data: bytes, slot: int) -> bool:
    s = sl.get_slot_raw(data, slot)
    return (not s["name"] or s["name"].startswith("Slot")) and not s["comments"] and (s["type"], s["bank"], s["index"]) == (1, 0, 0)


@mcp.tool()
def show_set_list(number: int, include_empty: bool = False, resolve_names: bool = True) -> dict:
    """Muestra un Set List slot a slot: nombre, Program/Combi asignado (con su nombre real), volumen,
    transposición, color, fuente, hold time, pista de teclado y comentarios. No cambia nada en el teclado."""
    from .protocol import setlist as sl

    data, _ = _read_set_list(number)
    names: dict = {}
    slots = [_slot_view(sl, data, i, names, resolve_names) for i in range(128) if include_empty or not _is_default_slot(sl, data, i)]
    return {"set_list": number, "name": _name(data), "used_slots": len(slots) if not include_empty else None, "slots": slots}


@mcp.tool()
def edit_set_list(number: int, changes: list[dict], new_name: str | None = None, confirm: bool = False) -> dict:
    """Edita un Set List. Sin confirm=true solo devuelve la vista previa (antes/después); no escribe nada.
    changes: lista de operaciones, cada una un dict:
      {"slot": 3, "name": "PULL ME UNDER", "perf": "COMBI USER-E 64", "volume": 110, "transpose": -2,
       "color": "Navy" (0-15 o nombre: Default, Charcoal, Brick, Burgundy, Ivy, Olive, Gold, Cacao, Indigo, Navy, Rose, Lavender, Azure, Denim, Silver, Slate), "font": 2, "hold_time": 0, "kbd_track": 0, "comments": "texto"}   (solo los campos a cambiar)
      {"slot": 4, "copy_from": {"set_list": 16, "slot": 2}}     copia un slot completo de otra lista
      {"slot": 9, "clear": true}                                deja el slot vacío (Program INT-A 000)
    Perf: 'COMBI INT-A..G|USER-A..G n' · 'PROGRAM INT-A..F|USER-A..GG|GM|g(1..9)|g(d) n' · 'SONG n'.
    Con confirm=true: backup de la lista → Object Dump (73) → Store Bank (76) → relectura y verificación."""
    from .protocol import setlist as sl

    before, raw_before = _read_set_list(number)
    data = bytearray(before)
    src_cache: dict[int, bytes] = {}
    touched = set()
    for ch in changes:
        ch = dict(ch)
        slot = int(ch.pop("slot"))
        touched.add(slot)
        if ch.pop("clear", False):
            sl.set_slot(data, slot, name="", perf="PROGRAM INT-A 0", volume=127, transpose=0, color=0, font=0,
                        hold_time=0, kbd_track=0, comments="")
            continue
        src = ch.pop("copy_from", None)
        if src:
            n = int(src["set_list"])
            if n not in src_cache:
                src_cache[n] = bytes(data) if n == number else _read_set_list(n)[0]
            sl.copy_slot(data, slot, src_cache[n], int(src["slot"]))
        if ch:
            sl.set_slot(data, slot, **ch)
    if new_name is not None:
        sl.set_list_name(data, new_name)
    names: dict = {}
    preview = [{"slot": i, "before": _slot_view(sl, before, i, names), "after": _slot_view(sl, bytes(data), i, names)} for i in sorted(touched)]
    res = {"set_list": number, "name_before": _name(before), "name_after": _name(bytes(data)), "preview": preview}
    if bytes(data) == before:
        return {**res, "status": "sin cambios"}
    if not confirm:
        return {**res, "status": "vista previa: repite con confirm=true para escribir en el Kronos"}
    _require_write(confirm)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bpath = BACKUP_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_SET_LIST_{number:03d}_antes.syx"
    bpath.write_bytes(raw_before)
    c = client()
    c.write_object(ObjType.SET_LIST, 0, number, bytes(data))
    c.store_bank(ObjType.SET_LIST, 0)
    after, _ = _read_set_list(number)
    ok = after == bytes(data)
    out = {**res, "status": "escrito y verificado" if ok else "ESCRITO PERO LA RELECTURA NO COINCIDE",
           "verified": ok, "backup_before": str(bpath.resolve())}
    if not ok:
        out["diff_bytes"] = sum(1 for a, b in zip(after, data) if a != b)
    return out


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
        # si ni siquiera contesta a 0x39, no tiene sentido seguir: casi siempre es la entrada MIDI ocupada
        return {"error": f"El Kronos no responde ({e}).",
                "hint": "¿Otro programa tiene abierta la entrada MIDI (otro kronos-mcp.exe, Claude Code, un editor)? "
                        "Ciérralo y reinicia el server. La salida se comparte, así que el Kronos recibe pero no llegan las respuestas.",
                "folder": str(out_dir.resolve())}
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


# --- Diseño de sonidos -------------------------------------------------------------
DESIGNS = DATA_DIR / "designs"


def _perf_ref(text: str) -> tuple[int, int]:
    parts = text.upper().replace(",", " ").split()
    return bank_code(parts[0]), int(parts[1])


def _catalog_after_store(obj: ObjType, bank: int, index: int, data: bytes) -> bool:
    """Refleja en el catálogo (si existe) el Program/Combi recién guardado, sin refresh_catalog."""
    from . import catalog as C

    cat = C.load(CATALOG)
    if cat is None:
        return False
    C.update_entry(cat, int(obj), bank, index, data, client().channel + 1)
    try:  # el digest nuevo evita que refresh_catalog relea el banco entero
        cat["bank_sha1"][f"{int(obj):02X}:{bank:02X}"] = client().bank_digest(obj, bank)
    except Exception:  # noqa: BLE001
        pass
    _save_catalog(cat)
    return True


def _design(obj: ObjType, target: str, changes: list[dict], name: str | None, base: str | None,
            action: str, confirm: bool, verbose: bool = False, init_unused_timbres: bool = False) -> dict:
    from . import design as D

    label = "PROGRAM" if obj == ObjType.PROGRAM else "COMBI"
    tb, ti = _perf_ref(target)
    bb, bi = _perf_ref(base) if base else (tb, ti)
    data, _ = client().read_object(obj, bb, bi)
    sources: dict[str, bytes] = {}
    ops = []
    for ch in changes:
        ch = dict(ch)
        if "from" in ch:
            ref = ch["from"]
            kind = ObjType.COMBINATION if str(ch.pop("from_type", "PROGRAM")).upper().startswith("COMBI") else ObjType.PROGRAM
            key = f"{kind.name}:{ref}"
            if key not in sources:
                sb, si = _perf_ref(ref)
                sources[key], _ = client().read_object(kind, sb, si)
            ch["from"] = sources[key]
        ops.append(ch)
    extra: dict = {}
    if init_unused_timbres:
        if obj != ObjType.COMBINATION:
            raise ValueError("init_unused_timbres solo aplica a Combis")
        more, skipped = D.unused_timbre_ops(obj, data, ops)
        ops += more
        if skipped:
            extra["init_skipped_in_use"] = skipped
    if name:
        ops.append({"name": name})
    new, log = D.apply(obj, data, ops)
    DESIGNS.mkdir(parents=True, exist_ok=True)
    rec = {"type": label, "target": target, "base": base or target, "name": name, "changes": changes,
           "ts": datetime.now().isoformat(timespec="seconds")}
    (DESIGNS / f"{datetime.now():%Y%m%d_%H%M%S}_{label}_{(name or target).replace(' ', '_').replace('/', '-')}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    res = {"type": label, "target": target, "applied": log, "action": action, **extra}
    if base and base != target:
        res["base"] = base
    if verbose:
        if obj == ObjType.PROGRAM:
            res["engine"] = "EXi" if len(new) == PROGRAM_SIZE_EXI else "HD-1"
        else:
            from . import catalog as C

            res["timbres"] = [{k: t[k] for k in ("timbre", "status", "program_bank", "program_number", "midi_channel", "volume", "transpose", "key_zone")}
                              for t in C.combi_timbres(new, client().channel + 1)]
        res["summary"] = P.summary(obj, new)
    if action == "preview":
        return res
    if action == "audition":
        want = "PROGRAM" if obj == ObjType.PROGRAM else "COMBINATION"
        if client().get_mode().name != want:
            raise RuntimeError(f"Pon el Kronos en {want} mode (con un {label} seleccionado) y repite.")
        cur = client().read_current_object(obj)
        if len(cur) != len(new):
            raise RuntimeError(f"El {label} seleccionado es de otro tipo/tamaño ({len(cur)} vs {len(new)} bytes): selecciona {target}.")
        client().write_current_object(obj, new)
        back = client().read_current_object(obj)
        res["status"] = "en el buffer de edición: toca el teclado (no está guardado)"
        res["verified"] = D.same(obj, back, new) or D.same(obj, back[24:], new[24:])
        return res
    if action == "store":
        _require_write(confirm)
        old, raw = client().read_object(obj, tb, ti)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        bk = BACKUP_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{label}_{target.replace(' ', '_')}_antes.syx"
        bk.write_bytes(raw)
        client().write_object(obj, tb, ti, new)
        client().store_bank(obj, tb)
        back, _ = client().read_object(obj, tb, ti)
        ok = D.same(obj, back, new)
        res.update(status="guardado y verificado" if ok else "GUARDADO PERO LA RELECTURA NO COINCIDE",
                   verified=ok, backup_before=str(bk))
        if not ok:
            res["diff_bytes"] = sum(1 for a, b in zip(back, new) if a != b)
        try:
            res["catalog_updated"] = _catalog_after_store(obj, tb, ti, back)
        except Exception as e:  # noqa: BLE001 - el catálogo nunca debe hacer fallar un store ya hecho
            res["catalog_updated"] = f"error: {e}"
        return res
    raise ValueError("action: preview | audition | store")


@mcp.tool()
def design_program(target: str, changes: list[dict], name: str | None = None, base: str | None = None,
                   action: str = "preview", confirm: bool = False, verbose: bool = False) -> dict:
    """Diseña un Program aplicando cambios con nombre (mapas oficiales, incluido el motor EXi y los efectos).
    target: dónde vivirá, p. ej. 'USER-AA 003'. base: Program de partida (por defecto el propio target).
    changes: [{"param": "Filter A] Cutoff", "value": 55}, {"copy_effect": "Insert Effect2", "from": "INT-C 035",
              "to": "Insert Effect1"}, {"copy_range": [1048, 1050], "from": "INT-C 035"}]
    action: 'preview' (solo calcula), 'audition' (lo manda al buffer de edición para tocarlo: el teclado debe estar
    en Program mode con un Program del mismo tipo EXi/HD-1 seleccionado; no guarda nada),
    'store' (lo escribe en target y lo guarda: requiere confirm=true; hace copia del original antes).
    copy_effect conserva por defecto Chain Switch / Chain To / Bus Select del destino (keep_routing=false para copiarlos)
    y, en Master Effect1/2, copia también su Return (copy_return=false lo evita).
    Respuesta ligera: applied (solo lo que cambia), status, verified, backup_before. verbose=true añade el summary."""
    return _design(ObjType.PROGRAM, target, changes, name, base, action, confirm, verbose)


@mcp.tool()
def design_combi(target: str, changes: list[dict], name: str | None = None, base: str | None = None,
                 action: str = "preview", confirm: bool = False, verbose: bool = False,
                 init_unused_timbres: bool = False) -> dict:
    """Diseña una Combi con cambios con nombre (mapa oficial CombiAndSongTimbreSet).
    Timbres: {"param": "Timbre1 / Bank Number", "value": 24} (códigos de banco: INT-A..F 0-5, GM 6, g(1..9) 7-15,
    g(d) 16, USER-A..G 17-23, USER-AA..GG 24-30), "Timbre1 / Program Number", "Timbre1 / Status" (0 Off, 1 INT),
    "Timbre1 / MIDI Channel" (16 = canal global), "Timbre1 / Volume", "Timbre1 / Pan" (64 = centro),
    "Timbre1 / Transpose", "Timbre1 / Bus Select" (0 L/R, 1 IFX1, 2 IFX2…), "Timbre1 / Send1", "Timbre1 / Send2".
    Efectos: {"copy_effect": "Insert Effect1", "from": "USER-AA 003", "from_type": "PROGRAM", "to": "Insert Effect2"}.
    action: preview | audition (Combination mode, Combi seleccionada) | store (confirm=true, copia previa).
    init_unused_timbres=true: Status=Off en los timbres que `changes` no toca y que siguen inicializados
    (Program 0, banco INT-A); los que parecen en uso se dejan y se listan en init_skipped_in_use.
    copy_effect: keep_routing (true) conserva Chain/Bus del destino; en Master Effect copia también el Return.
    Respuesta ligera (applied/status/verified/backup_before); verbose=true añade timbres y summary."""
    return _design(ObjType.COMBINATION, target, changes, name, base, action, confirm, verbose, init_unused_timbres)


@mcp.tool()
def play_notes(events: list[dict], bpm: float = 120, channel: int | None = None, select: str | None = None,
               select_mode: str = "COMBINATION") -> dict:
    """Toca notas por MIDI para oír un sonido (no escribe nada en el Kronos).
    events: [{"n": 74 | "D5" | [50,54,57], "dur": 0.5 (negras), "vel": 100, "at": opcional (negras desde el inicio)}];
    sin "n" es un silencio; sin "at" cada evento empieza al acabar el anterior. channel 1-16 (por defecto el global).
    select: 'INT-B 025' -> Bank Select + Program Change antes de tocar (pasa a select_mode COMBINATION|PROGRAM si hace falta;
    en los bancos USER-* el LSB es el código de banco del SysEx: sin confirmar). Límite 60 s; siempre acaba con All Notes Off."""
    from . import play

    return play.play(client(), events, bpm, channel, select, select_mode)


# --- Extensiones en caliente (sin reiniciar la app) ---------------------------------
SCRIPTS_DIR = PROJECT_DIR / "scripts"


@mcp.tool()
def run_script(name: str, args: dict | None = None, confirm: bool = False) -> dict:
    """Ejecuta scripts/<name>.py dentro del server, sin reiniciar la app. Sirve para probar funciones nuevas
    antes de convertirlas en herramientas. El script define run(ctx, **args) y devuelve un dict;
    ctx trae client, P (mapas), design, catalog, ObjType, KB, MONITOR, PROJECT_DIR y require_write.
    Antes de ejecutar recarga los módulos design, catalog y params, así sus cambios entran al momento.
    Requiere confirm=true (el script puede escribir en el Kronos si llama a ctx.require_write)."""
    import importlib
    import importlib.util
    import types

    from . import catalog as C
    from . import design as D

    if not confirm:
        raise PermissionError("run_script ejecuta código del proyecto: repite con confirm=true.")
    path = (SCRIPTS_DIR / f"{name}.py").resolve()
    if SCRIPTS_DIR.resolve() not in path.parents or not path.exists():
        raise FileNotFoundError(f"No existe scripts/{name}.py")
    for mod in (P, D, C):
        importlib.reload(mod)
    spec = importlib.util.spec_from_file_location(f"kronos_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = types.SimpleNamespace(client=client(), P=P, design=D, catalog=C, ObjType=ObjType, KB=KB, MONITOR=MONITOR,
                                PROJECT_DIR=PROJECT_DIR, DATA_DIR=DATA_DIR, BACKUP_DIR=BACKUP_DIR,
                                require_write=lambda: _require_write(True), perf_ref=_perf_ref)
    return module.run(ctx, **(args or {}))


# --- Catálogo de Programs y Combis -------------------------------------------------
CATALOG = DATA_DIR / "catalog.json"


def _catalog() -> dict:
    from . import catalog as C

    cat = C.load(CATALOG)
    if cat is None:
        raise RuntimeError("No hay catálogo todavía: llama a build_catalog().")
    return cat


def _save_catalog(cat: dict) -> None:
    from . import catalog as C

    C.resolve(cat)
    C.save(cat, CATALOG)
    (DATA_DIR / "catalog.md").write_text(C.to_markdown(cat), encoding="utf-8")


def _fetch_gm_names(cat: dict) -> int:
    """Nombres de los Programs GM/g(n) que usan las Combis (son de solo lectura y no van en el backup)."""
    from . import catalog as C
    from .protocol.setlist import PROG_SLOT_BANKS

    got = 0
    back = {C.slot_bank_to_object_bank(1, i): name for i, name in enumerate(PROG_SLOT_BANKS)}
    for ob, n in sorted(C.gm_refs(cat)):
        key = f"{back.get(ob, hex(ob))} {n:03d}"
        if key in cat["gm_names"]:
            continue
        try:
            data, _ = client().read_object(ObjType.PROGRAM, ob, n)
            cat["gm_names"][key] = _name(data)
            got += 1
        except Exception:  # noqa: BLE001
            pass
    return got


@mcp.tool()
def build_catalog(backup_folder: str | None = None, fetch_gm_names: bool = True) -> dict:
    """Crea el catálogo de Programs y Combis (nombres y qué Programs usa cada Combi) desde un backup
    completo (por defecto el más reciente en backups/full_*), sin leer bancos del teclado.
    fetch_gm_names: pide al Kronos solo los nombres de los Programs GM/g(n) que usan las Combis.
    Guarda data/catalog.json y data/catalog.md. Después usa refresh_catalog para ponerlo al día."""
    from . import catalog as C

    if backup_folder:
        folder = Path(backup_folder)
    else:
        fulls = sorted(p for p in BACKUP_DIR.glob("full_*") if p.is_dir())
        if not fulls:
            raise RuntimeError("No hay backups completos: ejecuta backup_all primero.")
        folder = fulls[-1]
    cat = C.build_from_backup(folder, global_channel=client().channel + 1)
    gm = 0
    if fetch_gm_names:
        try:
            gm = _fetch_gm_names(cat)
        except Exception:  # noqa: BLE001 - sin teclado el catálogo vale igual
            pass
    _save_catalog(cat)
    kb = sum(1 for c in cat["combis"].values() for t in c["timbres"] if t["keyboard"])
    return {"source": str(folder), "programs": len(cat["programs"]), "combis": len(cat["combis"]),
            "keyboard_timbres": kb, "gm_names_read": gm, "file": str(CATALOG), "markdown": str(DATA_DIR / "catalog.md")}


@mcp.tool()
def refresh_catalog() -> dict:
    """Pone al día el catálogo: compara los digests del Kronos y solo vuelve a leer los bancos de
    Programs/Combis que han cambiado desde que se creó."""
    from . import catalog as C

    cat = _catalog()
    dev = _digests()
    changed = [k for k, sha in dev.items() if k[:2] in ("00", "01") and k in cat["bank_sha1"] and cat["bank_sha1"][k] != sha]
    for k in changed:
        obj, b = ObjType(int(k[:2], 16)), int(k[3:], 16)
        for i, data, _raw in _bank_raw(obj, b):
            C.add_object(cat, int(obj), b, i, data, client().channel + 1)
        cat["bank_sha1"][k] = dev[k]
    gm = _fetch_gm_names(cat) if changed else 0
    _save_catalog(cat)
    return {"banks_reread": [f"{'PROGRAM' if k[:2] == '00' else 'COMBI'} {_label(int(k[:2], 16), int(k[3:], 16))}" for k in changed],
            "gm_names_read": gm, "up_to_date": not changed}


def _perf_key(text: str) -> str:
    """'int-a 5' / 'INT-A 005' / 'USER-AA 12' → 'INT-A 005'."""
    parts = text.upper().replace(",", " ").split()
    if len(parts) != 2:
        raise ValueError(f"Formato: 'INT-A 102' (recibido {text!r})")
    return f"{parts[0]} {int(parts[1]):03d}"


@mcp.tool()
def find_sound(query: str, kind: str = "all", limit: int = 40) -> dict:
    """Busca por nombre en el catálogo. kind: 'all', 'program' o 'combi'. Para Combis también
    encuentra las que contienen un Program cuyo nombre coincide."""
    cat = _catalog()
    q = query.lower()
    out: dict = {"combis": [], "programs": [], "combis_containing": []}
    if kind in ("all", "combi"):
        for k, c in cat["combis"].items():
            if q in c["name"].lower():
                out["combis"].append({"combi": k, "name": c["name"],
                                      "programs": [f"{t['program']} {t.get('program_name') or ''}".strip() for t in c["timbres"] if t["keyboard"]]})
        for k, c in cat["combis"].items():
            hits = [f"T{t['timbre']} {t['program']} {t['program_name']}" for t in c["timbres"] if t["keyboard"] and q in (t.get("program_name") or "").lower()]
            if hits and q not in c["name"].lower():
                out["combis_containing"].append({"combi": k, "name": c["name"], "matching_timbres": hits})
    if kind in ("all", "program"):
        for k, p in cat["programs"].items():
            if q in p["name"].lower():
                out["programs"].append({"program": k, "name": p["name"], "engine": p["engine"], "used_in_combis": p["used_in_combis"][:20],
                                        "used_count": len(p["used_in_combis"])})
    for key in out:
        out[key] = out[key][:limit]
    return out


@mcp.tool()
def combi_info(combi: str, all_timbres: bool = False) -> dict:
    """Programs que contiene una Combi ('INT-A 102'): timbre, Program y nombre, volumen, zona de teclado,
    transposición. Por defecto solo los timbres que suenan desde el teclado (INT en canal global)."""
    cat = _catalog()
    k = _perf_key(combi)
    c = cat["combis"].get(k)
    if c is None:
        raise KeyError(f"No existe la Combi {k}")
    ts = c["timbres"] if all_timbres else [t for t in c["timbres"] if t["keyboard"]]
    keep = ("timbre", "status", "program", "program_name", "midi_channel", "volume", "transpose", "key_zone", "keyboard")
    return {"combi": k, "name": c["name"], "timbres": [{x: t[x] for x in keep if x in t} for t in ts],
            "hidden_timbres": len(c["timbres"]) - len(ts)}


@mcp.tool()
def program_usage(program: str) -> dict:
    """En qué Combis se usa un Program ('USER-B 007'), desde el teclado."""
    cat = _catalog()
    k = _perf_key(program)
    p = cat["programs"].get(k)
    if p is None:
        raise KeyError(f"No existe el Program {k}")
    return {"program": k, "name": p["name"], "engine": p["engine"],
            "combis": [{"combi": ck, "name": cat["combis"][ck]["name"]} for ck in p["used_in_combis"]]}


PID_DIR = Path(os.getenv("KRONOS_LOG_DIR", PROJECT_DIR / "logs")) / "pids"


def _shutdown(code: int = 0) -> None:
    try:
        if _client is not None:
            _client.release()
    except Exception:  # noqa: BLE001
        pass
    try:
        (PID_DIR / f"{os.getpid()}.json").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    os._exit(code)  # los hilos de rtmidi no deben mantener vivo el proceso


def main() -> None:
    from . import lifecycle

    try:
        lifecycle.clean_orphans(PID_DIR)
        lifecycle.register(PID_DIR)
        lifecycle.watch_parent(_shutdown)
    except Exception:  # noqa: BLE001 - nunca impedir el arranque por esto
        pass
    try:
        mcp.run()
    finally:
        _shutdown(0)


if __name__ == "__main__":
    main()

# Proyecto kronos-mcp

MCP server (Python, `src/kronos_mcp`) para mantener un Korg Kronos por MIDI SysEx y descubrir su mapa de parámetros en caliente.

## Contexto que leer primero
- `docs/korg_oficial/KRONOS_MIDI_SysEx.txt`: especificación oficial (fuente de verdad). Mapas en `data/params/` (regenerar con `python tools/build_param_maps.py`).
- `docs/sysex/kronos_sysex_referencia.md`: protocolo (funciones, tipos de objeto, bancos, codificación 7-bit).
- `docs/ingenieria_inversa.md`: lo confirmado con el preload del Kronos Editor (versiones, tamaños, formatos 72/73/75).
- `data/knowledge/params.json` y `notes.jsonl`: lo aprendido en sesiones anteriores (tool `knowledge`).

## Reglas de trabajo con el teclado
- Leer es libre: `probe` (peticiones seguras), `snapshot`, `read_object`, `list_bank`, `monitor`.
- Escribir exige `KRONOS_ALLOW_WRITE=1` y `confirm=true`. Antes: backup (`backup_bank`) y confirmación explícita del usuario.
- Para entender un sonido: `voice_summary` (una llamada); detalle con `decode_object` y `query` como lista. Oírlo: `play_notes`. Para auditar el teclado: `system_status` + `bank_status`.
- Cada hallazgo se registra en el momento: `learn_param` (parámetros) y `note` (protocolo). Al cerrar sesión, `export_knowledge`.
- Un cambio por experimento. Si el diff tiene muchos tramos, repetir aislando mejor.

## Desarrollo
- `pytest` usa el Kronos simulado (`transport/mock.py`); `KRONOS_MOCK=1` para arrancar el server sin teclado.
- `mcp<2` fijado (API FastMCP).

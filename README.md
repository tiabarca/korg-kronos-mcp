# kronos-mcp

MCP server para conectar un **Korg Kronos** por MIDI SysEx y hacer tareas de mantenimiento desde un LLM (Claude Desktop, Claude Code…).

## Estructura

```
kronos/
├── src/kronos_mcp/
│   ├── server.py          # MCP server (FastMCP, stdio): tools, prompt y resources
│   ├── client.py          # Petición/respuesta SysEx, timeouts
│   ├── monitor.py         # Registro y decodificación de todo el tráfico MIDI
│   ├── knowledge.py       # Snapshots, diff y mapa de parámetros aprendido
│   ├── protocol/
│   │   ├── constants.py   # Funciones, tipos de objeto, modos, códigos de error
│   │   ├── codec.py       # Codificación 7→8 bits de Korg
│   │   ├── messages.py    # Construcción/parseo de mensajes
│   │   └── setlist.py     # Parseo de la estructura Set List
│   └── transport/
│       ├── midi.py        # MIDI real (mido + python-rtmidi)
│       └── mock.py        # Kronos simulado para desarrollar sin teclado
├── tests/                 # pytest (usa el mock)
├── data/
│   ├── knowledge/              # Lo aprendido en caliente (params.json, notes.jsonl, snapshots/)
│   ├── preload_index.json      # Preload de fábrica: nombres + md5 (de ingeniería inversa)
│   └── editor_stringlists.json # Listas de valores del Kronos Editor
├── docs/
│   ├── ingenieria_inversa.md   # Hallazgos del SO 3.1.3 y del Kronos Editor
│   ├── extra/             # SO y editor originales (no versionar)
│   ├── sysex/             # Referencia propia del protocolo
│   └── korg_oficial/      # ← aquí la doc SysEx oficial de Korg (.txt del zip 2.1)
└── backups/               # Dumps .bin que guarda read_current_object
```

## Instalación (Windows)

```powershell
cd C:\dev\kronos
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
pytest                      # tests contra el Kronos simulado
```

Necesitas el **KORG USB-MIDI Driver** instalado y el Kronos conectado por USB (o una interfaz MIDI).

## Configuración del Kronos

- **Global P1 → MIDI Filter → Exclusive**: activado.
- **Canal MIDI global**: debe coincidir con `KRONOS_CHANNEL` (1 por defecto).

## Uso con Claude Desktop

Copia el bloque de `claude_desktop_config.example.json` en `%APPDATA%\Claude\claude_desktop_config.json`.

| Variable | Uso |
|---|---|
| `KRONOS_MIDI_IN` / `KRONOS_MIDI_OUT` | Nombre exacto de los puertos (por defecto autodetecta "kronos") |
| `KRONOS_CHANNEL` | Canal MIDI global 1..16 |
| `KRONOS_ALLOW_WRITE` | `1` para habilitar las tools que escriben en el Kronos |
| `KRONOS_MOCK` | `1` para usar el Kronos simulado |
| `KRONOS_BACKUP_DIR` | Carpeta de backups |

Probar sin Claude: `npx @modelcontextprotocol/inspector .venv\Scripts\python -m kronos_mcp`

## Tools

| Tool | Escribe | Descripción |
|---|---|---|
| `list_midi_ports` | – | Puertos MIDI disponibles |
| `identify` | – | Universal Identity Request |
| `get_mode` / `set_mode` | – | Modo actual / cambio de modo |
| `read_current_object` | – | Dump del buffer de edición + backup `.bin` |
| `get_set_list` | – | Set List estructurado (slots, bancos, transposición…) |
| `rename_current_slot`, `set_current_slot_comments` | ✔ | Editan el buffer de edición |
| `store_set_list` | ✔ | Persiste en memoria no volátil |
| `send_raw_sysex` | ✔ | SysEx arbitrario para explorar funciones nuevas |
| `read_object` | – | Lee un objeto almacenado por banco/índice (72→73), opcionalmente a `.syx` |
| `list_bank` | – | Nombres de un banco completo (y motor EXi/HD-1) |
| `backup_bank` | – | Banco completo a `.syx` restaurable |
| `compare_with_factory` | – | Qué posiciones siguen de fábrica y cuáles se han modificado |
| `search_factory` | – | Busca en el preload de fábrica sin teclado |
| `restore_syx` | ✔ | Restaura un `.syx` de Object Dumps |
| `sysex_log` | – | Últimos mensajes enviados/recibidos en hex |

Las tools que escriben exigen `KRONOS_ALLOW_WRITE=1` **y** `confirm=true` en la llamada.

## Mapas oficiales de parámetros

La documentación oficial de Korg (`docs/korg_oficial/`) se convierte con `python tools/build_param_maps.py` en `data/params/*.json`:
- unos 60 000 campos con offset, bits, rango y Parameter ID;
- 197 tipos de efecto;
- 8 motores EXi.

Validado contra el preload de fábrica (ver `docs/sysex/kronos_sysex_referencia.md` §0).

| Tool | Escribe | Uso |
|---|---|---|
| `system_status` | – | Identidad, modo, reloj MIDI, **protección de memoria**, performance activa, tipos de banco |
| `system_info` | – | Inventario: pianos SGX, bancos de multisamples, tipos de banco EXi/HD-1 |
| `bank_status` | – | Digests SHA-1 de todos los bancos (1 mensaje) comparados con fábrica |
| `decode_object` | – | Program/Combi/Global… con nombres reales, efecto de cada slot y motor EXi |
| `find_parameter` | – | Busca un parámetro por nombre → offset y Parameter ID |
| `set_parameter` | buffer | Cambia un parámetro en el buffer de edición (0x43) y verifica el cambio. Requiere `confirm=true`, no guarda |

Además:
- `learn_finish` y `diff_snapshots` devuelven `params_changed`, con el nombre oficial de cada parámetro modificado.
- El monitor traduce a nombres los Parameter Change que emite el Kronos.
- `backup_bank` usa Dump Bank (77) y `restore_syx` envía Store Bank (76) al final, como exige la doc.

## Exploración en caliente

El server registra todo el tráfico MIDI y va construyendo el mapa de parámetros en `data/knowledge/` mientras trabajas con el teclado.

| Tool | Uso |
|---|---|
| `connection_status` | Puertos, canal, permisos, monitor y lo aprendido |
| `monitor` / `monitor_mark` | Tráfico decodificado por nº de secuencia; `only_unsolicited` = lo que el Kronos envía al tocar controles |
| `probe` | Peticiones de solo lectura (12, 30, 32, 34, 37, 39, 60, 72, 74, 79) sin permisos de escritura |
| `snapshot` / `list_snapshots` / `diff_snapshots` | Fotos de objetos y diff byte a byte (u8/s8/16/32, bits cambiados) |
| `learn_start` → (cambias un parámetro en el Kronos) → `learn_finish` | Diff automático + mensajes emitidos por el Kronos durante el cambio |
| `learn_param` / `note` | Guardan el hallazgo (offset, tamaño, bits, valores, evidencia) |
| `knowledge` / `decode_current` | Consultan lo aprendido y lo aplican al buffer de edición |
| `export_knowledge` | Genera `docs/mapa_parametros.md` |

Prompt incluido: **descubrir_parametro** (guía paso a paso).

Logs completos del monitor en `logs/*.jsonl` (no versionados).

### Antes de empezar
1. Copia de seguridad completa del Kronos (Disk → Save All a USB y/o copia del SSD).
2. Global P1 → MIDI Filter → Exclusive activado (para que el Kronos también *emita* los cambios de parámetros).
3. Arranca con `KRONOS_ALLOW_WRITE=0`: la exploración no necesita escribir.

### Claude Code
Copia `mcp.example.json` como `.mcp.json` para que Claude Code registre el server al abrir `C:\dev\kronos` (tras crear el `.venv`). `CLAUDE.md` le da el contexto y las reglas.

## Pendiente

- Confirmar con hardware real el formato de Reply y los códigos de error 0x40–0x42.
- Backup por FTP (adaptador USB-Ethernet, ver `docs/ingenieria_inversa.md`).
- Validar con el teclado: digests de fábrica (`bank_status` en un Kronos restaurado), `set_parameter` y emisión de 0x43 al tocar controles.
- Parámetros binarios (0x44/0x54/0x56: UUID de bancos de samples).
- Song Event / Drum Track Pattern Event (formatos especiales).

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
│   ├── catalog.py         # Catálogo de Programs/Combis y uso de cada Program
│   ├── design.py          # Diseño de sonidos con nombres oficiales de parámetros
│   ├── lifecycle.py       # Control de procesos (PIDs, instancias huérfanas)
│   ├── protocol/
│   │   ├── constants.py   # Funciones, tipos de objeto, modos, códigos de error
│   │   ├── codec.py       # Codificación 7→8 bits de Korg
│   │   ├── messages.py    # Construcción/parseo de mensajes
│   │   └── setlist.py     # Parseo de la estructura Set List
│   └── transport/
│       ├── midi.py        # MIDI real (mido + python-rtmidi)
│       └── mock.py        # Kronos simulado para desarrollar sin teclado
├── scripts/               # Scripts en caliente para run_script (def run(ctx, **args))
├── tools/                 # build_param_maps.py, run_checks, matar_servers.bat
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

Requisitos: Python 3.11 o superior, el **KORG USB-MIDI Driver** y el Kronos conectado por USB (o por una interfaz MIDI).

```powershell
git clone <url-del-repo> C:\dev\kronos
cd C:\dev\kronos
python -m venv .venv
.venv\Scriptsctivate
pip install -e .[dev]       # instala también el comando kronos-mcp en .venv\Scripts
pytest                      # tests contra el Kronos simulado (test_live.py necesita teclado y se omite)
```

Comprobaciones:

```powershell
$env:KRONOS_MOCK = "1"; kronos-mcp      # arranca el server sin teclado (Ctrl+C para salir)
toolsun_checks.bat                    # con el Kronos: pruebas de solo lectura -> logs\checks_*.json
```

Los datos personales (`backups/`, `*.syx`, `data/catalog.*`, `data/knowledge/params.json`, `logs/`) no se versionan.
Los mapas de `data/params/` ya vienen en el repo; para regenerarlos: `python tools/build_param_maps.py`.

## Configuración del Kronos

- **Global P1 → MIDI Filter → Exclusive**: activado.
- **Canal MIDI global**: debe coincidir con `KRONOS_CHANNEL` (1 por defecto).

## Registrar el server en el cliente MCP

**Claude Code:** copia `mcp.example.json` como `.mcp.json` en la raíz (ajusta la ruta si no usas `C:\dev\kronos`) y abre la carpeta. `CLAUDE.md` le da el contexto y las reglas.

**Claude Desktop:** copia el bloque de `claude_desktop_config.example.json` en `%APPDATA%\Claude\claude_desktop_config.json` y reinicia la app.

Tras actualizar el código (`git pull`) vuelve a ejecutar `pip install -e .[dev]` y reinicia el cliente para que cargue el server nuevo.

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

## Catálogo, diseño y automatización

| Tool | Escribe | Uso |
|---|---|---|
| `backup_all` | – | Backup completo de todos los bancos a una carpeta |
| `set_baseline` / `changes_since_baseline` | – | Referencia de digests y qué bancos han cambiado desde entonces |
| `build_catalog` / `refresh_catalog` | – | Catálogo de Programs y Combis desde un backup; refresco leyendo solo bancos cambiados |
| `find_sound` / `program_usage` | – | Busca sonidos por nombre y qué Combis usan un Program |
| `design_program` / `design_combi` | ✔ | Aplica cambios con nombre (`param`, `copy_effect`, `copy_range`, `name`) y guarda el diseño en `data/designs/` |
| `get_set_list` / `show_set_list` / `edit_set_list` | ✔ (edit) | Lee y edita la Set List |
| `run_script` | según script | Ejecuta un script de `scripts/` con acceso al cliente (`confirm=true` si escribe) |
| `server_instances` / `reconnect_midi` | – | Instancias del server y reapertura de puertos |

## Pendiente

- Confirmar con hardware real el formato de Reply y los códigos de error 0x40–0x42.
- Backup por FTP (adaptador USB-Ethernet, ver `docs/ingenieria_inversa.md`).
- Validar con el teclado: digests de fábrica (`bank_status` en un Kronos restaurado), `set_parameter` y emisión de 0x43 al tocar controles.
- Parámetros binarios (0x44/0x54/0x56: UUID de bancos de samples).
- Song Event / Drum Track Pattern Event (formatos especiales).

## Procesos y puertos MIDI (robustez en sesiones largas)

- **Puertos bajo demanda:** el server abre los puertos MIDI al hacer una operación y los cierra tras
  `KRONOS_IDLE_RELEASE` segundos sin uso (15 por defecto). Así no se retiene la entrada MIDI (en Windows
  solo la puede usar un proceso) y un desenchufe del USB o una re-enumeración del Kronos no deja una
  conexión muerta: la siguiente operación abre puertos nuevos.
- **Reintento automático:** si el Kronos no responde, se reabren los puertos y se repite una vez.
  `reconnect_midi` lo fuerza a mano.
- **Sin procesos colgados:** el server termina con `os._exit` al cerrarse el cliente (los hilos de rtmidi
  ya no lo mantienen vivo), vigila a su proceso padre y, al arrancar, termina instancias huérfanas
  (registradas en `logs/pids/`). `server_instances` muestra las instancias.
- **Limpieza manual:** `tools\matar_servers.bat` termina todos los procesos kronos-mcp (con la app cerrada).
- En `learn_start` los puertos se mantienen abiertos (máx. 10 min) para recibir lo que envía el Kronos.

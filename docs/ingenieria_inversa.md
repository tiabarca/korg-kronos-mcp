# Ingeniería inversa — hallazgos (2026-10-11)

Material analizado (en `docs\extra`):
- `KRONOS_Update_3_1_3` — actualización del sistema operativo 3.1.3.
- `Kronos31Setup.exe` — instalador del KRONOS Editor / Plug-In Editor 3.1 (Inno Setup 5.5).

Objetivo: interoperabilidad con un Kronos propio. **No se ha intentado descifrar el firmware** (ver §1).

---

## 1. Actualización del SO 3.1.3

| Elemento | Hallazgo |
|---|---|
| Plataforma | Linux 2.6.32.11-korg con RTAI (tiempo real), x86 de 32 bits |
| `install.info` | VERSION=3.1.3, tar.gz con scripts `pretar.sh` / `posttar.sh` y verificación md5 de cada fichero (`*.csum`) |
| Aplicación principal | `mnt/korg/ro/Eva.img` (129 MB) y `Mod.img` (17 MB), **cifrados** (entropía ≈ 7,9998 bits/byte). Los carga `sbin/loadmod.ko` / `loadoa` al arrancar; si la autenticación falla se muestra `ShowReauthScreen`. |
| Módulos en claro | `USBMidiAccessory.ko` (MIDI USB), `OmapNKS4Module.ko` (panel frontal/barra de progreso), `STGGmp.ko`, drivers USB-Ethernet (`smsc7500.ko`, etc.) |

**Decisión:** el motor de sonido y la implementación SysEx están dentro de las imágenes cifradas. Saltarse ese cifrado queda fuera del alcance del proyecto. El protocolo se ha obtenido del editor oficial (§2), que es más útil para este fin.

### Servidor FTP integrado (útil para mantenimiento)

`etc/OA.rc` arranca `vsftpd` cuando hay un adaptador USB-Ethernet. La función es oficial: existe desde el SO 2.0.2 y está pensada para transferir ficheros.

```
anonymous_enable=YES      anon_root=/korg/ftp/   (→ SSD1, SSD2)
write_enable=YES          anon_upload_enable=YES
anon_mkdir_write_enable=YES  anon_other_write_enable=YES
local_enable=YES          banner "Welcome to the Korg Kronos FTP service."
```

- También se anuncia por **avahi/mDNS**.
- `posttar.sh` crea `/korg/rw/Startup/KronosNet.conf` con dos líneas: `kronos` / `kronos`. Probablemente son el nombre de red y la contraseña por defecto (⚠️ sin confirmar).
- **Implicaciones:**
  - Oportunidad: copia de seguridad completa de los `.PCG`, `.SNG` y muestras por FTP, mucho más rápida que por SysEx. Se puede añadir como tool del MCP.
  - Riesgo: un FTP anónimo con escritura sobre los SSD queda expuesto en cualquier red a la que se conecte el teclado. Conviene usarlo solo en una red aislada o con un cable directo.

---

## 2. KRONOS Editor 3.1 (Sound Quest UniQuest / MidiQuest)

Extraído con `innoextract`. El editor es un "driver" de MidiQuest:
- `app/Instruments/Korg OASYS/*.sqt`: plantillas de pantalla en un formato propio comprimido. No se han decodificado.
- `*.ini`: 599 listas de valores (enumeraciones de parámetros) → exportadas a `data/editor_stringlists.json`.
- `Win32/OASYS*.dll`: controles gráficos personalizados. Contienen los textos de los códigos de error Reply.
- **`userdocs/.../KRONOS_Preload.SQS` y `NewKRONOS_Preload.SQS`**: el preload completo de fábrica guardado como **SysEx real**, con 10 385 mensajes por fichero. Los ficheros de banco (`Combi.IntA`, `Patch.v2.BkB`, `Drums.v2.UserG`…) contienen además las **secuencias de mensajes que usa el editor** para cada operación.

### 2.1 Formatos confirmados ✅

```
Object Dump Request   F0 42 3g 68 72 tt bb iH iL F7
Object Dump           F0 42 3g 68 73 tt bb iH iL vv [datos 7-bit] F7
Current Obj Dump Req  F0 42 3g 68 74 tt F7
Current Object Dump   F0 42 3g 68 75 tt vv [datos 7-bit] F7
Store Bank Request    F0 42 3g 68 76 tt bb F7
Change Prog Bank Type F0 42 3g 68 7C bb kk F7      (kk = tipo de banco HD-1/EXi)
Mode Change           F0 42 3g 68 4E mm F7
Song Select           F0 42 3g 68 13 00 00 00 F7
Set Current Object    F0 42 3g 68 71 00 00 00 F7   (plantilla; campos sin identificar)
Current Perf Id Req   F0 42 3g 68 32 02 F7
Parameter Change int  F0 42 3g 68 43 0F 00 00 34 00 00 00 01 F7   (visto en modo Sequencer; estructura sin descifrar)
```

- `tt` = tipo de objeto · `bb` = banco · `iH iL` = índice de 14 bits · `vv` = versión del formato.
- **Corrección respecto a kronut:** en la `0x75` los datos empiezan tras `tt vv` (2 bytes). kronut escribe los Set Lists como `75 0D bank idH idL ver datos`, cosa que no cuadra con lo que usa el editor oficial (`75 0D 00 datos`). El MCP usa el formato del editor.
- El editor guarda las peticiones `72` con el **último índice usado** en un bucle (p. ej. `72 04 00 00 27` → el kit 39 es el último de los 40 kits INT). Eso confirma que se pide **un objeto por mensaje**.

### 2.2 Versiones y tamaños por tipo de objeto ✅

| Tipo | Objeto | Versión | Tamaño interno (bytes) |
|---|---|---|---|
| 00 | Program | 5 | **4960 = EXi**, **3706 = HD-1** |
| 01 | Combination | 3 | 7810 |
| 02 | Song Timbre Set | 3 | 7810 |
| 03 | Global | 2 | 24708 |
| 04 | Drum Kit | 3 | 38424 |
| 05 | Wave Sequence | 1 | 2216 |
| 06 | KARMA GE | 0 | 2544 |
| 08 | Song Control | 0 | 5264 |
| 0D | Set List | 0 | 69416 |
| 0E | Drum Track Pattern | 0 | 32–34 |

Programs, Combis, Drum Kits, Wave Seqs, KARMA GE y Set Lists llevan el **nombre en los primeros 24 bytes** (latin-1, relleno con espacios o NUL).

### 2.3 Numeración de bancos ✅

| Objeto | INT | USER |
|---|---|---|
| Program | INT-A…F = `00–05` | USER-A…G = `40–46`, USER-AA…GG = `47–4D` |
| Combination | INT-A…G = `00–06` | USER-A…G = `40–46` |
| Drum Kit | INT = `00` (40 kits) | `40–4D` (16 kits por banco) |
| Wave Sequence | INT = `00` (150) | `40–4D` (32 por banco) |
| KARMA GE | `00–0B` (128 por banco) | — |
| Set List | banco `00`, índice 0–127 | — |
| Song (Timbre Set / Control) | banco `00`, índice 0–199 | — |
| Drum Track Pattern | banco `00`, índice 0–999 | — |

En el preload, los bancos de Program de tipo EXi son INT-A, USER-B…F y USER-BB…DD; el resto son HD-1.

---

## 3. Productos derivados

- `data/preload_index.json`: 7 278 objetos de fábrica por modelo (KRONOS y NewKRONOS), con tipo, banco, índice, versión, tamaño, nombre, motor y **md5 del objeto decodificado**. Sirve para:
  - comparar el contenido del teclado con el de fábrica (tool `compare_with_factory`);
  - buscar sonidos de fábrica por nombre sin el teclado conectado (`search_factory`).
- `data/editor_stringlists.json`: listas de valores del editor, para traducir valores numéricos a nombres cuando se descifren los mapas de parámetros.

## 4. Pendiente

- Mapa de parámetros dentro de cada objeto (offsets dentro de los 4960/3706/7810/24708 bytes). Lo más directo es la doc oficial (`KRONOS_SysEx_2_1.zip`). Sin ella, la vía es decodificar el formato `.sqt`.
- Estructura de `0x43` (Parameter Change) y `0x71` (Set Current Object).
- Confirmar con el teclado: el formato de la respuesta Reply y los códigos 0x40–0x42.
- Tool de backup por FTP.

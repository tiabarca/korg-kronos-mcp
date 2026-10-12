# Korg Kronos — Referencia técnica del protocolo SysEx

Documento de contexto para el proyecto **korg kronos mcp server**. Recopilado el 2026-10-11.

> **Fiabilidad.** Lo marcado ✅ está verificado en código que funciona contra un Kronos real (kronut) o en mensajes probados por usuarios. Lo marcado ⚠️ es inferido o pendiente de confirmar con la documentación oficial de Korg (ver §9).

---

## 1. Cabecera de todos los mensajes ✅

```
F0 42 3g 68 ff [datos...] F7
│  │  │  │  └─ código de función (§3)
│  │  │  └──── ID de modelo Kronos = 0x68
│  │  └─────── 0x30 + canal MIDI global (g = 0..F → canal 1..16)
│  └────────── ID de fabricante Korg = 0x42
└───────────── inicio SysEx
```

- El nibble `g` es el **canal MIDI global** del Kronos (Global P1). Si no coincide, el Kronos ignora el mensaje en silencio. Con canal global 1 → `0x30`; canal 2 → `0x31`, etc.
- El filtro **Global P1 → MIDI Filter → Exclusive** debe estar activado para que el Kronos transmita/reciba SysEx.
- Además de los mensajes Korg, el Kronos acepta Universal SysEx: Inquiry (identidad), GM System On, Master Volume, Master Balance, Master Fine Tune y Master Coarse Tune.

### Identificación del dispositivo (Universal) ✅ capturado de un Kronos real (2026-10-12)
- Petición: `F0 7E 7F 06 01 F7`
- Respuesta real: `F0 7E 00 06 02 42 68 00 0E 00 03 01 03 00 F7`
  - `00` = canal global 1 · `42` = Korg
  - familia `68 00` → 0x0068 (Kronos), en orden LSB-MSB
  - miembro `0E 00` → 0x000E
  - versión `03 01 03 00` → **SO 3.1.3**

---

## 2. Patrón petición/respuesta ✅

- Las peticiones tienen pareja de respuesta (p. ej. `74` → `75`, `12` → `42`).
- Las órdenes de escritura/cambio responden con **Reply `0x24`**: `F0 42 3g 68 24 cc … F7`, donde `cc` es el código de error (§6). `cc = 00` = OK.
- Implementación de referencia: enviar, escuchar sólo SysEx cuya función sea la esperada **o** `0x24`, y abortar con timeout (~5 s).

---

## 3. Códigos de función ✅

R = el Kronos lo recibe · T = lo transmite

| Func | Dir | Nombre |
|---|---|---|
| `12` | R | Mode Request |
| `42` | T | Mode Data (respuesta a 12; modo en `datos[0] & 0x0F`) |
| `4E` | R/T | Mode Change |
| `13` | R/T | Song Select |
| `24` | T | Reply (resultado/código de error) |
| `30` | R | Current Sample Information Request |
| `31` | T | Current Sample Information |
| `32` | R | Current Performance Id Request |
| `33` | T | Current Performance Id |
| `34` | R | Current Piano Types Request |
| `35` | T | Current Piano Types |
| `36` | T | Notify Piano Types Changed |
| `37` | R | Bank Digest Request |
| `38` | T | Bank Digest |
| `39` | R | Bank Digest Collection Request |
| `3A` | T | Bank Digest Collection |
| `41` | R/T | Sequencer Parameter Change |
| `43` | R/T | Parameter Change (entero) |
| `44` | R/T | Parameter Change (binario) |
| `53` | R/T | Drum Kit Parameter Change (entero) |
| `54` | R/T | Drum Kit Parameter Change (binario) |
| `55` | R/T | Wave Seq Parameter Change (entero) |
| `56` | R/T | Wave Seq Parameter Change (binario) |
| `60` | R | Program Bank Types Request |
| `61` | T | Program Bank Types |
| `6D` | R/T | KARMA Parameter Change |
| `6E` | R/T | Drum Track Parameter Change |
| `71` | R/T | Set Current Object |
| `72` | R | Object Dump Request |
| `73` | R/T | Object Dump |
| `74` | R | Current Object Dump Request (buffer de edición) |
| `75` | R/T | Current Object Dump |
| `76` | R/T | Store Bank Request (guardar en memoria no volátil) |
| `77` | R | Dump Bank Request |
| `78` | R/T | Reset Controller |
| `79` | R | SMF Data Dump Request |
| `7A` | T | SMF Data Dump |
| `7B` | ? | Preset Pattern Dump (citado en el error 0x40) ⚠️ |
| `7C` | R | Change Program Bank Type |
| `7D` | R | Query Program Bank Type (obsoleto) |
| `7E` | T | Query Program Bank Type Reply (obsoleto) |
| `7F` | R/T | KARMA Control |

El formato de bytes de las funciones `43/44/41/53–56/6D/6E` (IDs de parámetro, índices) **no está aquí**: está en los ficheros oficiales (§9).

---

## 4. Tipos de objeto (para 72/73/74/75/76/77) ✅

Entre paréntesis, el fichero de definición de la doc oficial de Korg.

| Tipo | Objeto |
|---|---|
| `00` | Program (Prog_EXi_Common.txt, Prog_EXi.txt, Prog_HD-1.txt) |
| `01` | Combination (CombiAndSongTimbreSet.txt) |
| `02` | Song Timbre Set (CombiAndSongTimbreSet.txt) |
| `03` | Global (Global.txt) |
| `04` | Drum Kit (DrumKit.txt) |
| `05` | Wave Sequence (WaveSequence.txt) |
| `06` | KARMA GE (sin buffer de edición) |
| `07` | KARMA Template |
| `08` | Song Control (SongControl.txt) |
| `09` | Song Event (deshabilitado) |
| `0A` | Song Region |
| `0B` | Reservado |
| `0C` | KARMA GE RTP Info (KARMA_GE_RTP.txt) |
| `0D` | Set List (index = nº set list) (SetList.txt) |
| `0E` | Drum Track Pattern (DrumTrackPattern.txt) |
| `0F` | Drum Track Pattern Event (DrumTrackPatternEvent.txt) |
| `10` | Set List Slot Comments (bank = set list, index = slot) |
| `11` | Set List Slot Name (bank = set list, index = slot) |
| `12` | Combi Name |
| `13` | Program Name |
| `14` | Song Name |
| `15` | Wave Seq Name |
| `16` | Drum Kit Name |
| `17` | Set List Name (index = set list) |
| `18` | Song (Timbre Set + Song Control) (Song.txt) |

---

## 5. Modos (func 4E / 42) ✅

| Valor | Modo |
|---|---|
| `0` | Combination |
| `2` | Program |
| `4` | Sequencer |
| `6` | Sampling |
| `7` | Global |
| `8` | Disk |
| `9` | Set List |

Ejemplos probados: `F0 42 30 68 4E 00 F7` → Combi · `F0 42 30 68 4E 02 F7` → Program.

---

## 6. Códigos de error de Reply (`0x24`) ✅

| Código | Significado |
|---|---|
| `00` | Sin error |
| `01` | Tipo de parámetro incorrecto para el modo actual |
| `02` | Tipo de mensaje, ID de parámetro o índice desconocido |
| `03` | Mensaje corto o malformado |
| `04` | Objeto destino no encontrado |
| `05` | Recursos insuficientes |
| `06` | Valor fuera de rango |
| `07` | Error interno |
| `40` | Otro error: banco de programas de tipo incorrecto para el dump recibido (func 73/75); datos inválidos en Preset Pattern Dump (func 7B) |
| `41` | Objeto destino protegido |
| `42` | Desbordamiento de memoria |

> Nota: kronut compara estos tres últimos con `0x64/0x65/0x66` (probable confusión decimal/hex). Lo coherente con la doc es `0x40/0x41/0x42`; confirmar con una respuesta real.

---

## 7. Codificación de datos 7→8 bits ✅

Los datos de objeto internos (8 bits) se transmiten en grupos de **7 bytes internos → 8 bytes MIDI**: el primer byte de cada grupo lleva los bits altos (bit *i* = MSB del byte *i*), seguido de los 7 bytes con el MSB a cero.

- `midi_len = internal_len + ceil(internal_len / 7)` → `internal_len + (internal_len + 6) // 7`
- `internal_len = (midi_len // 8) * 7 + (midi_len % 8 - 1 if midi_len % 8 else 0)`

```python
def decode_7bit(midi: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(midi), 8):
        chunk = midi[i:i+8]
        hi = chunk[0]
        for j, b in enumerate(chunk[1:]):
            out.append(b | (0x80 if hi & (1 << j) else 0))
    return bytes(out)

def encode_7bit(data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 7):
        chunk = data[i:i+7]
        hi = sum(1 << j for j, b in enumerate(chunk) if b & 0x80)
        out.append(hi)
        out.extend(b & 0x7F for b in chunk)
    return bytes(out)
```

---

## 8. Formatos de mensaje confirmados ✅

> Actualizado tras analizar el preload SysEx del KRONOS Editor 3.1 (ver `docs/ingenieria_inversa.md`).

| Acción | Bytes |
|---|---|
| Pedir modo | `F0 42 3g 68 12 F7` → resp. `F0 42 3g 68 42 mm … F7` |
| Cambiar modo | `F0 42 3g 68 4E mm F7` → Reply `24` |
| Leer objeto almacenado | `F0 42 3g 68 72 tt bb iH iL F7` → `F0 42 3g 68 73 tt bb iH iL vv [datos 7bit] F7` |
| Escribir objeto almacenado | `F0 42 3g 68 73 tt bb iH iL vv [datos 7bit] F7` → Reply `24` |
| Leer objeto actual (buffer edición) | `F0 42 3g 68 74 tt F7` → `F0 42 3g 68 75 tt vv [datos 7bit] F7` |
| Escribir objeto actual | `F0 42 3g 68 75 tt vv [datos 7bit] F7` → Reply `24` |
| Guardar banco en memoria | `F0 42 3g 68 76 tt bb F7` → Reply `24` |
| Cambiar tipo de banco de Programs | `F0 42 3g 68 7C bb kk F7` |
| Ir a Set List *n* | Modo 9 + `Bn 00 00`, `Bn 20 nn`, `Cn 00` (canal MIDI normal) |

- `vv` = versión del formato: Program 5, Combi 3, Song Timbre Set 3, Global 2, Drum Kit 3, Wave Seq 1, KARMA GE 0, Song Control 0, Set List 0, Drum Track Pattern 0.
- Tamaños internos: Program 4960 (EXi) / 3706 (HD-1), Combi 7810, Global 24708, Drum Kit 38424, Wave Seq 2216, KARMA GE 2544, Song Control 5264, Set List 69416.
- Bancos: INT-A…G = `00–06`, USER-A…G = `40–46`, USER-AA…GG = `47–4D` (tabla completa en `ingenieria_inversa.md`).
- Nombre: primeros 24 bytes del objeto.
- kronut escribe los Set Lists como `75 0D bank idH idL ver datos`; el editor oficial usa `75 0D 00 datos`. El MCP sigue el formato del editor.

**Importante para mantenimiento:** `75` solo modifica el buffer de edición; para persistirlo hay que enviar `76`. En cambio `73` escribe directamente en la posición de memoria indicada.

### Estructura de Set List (69 416 bytes internos) ✅
- `name[24]`, `slots[128]`, `eq_bypass`, `band_levels[9]`, `control_surface_mode`, `control_surface_assign_from` (0 = Slot, 1 = Set List), `slots_per_page` (0 = 16, 1 = 8, 2 = 4), `reserved[3]`. Si `name[0] == 0xFF` el set list está sin inicializar.
- **Slot** (542 bytes): `name[24]`, `performance_type` (bits 0-1 tipo, 2-5 color, 6-7 bits bajos de fuente), `performance_bank` (bits 0-4 banco, 5-7 bits altos de transposición), `performance_index`, `hold_time`, `volume`, `keyboard_track` (bits 0-3 kbd track, bit 4 bit alto de fuente, 5-7 bits bajos de transposición), `comments[512]`.
- Bancos de performance: INT-A…F = `00–05`, GM = `06`, g(1)…g(9) = `07–0F`, g(d) = `10`, USER-A…G = `11–17`, USER-AA…GG = `18–1E`.

### Selección de bancos por CC (Program mode) ✅
INT-A…F: `B0 00 00` + `B0 20 00…05` · I-G (GM): `B0 00 79` · USER-A…G: `B0 00 00` + `B0 20 08…0E`, seguido de Program Change.

---

## 9. Fuentes y documentación oficial pendiente

**Documentación oficial de Korg** (contiene los `.txt` de definición de parámetros: Global.txt, Prog_EXi.txt, SetList.txt, etc. — imprescindibles para `43/44` y para interpretar dumps de Program/Combi/Global):
- KRONOS SysEx documentation 2.1 (Kronos / Kronos X, sistema 2.1): enlazada desde shop.korg.com/kronos/support.aspx → `KRONOS_SysEx_2_1.zip`.
- KRONOS SysEx documentation 2.0.
- KRONOS MIDI Implementation (zip) en korg.com, sección de descargas del Kronos.

No se pudieron descargar desde el entorno de trabajo (dominio bloqueado). **Siguiente paso: descargar `KRONOS_SysEx_2_1.zip` y subir su contenido al proyecto.**

Ten en cuenta que la doc SysEx es de la versión 2.1 y el sistema actual es 3.x: puede haber parámetros nuevos (SGX-2, efectos vintage, Set List 3.x) no documentados.

**Fuentes usadas:**
- KRONOS Editor 3.1 (Sound Quest), preload SysEx `KRONOS_Preload.SQS`: ver `docs/ingenieria_inversa.md`.
- kronut (editor de Set Lists para Kronos, C++/RtMidi): https://github.com/jimm/kronut — commit d8c8650 (2026-05-13), ficheros `src/consts.h`, `src/kronos.cpp`, `src/midi_data.cpp`, `src/set_list.h`, `src/slot.h`.
- Korg Kronos support (descargas, doc SysEx): https://shop.korg.com/kronos/support.aspx
- Korg JP, descargas Kronos (2011) / Kronos X: https://www.korg.com/jp/support/download/product/0/84
- Korg US, descargas Kronos: https://www.korg.com/us/support/download/product/0/988/
- Tabla de implementación MIDI (Universal SysEx soportados, filtros): https://manualslib.mx/manual/7923/Korg-Kronos.html?page=297
- Mensajes de cambio de modo probados: https://zubersoft.com/mobilesheets/forum/thread-9582.html y https://www.zubersoft.com/mobilesheets/forum/showthread.php?tid=7845

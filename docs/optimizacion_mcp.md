# Optimización del MCP kronos — salidas compactas y menos idas y vueltas

> Objetivo: que diseñar un sonido o una Combi cueste 3-4 llamadas ligeras en vez de ~15 pesadas.
> Origen: sesión del 2026-10-17 (leads NEON/GLASS/RAZOR, 80s RIFF/PAD/FM, combis INT-B 024-026).
> Cada respuesta del MCP se queda en el contexto del modelo y se relee en cada turno: el tamaño de salida es el coste principal.

## Problemas observados

| # | Problema | Evidencia |
|---|---|---|
| 1 | `decode_object` sin `query` devuelve el objeto entero (KARMA, Dynamic MIDI, Audio Input, Drum Track…): miles de líneas | USER-G 028, primera lectura |
| 2 | Toda respuesta de `decode_object` repite `effects` + `exi`, y cada parámetro trae `ofs`, `range` e `id` | todas las lecturas filtradas |
| 3 | Para entender un Program hacen falta 5-6 llamadas (OSC, Filter, Amp EG, EG1, Legato, efectos) | análisis de los 3 leads |
| 4 | `design_program` / `design_combi` devuelven el `summary` completo además del diff | todos los store |
| 5 | `copy_effect` copia también `Chain Switch` / `Chain To` del donante → la relectura tras store "no coincide" y hay que volver a guardar | USER-G 042, 044; INT-B 026 |
| 6 | `copy_effect` de Master Effects no copia `Master Effect / Return 1-2` (ofs 1048-1049); en una Combi inicializada quedan a 0 y el delay/reverb no suenan | INT-B 025 |
| 7 | `combi_info` lee el catálogo en caché: tras guardar una Combi devuelve lo de antes | INT-B 024 tras store |
| 8 | Crear una Combi desde slot vacío obliga a apagar a mano Timbre3..16 (16 cambios) | INT-B 025, 026 |
| 9 | Tocar notas exige un script (`scripts/riff_demo.py`) y `run_script` | demo de riff y pad |
| 10 | `find_parameter` no encuentra nada con términos comunes ("Voice Assign", "Portamento", "Mono") y no sugiere alternativas | búsqueda del modo legato |

## Cambios propuestos (por prioridad)

### P1 — `decode_object` compacto  (`server.py`, `params.py`)
- Nuevo parámetro `format`: `"compact"` (por defecto) | `"full"` (comportamiento actual).
- `compact`: un parámetro por línea, `Nombre = valor` (valor ya traducido a texto cuando el mapa tenga lista: `Saw`, `Low Pass`, `On`…). Sin `ofs`/`range`/`id`.
- `query` acepta lista: `query: ["[Filter A] Cutoff", "Amplifier EG]", "Legato"]` → una sola llamada.
- `header`: `true|false` (por defecto `false` si hay `query`) para incluir o no `effects`/`exi`.
- `skip_defaults`: omitir parámetros a 0/Off/valor inicial (útil para Mod Sources apagadas).
- Sin `query` y en `compact`: excluir por defecto los grupos KARMA, Dynamic MIDI, Audio Input, S/PDIF, Drum Track, Vector Control (incluir con `groups=[...]`).
- **Aceptación:** decode de USER-G 028 sin query en `compact` < 150 líneas; con 3 queries en una llamada < 60 líneas.

### P2 — Nueva tool `voice_summary(ref, obj_type="PROGRAM")`
Resumen legible en ~30 líneas, una llamada:
- Motor(es) EXi / HD-1 y modo de voz: Poly / Legato / Poly Legato.
- Por EXi: OSC1/OSC2 (forma de onda, octava, tune, sync, FM amount, nivel), filtro (routing, tipo, cutoff, reso, EG int, key track, vel), Amp EG y EG1 como `A/D/S/R` + niveles, LFOs con destino ≠ 0.
- Cadena de efectos: `IFX1 Stereo Limiter → IFX2 … (chain)`, MFX1/2 con Return, TFX.
- Para Combi: timbres activos (programa, canal, vol, transp, zona, bus) + efectos + retornos.
- **Aceptación:** con `voice_summary` basta para describir USER-G 001/004/028 sin otro decode.

### P3 — `design_program` / `design_combi` ligeros y correctos  (`design.py`)
- Respuesta por defecto: `applied` (solo los que cambian: `old→new`), `status`, `verified`, `backup_before`. `summary` solo con `verbose=true`.
- No incluir en `applied` los cambios `old == new`.
- `copy_effect`: copiar tipo + parámetros del efecto, pero **conservar** `Chain Switch`, `Chain To`, `Bus Select` del slot destino (opción `keep_routing=true` por defecto; `false` para el comportamiento actual).
- `copy_effect` de `Master Effect1/2`: copiar también el `Return` correspondiente (o avisar si el destino queda a 0).
- Verificación tras store: ignorar campos que el Kronos normaliza (`Chain To` cuando `Chain Switch = Off`) o normalizarlos antes de escribir, para que `verified` sea fiable a la primera.
- `design_combi`: opción `init_unused_timbres=true` → pone `Status = Off` en los timbres no tocados por `changes` cuando el destino es una Combi vacía/inicializada.
- **Aceptación:** repetir los stores de USER-G 042/044 e INT-B 026 → `verified: true` a la primera; INT-B 025 recreada suena con delay/reverb sin tocar Returns.

### P4 — Caché coherente  (`catalog.py`)
- Tras cualquier store (Program, Combi, Set List), refrescar la entrada del catálogo de ese slot.
- `combi_info` / `find_sound` / `list_bank` deben reflejar el cambio sin `refresh_catalog`.
- **Aceptación:** `design_combi(... store)` seguido de `combi_info` muestra el nombre y timbres nuevos.

### P5 — Nueva tool `play_notes`
- `play_notes(events, bpm=120, channel=None, select=None)`
  - `events`: lista de `{"n": 74 | "D5" | [50,54,57], "dur": 0.5 (en negras), "vel": 100, "at": opcional}`.
  - `select`: `"INT-B 025"` → Bank Select + Program Change antes de tocar (cambia a Combination/Program si hace falta).
  - Siempre `All Notes Off` al final (en `finally`), mantiene puertos abiertos durante la reproducción (`client.hold`).
  - Límite de duración (p. ej. 60 s) para no bloquear el server.
- Sustituye a `scripts/riff_demo.py` (puede quedarse como ejemplo).

### P6 — `find_parameter` más útil
- Búsqueda difusa y por sinónimos: `mono|voice assign → Legato / Poly Legato`, `portamento|glide`, `cutoff|freq`, `vibrato → LFO`.
- Si no hay resultados, devolver los 5 nombres más parecidos.

## Fuera de alcance (por ahora)
- Cambiar los mapas JSON de `data/params/`.
- Tocar el transporte MIDI (`transport/midi.py`) más allá de lo que necesite `play_notes`.

## Pruebas
- `pytest` con el mock (`KRONOS_MOCK=1`):
  - `decode_object` compact/full, query lista, header on/off, skip_defaults.
  - `voice_summary` sobre un Program EXi y una Combi del mock.
  - `copy_effect` con `keep_routing` y copia de Returns.
  - Store + verify con normalización de `Chain To`.
  - Catálogo refrescado tras store.
  - `play_notes`: eventos enviados correctos y `All Notes Off` aunque haya excepción.
- Prueba real (teclado conectado): repetir el flujo "80s RIFF COMBI" desde cero y contar llamadas (objetivo ≤ 4).

## Notas ya registradas en el knowledge del server
- `copy_effect` arrastra Chain Switch/Chain To; primera relectura "no coincide" (2026-10-17).
- `copy_effect` de Master FX no copia Return 1-2 (2026-10-17).

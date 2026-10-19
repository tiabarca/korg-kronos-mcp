# Roadmap: bucle de retroalimentación por audio

Objetivo: cerrar el círculo **MIDI → Kronos → audio → PC → análisis → ajuste de parámetros → repetir**, de modo que el MCP pueda juzgar por el sonido real (no solo por los bytes) si un programa o combinación suena como se pretende.

Todo va detrás de un flag. Sin él, el servidor se comporta exactamente como ahora (sin dependencias nuevas, sin tools nuevas).

## 0. Principios

- **Opt-in:** `KRONOS_AUDIO=1` registra las tools de audio. Sin el flag no se importa `numpy`/`sounddevice`.
- **Dependencias opcionales:** extra `pip install kronos-mcp[audio]` (`sounddevice`, `numpy`, `scipy`, `soundfile`; `librosa` solo si hace falta en la fase 3).
- **Lectura libre, escritura protegida:** capturar y analizar no modifica el teclado. El ajuste automático (fase 5) reutiliza las reglas actuales: `KRONOS_ALLOW_WRITE=1` + `confirm=true` + backup previo.
- **Respuestas ligeras:** el WAV se guarda en disco (`data/audio/captures/`); al cliente solo vuelven métricas compactas y la ruta. Coherente con `docs/optimizacion_mcp.md`.
- **Probar sin hardware:** backend de audio simulado (`transport/mock.py` ya existe para MIDI) para que `pytest` cubra todo.
- **Hallazgos registrados:** cada descubrimiento va a `note` / `learn_param`, como en el resto del proyecto.

## 1. Configuración (flag y entorno)

| Variable | Defecto | Función |
|---|---|---|
| `KRONOS_AUDIO` | `0` | Activa el módulo de audio |
| `KRONOS_AUDIO_DEVICE` | auto | Dispositivo de entrada (nombre parcial o índice) |
| `KRONOS_AUDIO_SR` | `48000` | Frecuencia de muestreo |
| `KRONOS_AUDIO_CHANNELS` | `2` | Canales a capturar |
| `KRONOS_AUDIO_LATENCY_MS` | calibrado | Compensación ida y vuelta |
| `KRONOS_AUDIO_MAX_SECONDS` | `30` | Tope por captura |
| `KRONOS_MIDI_LIBRARY` | `data/midi/` | Carpeta de corpus MIDI |

Ruta de audio (por orden de preferencia):
1. **Audio USB del propio Kronos** por el mismo cable USB que ya lleva el MIDI: un solo cable para MIDI y audio, sin interfaz externa. Es la ruta objetivo.
2. **Interfaz de audio externa** (entrada de línea) conectada a las salidas analógicas, como alternativa si la ruta 1 no está disponible o su calidad/ruteo no sirve.

`audio_status` detecta cuál está activa (busca un dispositivo de entrada cuyo nombre coincida con el Kronos, o usa `KRONOS_AUDIO_DEVICE`). **Pendiente de verificar con el teclado encendido** (el 2026-10-19 Windows no listaba ningún dispositivo Korg): que aparezca como dispositivo de entrada, cuántos canales expone, qué bus del Kronos se envía por USB (ajuste de Audio/USB Routing en el Kronos) y si entrega el audio ya procesado por los efectos o pre-master. Lo que se descubra se registra con `note`.

## 2. Fases

### Fase 1 — Captura de audio y calibración  (base)
- **Desarrollo sin teclado:** todo lo de las fases 1-4 (captura, corpus, análisis) se construye y se prueba con el backend de audio simulado y WAVs de ejemplo; el hardware solo es imprescindible para el paso 0, la calibración de latencia y las pruebas `test_live.py`. El audio USB se da por posible y se confirma cuando haya teclado.
- **Paso 0, spike de verificación (pendiente, requiere teclado conectado):** con el Kronos encendido, listar dispositivos de entrada (`sounddevice`), grabar 5 s con una nota sostenida y confirmar canales, nivel y bus de origen. Decide si la ruta USB basta o hace falta interfaz externa.
- Clock: con audio USB el reloj del dispositivo es el del Kronos, así que la deriva frente al MIDI debería ser mínima; se mide en la calibración.
- `src/kronos_mcp/audio/capture.py`: abrir dispositivo, grabar N segundos, guardar WAV, detectar clipping y silencio.
- Tools: `list_audio_devices`, `audio_status`, `record_audio(seconds)`.
- `calibrate_latency`: envía una nota percusiva, mide el retardo MIDI→audio y lo guarda para alinear capturas.
- Nivel de entrada: medir ruido de fondo (suelo) y sugerir ganancia.
- **Hecho cuando:** grabar mientras `play_notes` suena devuelve un WAV alineado con las notas (error < 10 ms tras calibrar) y la suite pasa con el backend simulado.

### Fase 2 — Corpus de estímulos MIDI
Dos familias de estímulos:

1. **Sintéticos (generados en código, sin licencias):** escalas, acordes, barrido de notas 0-127, barrido de velocity, notas largas con release, glissandos, ráfagas de notas repetidas. Son los más útiles para sonidos con **capas**: un barrido por zona de nota o de velocity revela qué capa suena en cada punto.
2. **Musicales, de fuentes abiertas y fiables:** candidatos a evaluar y verificar licencia antes de integrar: Mutopia Project (dominio público/CC), MAESTRO (piano, CC BY 4.0), Lakh MIDI Dataset (CC BY 4.0), Groove MIDI Dataset (batería, CC BY 4.0), Nottingham (melodías folk). Se descargan bajo demanda con un script (`tools/fetch_midi_corpus.py`), nunca se versionan en git, y se guarda un `manifest.json` con origen, licencia y hash de cada archivo.

- **MIDI propio del usuario:** parámetro `midi_file=` (ruta) y carpeta `data/midi/user/`. Se valida (formato 0/1, duración, polifonía, canales usados) y se puede recortar por compases o canal.
- Etiquetado de cada pieza: rango de notas, polifonía máxima, densidad, velocity media. Sirve para elegir qué estímulo ejercita qué tipo de sonido.
- Tools: `list_stimuli`, `play_midi(file | stimulus_id, channel, tempo, start/end)`; reutiliza el planificador de `play.py` (límite de duración y All Notes Off al final).
- **Hecho cuando:** `play_midi` toca un `.mid` propio y uno del corpus, y `list_stimuli` muestra licencia y etiquetas.

### Fase 3 — Análisis acústico
- `audio/analysis.py`, salida siempre compacta (JSON pequeño):
  - Nivel: RMS, pico, LUFS aproximado, rango dinámico.
  - Envolvente: ataque, decaimiento, sustain, release.
  - Timbre: centroide y rolloff espectral, planicie, MFCC resumidos, energía por bandas, armónicos (f0, inarmonicidad).
  - Espacio: anchura estéreo, correlación L/R.
  - Modulación: profundidad y velocidad de vibrato/trémolo/filtro.
- Huella (`fingerprint`) normalizada por nivel para comparar sonidos entre sí.
- Tools: `analyze_audio(file)`, `compare_audio(a, b)` (distancia por dimensión, no un número opaco).
- **Hecho cuando:** dos tomas del mismo programa dan distancia ~0, y programas distintos dan distancias separables en un conjunto de prueba.

### Fase 4 — Análisis por capas
- Un sonido con X capas se prueba **aislando**: Combinations con varios timbres (silenciar/solo por timbre vía `set_parameter` en el buffer de edición, con restauración garantizada) y Programs con varios osciladores.
- `layer_report(object)`: para cada capa, mapa nota×velocity (suena / no suena / nivel), rango de zona detectado y huella propia; compara con lo que dice el decode del SysEx (zonas, volúmenes, panning) y señala discrepancias.
- Detecta capas muertas, desbalances de nivel y solapes de zonas.
- **Hecho cuando:** sobre una combinación conocida el informe coincide con `combi_info` y marca una capa silenciada a propósito.

### Fase 5 — Bucle de ajuste (el cierre del círculo)
- Entrada: **objetivo** = audio de referencia (WAV del usuario), huella de un sonido existente, o descriptores textuales traducibles a métricas ("más brillante", "ataque más lento").
- Bucle: tocar estímulo → capturar → medir distancia al objetivo → proponer cambio de **un** parámetro (regla del proyecto: un cambio por experimento) → aplicar en el buffer de edición → repetir.
- Estrategia incremental: primero búsqueda guiada por heurísticas (brillo → cutoff, ataque → EG, etc., apoyado en `knowledge` y `find_parameter`); después optimización por coordenadas sobre un subconjunto acotado; opcional más adelante CMA-ES/bayesiana.
- Salvaguardas obligatorias:
  - Requiere `KRONOS_ALLOW_WRITE=1`, `confirm=true` y backup previo (`backup_bank`).
  - Solo toca el **buffer de edición**; nunca almacena (store) sin orden explícita aparte.
  - Lista blanca de parámetros y rangos por sesión, máximo de iteraciones, parada por no mejora.
  - Límite de nivel de salida y corte inmediato ante clipping.
  - Registro de cada iteración (parámetro, valor, métrica) y reversión al estado inicial con un comando.
  - Parámetros con efecto aleatorio o sincronizado (LFO libre, arpegiador, randomize) se marcan y se fija semilla/estado cuando se pueda.
- Cada ajuste que mejora la distancia se anota con `learn_param` (relación parámetro→efecto acústico), alimentando la heurística de futuras sesiones.
- Tools: `tune_start(target, params, budget)`, `tune_step`, `tune_status`, `tune_revert`.
- **Hecho cuando:** partiendo de un programa degradado a propósito, el bucle recupera una huella a distancia aceptable de la original en el presupuesto de iteraciones.

### Fase 6 — Endurecimiento y documentación
- Informe de sesión (Markdown) con gráficas de espectro/envolvente por iteración.
- Detección de deriva: reafinación, temperatura, cambios de ganancia entre sesiones.
- Documentar cableado, ganancia recomendada, formato de MIDI propio, y cómo añadir fuentes al corpus.
- `export_knowledge` incluye lo aprendido en el bucle.

## 3. Tests

- Backend de audio simulado que sintetiza una señal determinista a partir de los mensajes MIDI/SysEx del mock, para tests del bucle sin hardware.
- Tests unitarios de métricas con señales sintéticas conocidas (senoide, ruido, clic).
- Tests de seguridad: sin `ALLOW_WRITE` el bucle de ajuste se niega; sin el flag de audio las tools no existen.
- `test_live.py` gana casos opcionales con teclado e interfaz reales (marcados para no correr en CI).

## 4. Riesgos y decisiones abiertas

| Riesgo | Mitigación |
|---|---|
| El audio USB no aparece, tiene pocos canales o no incluye efectos | Spike de la fase 1; si falla, usar interfaz externa. `audio_status` lo informa con mensaje claro |
| Latencia y deriva de reloj MIDI vs audio | Calibración (fase 1) y alineado por correlación con el estímulo |
| Efectos y colas de reverb contaminan la medida | Opción de medir con efectos IFX/MFX/TFX en bypass y con ellos activados, por separado |
| Variabilidad del sonido (LFO, ruido) | Varias tomas, mediana, y marcar parámetros no deterministas |
| Licencias del corpus MIDI | Solo fuentes con licencia verificada, manifest con origen, nada versionado en git |
| Distancia acústica ≠ percepción | Métricas descompuestas por dimensión; el usuario puede validar a oído con `play_notes` |
| Escritura automática en el teclado | Backup, lista blanca, solo buffer de edición, reversión, confirmación explícita |

Decisiones que conviene cerrar antes de la fase 5:
1. ¿El objetivo principal es **igualar un audio de referencia** o **verificar que un diseño cumple su intención**? Cambia la métrica por defecto.
2. Resultado del spike de audio USB (canales y bus de origen). Condiciona la fase 1.
3. ¿Se aceptan `librosa`/`scipy` como dependencias, o se prefiere solo `numpy`?

## 5. Orden sugerido

Fase 1 → 2 (en paralelo con 3) → 4 → 5 → 6. Las fases 1-3 ya dan valor por sí solas (grabar y analizar lo que suena); la 5 es la que exige más cautela y va la última.

# Documentación oficial Korg (MIDI Implementation)

Aportada el 2026-10-12. Es la fuente de verdad del proyecto.

- `KRONOS_MIDI_SysEx.txt`: especificación de todos los mensajes SysEx (v1.26, ago-2014).
- `KRONOS_MIDI_Implementation.pdf`: tabla de implementación MIDI.
- Mapas de parámetros (OFS, bits, rango, Parameter ID TYP/SOC/SUB/PID/IDX):
  - `Prog_EXi_Common.txt`, `Prog_EXi.txt` (motores), `Prog_HD-1.txt`
  - `CombiAndSongTimbreSet.txt`, `Global.txt`, `Effect.txt`, `SetList.txt`, `DrumKit.txt`, `WaveSequence.txt`
  - `SongControl.txt`, `Song.txt`, `DrumTrackPattern.txt`, `SongEvent.txt`, `KARMA_GE_RTP.txt`

Para regenerar los mapas JSON que usa el MCP: `python tools/build_param_maps.py` (escribe en `data/params/`).

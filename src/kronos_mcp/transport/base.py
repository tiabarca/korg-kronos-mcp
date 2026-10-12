"""Interfaz de transporte MIDI (real o simulado)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

Listener = Callable[[str, bytes], None]  # (dirección 'in'/'out', bytes)


class Transport(ABC):
    def __init__(self) -> None:
        self.listeners: list[Listener] = []

    def _emit(self, direction: str, raw: bytes) -> None:
        for fn in list(self.listeners):
            try:
                fn(direction, raw)
            except Exception:  # noqa: BLE001 - un listener no debe tumbar el transporte
                pass

    @abstractmethod
    def send(self, raw: bytes) -> None: ...

    @abstractmethod
    def receive(self, timeout: float) -> bytes | None:
        """Devuelve el siguiente mensaje SysEx recibido o None si vence el timeout."""

    def drain(self) -> None:
        """Descarta SysEx pendientes (respuestas viejas) antes de una nueva petición."""

    def send_channel(self, status: int, *data: int) -> None:
        self.send(bytes([status, *data]))

    def close(self) -> None:  # pragma: no cover
        pass

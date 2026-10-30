"""Zerlegung des Ereignisstroms aus Puffer 2 (CBUF2).

Ereignisse können über Reportgrenzen hinweg geteilt sein; der Parser hält
unvollständige Reste bis zum nächsten ``feed`` zurück.
"""

from dataclasses import dataclass
from typing import List

from .constants import EVENT_ARG_SIZES, Event


@dataclass(frozen=True)
class StreamEvent:
    tag: int
    args: bytes = b""

    @property
    def name(self) -> str:
        try:
            return Event(self.tag).name
        except ValueError:
            return f"UNKNOWN_0x{self.tag:02X}"

    def __repr__(self) -> str:
        return f"StreamEvent({self.name}, {self.args.hex(' ')})"


class StreamParser:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self.unknown_tags = 0

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> List[StreamEvent]:
        self._buffer += data
        events: List[StreamEvent] = []
        buf = self._buffer
        i = 0
        while i < len(buf):
            tag = buf[i]
            if tag not in EVENT_ARG_SIZES:
                # Ohne Längenangabe ist keine Resynchronisation möglich:
                # Rest verwerfen, mit dem nächsten Paket neu aufsetzen.
                self.unknown_tags += 1
                events.append(StreamEvent(tag, bytes(buf[i + 1:])))
                i = len(buf)
                break
            size = self._size(tag, buf, i)
            if size is None or i + 1 + size > len(buf):
                break
            events.append(StreamEvent(tag, bytes(buf[i + 1:i + 1 + size])))
            i += 1 + size
        del buf[:i]
        return events

    @staticmethod
    def _size(tag: int, buf: bytearray, i: int):
        size = EVENT_ARG_SIZES[tag]
        if size is not None:
            return size
        if tag == Event.DATA_BYTES:
            # Länge + Daten
            if i + 1 >= len(buf):
                return None
            return 1 + buf[i + 1]
        if tag == Event.LIN_SLAVE_PROFILE_DATA:
            # PID, Länge, Daten
            if i + 2 >= len(buf):
                return None
            return 2 + buf[i + 2]
        return None

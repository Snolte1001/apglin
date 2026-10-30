"""USB-Verkehr mitschneiden und wiedergeben, z. B. für Regressionstests.

Dateiformat: eine Zeile je Report, ``W <hex>`` (Host → Gerät, ohne
Füllnullen) oder ``R <hex>`` (Gerät → Host, alle 64 Byte), ``#`` leitet
Kommentare ein.
"""

import queue
import threading
from pathlib import Path
from typing import List, Optional, Tuple, Union

from .constants import REPORT_SIZE
from .transport import Transport

Entry = Tuple[str, bytes]


class RecordingTransport(Transport):
    """Leitet an ``inner`` weiter und merkt sich jeden Report."""

    def __init__(self, inner: Transport):
        self.inner = inner
        self.entries: List[Entry] = []
        self._lock = threading.Lock()

    def write(self, report: bytes) -> None:
        with self._lock:
            self.entries.append(("W", bytes(report)))
        self.inner.write(report)

    def read(self, timeout: float) -> Optional[bytes]:
        report = self.inner.read(timeout)
        if report:
            with self._lock:
                self.entries.append(("R", bytes(report)))
        return report

    def close(self) -> None:
        self.inner.close()

    def save(self, path: Union[str, Path], comment: str = "") -> None:
        lines = [f"# {line}" for line in comment.splitlines()]
        for kind, report in self.entries:
            data = report.rstrip(b"\x00") if kind == "W" else report
            lines.append(f"{kind} {data.hex(' ')}")
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_recording(path: Union[str, Path]) -> List[Entry]:
    entries: List[Entry] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, data = line.partition(" ")
        report = bytes.fromhex(data)
        entries.append((kind, report + bytes(REPORT_SIZE - len(report))))
    return entries


class ReplayMismatch(AssertionError):
    pass


class ReplayTransport(Transport):
    """Spielt einen Mitschnitt ab.

    Jeder geschriebene Report muss dem nächsten ``W`` des Mitschnitts
    gleichen; danach werden die bis zum folgenden ``W`` aufgezeichneten
    Antworten freigegeben. So bleibt die Reihenfolge erhalten, die der
    Code bei der Aufnahme gesehen hat.
    """

    def __init__(self, entries: List[Entry]):
        self._entries = list(entries)
        self._pos = 0
        self._out: "queue.Queue[bytes]" = queue.Queue()
        self._lock = threading.Lock()
        self._release()

    @classmethod
    def load(cls, path: Union[str, Path]) -> "ReplayTransport":
        return cls(load_recording(path))

    def _release(self) -> None:
        while self._pos < len(self._entries) and self._entries[self._pos][0] == "R":
            self._out.put(self._entries[self._pos][1])
            self._pos += 1

    def write(self, report: bytes) -> None:
        with self._lock:
            if self._pos >= len(self._entries):
                raise ReplayMismatch(f"Mitschnitt zu Ende, geschrieben: {report.rstrip(bytes(1)).hex(' ')}")
            expected = self._entries[self._pos][1]
            if bytes(report) != expected:
                raise ReplayMismatch(
                    f"Report {self._pos}: erwartet {expected.rstrip(bytes(1)).hex(' ')}, "
                    f"geschrieben {bytes(report).rstrip(bytes(1)).hex(' ')}"
                )
            self._pos += 1
            self._release()

    def read(self, timeout: float) -> Optional[bytes]:
        try:
            return self._out.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def remaining_writes(self) -> int:
        return sum(1 for kind, _ in self._entries[self._pos:] if kind == "W")

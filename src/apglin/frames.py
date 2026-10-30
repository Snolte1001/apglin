"""Zusammensetzen von LIN-Frames aus dem Ereignisstrom."""

import time
from dataclasses import dataclass, field
from enum import IntFlag
from typing import Callable, List, Optional

from .constants import EVENT_ROLLOVER_S, EVENT_TICK_S, MAX_LIN_PAYLOAD, Event
from .lin import ChecksumType, brg_to_baud, detect_checksum_type, frame_id_of, parity_ok
from .stream import StreamEvent


class FrameError(IntFlag):
    NONE = 0
    NO_RESPONSE = 0x01         # nur Header, keine Antwort
    CHECKSUM = 0x02            # Prüfsumme passt weder classic noch enhanced
    PARITY = 0x04              # PID-Paritätsbits falsch
    BUS_ERROR = 0x08           # Statusfehler des Geräts während des Frames
    DEVICE_CHECKSUM = 0x10     # Gerät meldet Prüfsummenfehler
    DEVICE_PARITY = 0x20       # Gerät meldet ID-Paritätsfehler


@dataclass(frozen=True)
class LinFrame:
    pid: int
    data: bytes
    checksum: Optional[int]
    checksum_type: Optional[ChecksumType]
    errors: FrameError = FrameError.NONE
    timestamp: Optional[float] = None      # Gerätezeit in s seit Timer-Reset
    host_time: float = field(default_factory=time.time)
    baudrate: Optional[int] = None
    transmitted: bool = False              # vom Analyzer selbst gesendet (sofern erkennbar)

    @property
    def frame_id(self) -> int:
        return frame_id_of(self.pid)

    @property
    def ok(self) -> bool:
        return self.errors == FrameError.NONE

    def __str__(self) -> str:
        ts = f"{self.timestamp:10.4f}s " if self.timestamp is not None else ""
        chk = f" chk={self.checksum:02X}" if self.checksum is not None else ""
        kind = f" {self.checksum_type.value}" if self.checksum_type else ""
        err = "" if self.ok else f" [{self.errors!r}]"
        data = self.data.hex(" ").upper() or "-"
        tx = "TX " if self.transmitted else "RX "
        return f"{ts}{tx}ID=0x{self.frame_id:02X} PID=0x{self.pid:02X} [{len(self.data)}] {data}{chk}{kind}{err}"


class FrameAssembler:
    """Setzt Frames wie die Microchip-DLL zusammen.

    Ein Frame beginnt mit ``LIN_BREAK_RX``. Das erste Datenbyte danach ist die
    PID, die weiteren sind Daten, das letzte davon die Prüfsumme. Er endet mit
    dem nächsten Break, nach 9 Bytes, bei einem Statusfehler oder wenn
    ``timeout`` Sekunden lang kein Ereignis mehr kam (``poll``).
    """

    def __init__(self, timeout: float = 0.1, clock: Callable[[], float] = time.monotonic):
        self.timeout = timeout
        self.default_baudrate: Optional[int] = None
        self._clock = clock
        self._rollover = 0.0
        self._building = False
        self._pending_tx = False
        self.reset_time()
        self._clear()

    def reset_time(self) -> None:
        self._rollover = 0.0

    def _clear(self) -> None:
        self._building = False
        self._want_time = False
        self._pid: Optional[int] = None
        self._bytes = bytearray()
        self._time: Optional[float] = None
        self._baud: Optional[int] = None
        self._errors = FrameError.NONE
        self._tx = False
        self._last_activity = self._clock()

    def feed(self, events: List[StreamEvent]) -> List[LinFrame]:
        out: List[LinFrame] = []
        for ev in events:
            self._handle(ev, out)
        return out

    def poll(self) -> List[LinFrame]:
        if self._building and self._clock() - self._last_activity > self.timeout:
            return self._finish()
        return []

    def flush(self) -> List[LinFrame]:
        return self._finish() if self._building else []

    def _handle(self, ev: StreamEvent, out: List[LinFrame]) -> None:
        tag = ev.tag
        if self._building:
            self._last_activity = self._clock()
        if tag == Event.LIN_BREAK_RX:
            if self._building:
                out += self._finish()
            self._building = True
            self._want_time = True
            self._tx = self._pending_tx
            self._pending_tx = False
            self._last_activity = self._clock()
        elif tag == Event.LIN_BREAK_TX:
            self._pending_tx = True
        elif tag == Event.TIME_ROLLOVER:
            self._rollover += EVENT_ROLLOVER_S
        elif tag == Event.TIME:
            if self._building and self._want_time:
                ticks = ev.args[0] | (ev.args[1] << 8)
                self._time = self._rollover + ticks * EVENT_TICK_S
                self._want_time = False
        elif tag in (Event.DATA_BYTE, Event.DATA_BYTES):
            payload = ev.args if tag == Event.DATA_BYTE else ev.args[1:]
            for byte in payload:
                if self._building:
                    self._add_byte(byte, out)
        elif tag == Event.LIN_AUTOBAUD:
            baud = brg_to_baud(ev.args[0] | (ev.args[1] << 8))
            self.default_baudrate = baud
            if self._building:
                self._baud = baud
        elif tag in (Event.LIN_STATUS_ERROR, Event.STATUS_ERROR):
            if self._building:
                self._errors |= FrameError.BUS_ERROR
                out += self._finish()
        elif tag == Event.LIN_CHECKSUM_ERROR:
            if self._building:
                self._errors |= FrameError.DEVICE_CHECKSUM
        elif tag == Event.LIN_ID_PARITY_ERROR:
            if self._building:
                self._errors |= FrameError.DEVICE_PARITY

    def _add_byte(self, byte: int, out: List[LinFrame]) -> None:
        if self._pid is None:
            self._pid = byte
            return
        self._bytes.append(byte)
        if len(self._bytes) >= MAX_LIN_PAYLOAD:
            out += self._finish()

    def _finish(self) -> List[LinFrame]:
        pid = self._pid
        if pid is None:
            # Break ohne PID: nichts Auswertbares
            self._clear()
            return []
        errors = self._errors
        if not parity_ok(pid):
            errors |= FrameError.PARITY
        if self._bytes:
            data = bytes(self._bytes[:-1])
            chk: Optional[int] = self._bytes[-1]
            kind = detect_checksum_type(pid, data, chk)
            if kind is None:
                errors |= FrameError.CHECKSUM
        else:
            data, chk, kind = b"", None, None
            errors |= FrameError.NO_RESPONSE
        frame = LinFrame(
            pid=pid,
            data=data,
            checksum=chk,
            checksum_type=kind,
            errors=errors,
            timestamp=self._time,
            baudrate=self._baud or self.default_baudrate,
            transmitted=self._tx,
        )
        self._clear()
        return [frame]

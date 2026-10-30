"""Reine Kodier-/Dekodierfunktionen für die HID-Reports (ohne I/O)."""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import constants as c
from .exceptions import ProtocolError
from .lin import brg_to_baud, baud_to_brg


# --- ausgehende Reports ----------------------------------------------------

def _report(payload: bytes) -> bytes:
    if len(payload) > c.REPORT_SIZE:
        raise ValueError(f"Report zu lang: {len(payload)} Byte")
    return bytes(payload) + bytes(c.REPORT_SIZE - len(payload))


def control(sub: c.Control) -> bytes:
    return _report(bytes([c.Command.CONTROL, sub]))


def special_status_request() -> bytes:
    return _report(bytes([c.Command.SPECIAL_STATUS, c.SPECIAL_STATUS_KEY]))


def clear_cbuf(number: int) -> bytes:
    if number not in (1, 2, 3):
        raise ValueError("Puffer 1..3")
    return _report(bytes([c.Command.CLEAR_CBUF1 + number - 1]))


def write_control_block(block: bytes) -> bytes:
    if len(block) != c.CONTROL_BLOCK_SIZE:
        raise ValueError(f"Control Block muss {c.CONTROL_BLOCK_SIZE} Byte haben")
    return _report(bytes([c.Command.WRITE_CONTROL_BLOCK]) + bytes(block))


def _chunked(command: int, data: bytes) -> List[bytes]:
    if not data:
        raise ValueError("Keine Daten")
    reports = []
    for start in range(0, len(data), c.MAX_SCRIPT_CHUNK):
        chunk = data[start:start + c.MAX_SCRIPT_CHUNK]
        reports.append(_report(bytes([command, len(chunk)]) + chunk))
    return reports


def write_script(script: bytes) -> List[bytes]:
    """Skript für CBUF1, bei Bedarf auf mehrere Reports verteilt."""
    return _chunked(c.Command.WRITE_CBUF1, bytes(script))


def write_cbuf3(data: bytes) -> List[bytes]:
    return _chunked(c.Command.WRITE_CBUF3, bytes(data))


# --- Skriptbausteine -------------------------------------------------------

def script_marker(value: int = c.MARKER_SCRIPT_DONE) -> bytes:
    return bytes([c.Script.MARKER, value])


def script_lin_send(pid: int, payload: bytes) -> bytes:
    """Break + Sync + PID + ``payload`` (Daten inkl. Prüfsumme).

    Leeres ``payload`` sendet nur den Header (Master Request).
    """
    if len(payload) > c.MAX_LIN_PAYLOAD:
        raise ValueError(f"Höchstens {c.MAX_LIN_PAYLOAD} Byte (8 Daten + Prüfsumme)")
    return bytes([c.Script.LIN_SEND, len(payload) + 1, pid & 0xFF]) + bytes(payload)


def script_lin_write_slave_profile(pid: int, data: bytes) -> bytes:
    if len(data) > 0xF4:
        raise ValueError("Slave-Profil zu lang")
    return bytes([c.Script.LIN_WRITE_SLAVE_PROFILE, pid & 0xFF, len(data)]) + bytes(data)


def script_lin_read_slave_profile(pid: int) -> bytes:
    return bytes([c.Script.LIN_READ_SLAVE_PROFILE, pid & 0xFF])


def script_led(led: int, value: int) -> bytes:
    if led not in (1, 2):
        raise ValueError("LED 1 oder 2")
    return bytes([c.Script.LED1 if led == 1 else c.Script.LED2, value & 0xFF])


def script_event_timer_reset() -> bytes:
    return bytes([c.Script.EVENT_TIMER_RESET])


# --- Control Block ---------------------------------------------------------

class ControlBlock:
    """Veränderbare Sicht auf die 24 Byte Konfiguration des Geräts."""

    def __init__(self, data: bytes = c.LIN_DEFAULT_CONTROL_BLOCK):
        if len(data) != c.CONTROL_BLOCK_SIZE:
            raise ValueError(f"Control Block muss {c.CONTROL_BLOCK_SIZE} Byte haben")
        self.data = bytearray(data)

    @classmethod
    def lin_default(cls) -> "ControlBlock":
        return cls(c.LIN_DEFAULT_CONTROL_BLOCK)

    def copy(self) -> "ControlBlock":
        return ControlBlock(bytes(self.data))

    def __bytes__(self) -> bytes:
        return bytes(self.data)

    def __eq__(self, other) -> bool:
        return isinstance(other, ControlBlock) and self.data == other.data

    def __repr__(self) -> str:
        return f"ControlBlock({self.data.hex(' ')})"

    def _flag(self, index: int, mask: int) -> bool:
        return bool(self.data[index] & mask)

    def _set_flag(self, index: int, mask: int, on: bool) -> None:
        if on:
            self.data[index] |= mask
        else:
            self.data[index] &= ~mask & 0xFF

    @property
    def comm_mode(self) -> int:
        return self.data[c.CB_COMM_MODE]

    @property
    def brg(self) -> int:
        return self.data[c.CB_BRG_LO] | (self.data[c.CB_BRG_HI] << 8)

    @brg.setter
    def brg(self, value: int) -> None:
        self.data[c.CB_BRG_LO] = value & 0xFF
        self.data[c.CB_BRG_HI] = (value >> 8) & 0xFF

    @property
    def baudrate(self) -> int:
        return brg_to_baud(self.brg)

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        self.brg = baud_to_brg(value)

    @property
    def autobaud(self) -> bool:
        return self._flag(c.CB_LIN_FLAGS, c.LIN_FLAG_AUTOBAUD)

    @autobaud.setter
    def autobaud(self, on: bool) -> None:
        self._set_flag(c.CB_LIN_FLAGS, c.LIN_FLAG_AUTOBAUD, on)

    @property
    def receive(self) -> bool:
        return self._flag(c.CB_LIN_FLAGS, c.LIN_FLAG_RECEIVE)

    @receive.setter
    def receive(self, on: bool) -> None:
        self._set_flag(c.CB_LIN_FLAGS, c.LIN_FLAG_RECEIVE, on)

    @property
    def chip_select(self) -> bool:
        return self._flag(c.CB_LIN_FLAGS, c.LIN_FLAG_CHIP_SELECT)

    @chip_select.setter
    def chip_select(self, on: bool) -> None:
        self._set_flag(c.CB_LIN_FLAGS, c.LIN_FLAG_CHIP_SELECT, on)

    @property
    def slave_profile(self) -> bool:
        return self._flag(c.CB_LIN_FLAGS, c.LIN_FLAG_SLAVE_PROFILE)

    @slave_profile.setter
    def slave_profile(self, on: bool) -> None:
        self._set_flag(c.CB_LIN_FLAGS, c.LIN_FLAG_SLAVE_PROFILE, on)

    @property
    def flush_on_count(self) -> bool:
        return self._flag(c.CB_FLAGS, c.CB_FLAG_FLUSH_ON_COUNT)

    @property
    def flush_on_time(self) -> bool:
        return self._flag(c.CB_FLAGS, c.CB_FLAG_FLUSH_ON_TIME)

    @property
    def flush_byte_count(self) -> int:
        return self.data[c.CB_FLUSH_BYTE_COUNT]

    @property
    def flush_interval_ms(self) -> float:
        return self.data[c.CB_FLUSH_INTERVAL] * c.FLUSH_TICK_MS

    def set_flush(self, on_count: bool, on_time: bool, byte_count: int, interval_ms: float) -> None:
        """Wann das Gerät gesammelte Ereignisse an den Host schickt."""
        self._set_flag(c.CB_FLAGS, c.CB_FLAG_FLUSH_ON_COUNT, on_count)
        self._set_flag(c.CB_FLAGS, c.CB_FLAG_FLUSH_ON_TIME, on_time)
        self.data[c.CB_FLUSH_BYTE_COUNT] = byte_count & 0xFF
        ticks = round(interval_ms / c.FLUSH_TICK_MS)
        if not 0 <= ticks <= 0xFF:
            raise ValueError(f"Flush-Intervall {interval_ms} ms außerhalb 0..{0xFF * c.FLUSH_TICK_MS:.1f} ms")
        self.data[c.CB_FLUSH_INTERVAL] = ticks


# --- Gerätestatus ----------------------------------------------------------

@dataclass(frozen=True)
class BufferStatus:
    cbuf1_used: int
    cbuf1_free: int
    cbuf2_used: int
    cbuf2_free: int
    cbuf3_used: int
    cbuf3_free: int

    @classmethod
    def from_bytes(cls, data: bytes) -> "BufferStatus":
        return cls(*data[:6])


@dataclass(frozen=True)
class DeviceStatus:
    firmware_version: Optional[int]
    control_block: Optional[ControlBlock]
    status_block: Optional[bytes]
    buffers: Optional[BufferStatus]

    @property
    def firmware_version_str(self) -> str:
        if self.firmware_version is None:
            return "?"
        return f"0x{self.firmware_version:04X}"

    @property
    def comm_mode(self) -> Optional[int]:
        if self.status_block is None:
            return None
        return self.status_block[c.SB_COMM_MODE]

    @property
    def error_code(self) -> int:
        """Sammelwert der drei Fehlerbytes (0 = kein Fehler)."""
        if self.status_block is None:
            return 0
        sb = self.status_block
        return sb[c.SB_ERROR_0] | (sb[c.SB_ERROR_1] << 8) | (sb[c.SB_ERROR_2] << 16)

    @property
    def has_error(self) -> bool:
        return self.error_code != 0

    @property
    def busy(self) -> bool:
        return bool(self.status_block and self.status_block[c.SB_FLAGS] & 0x01)

    @property
    def active_brg(self) -> Optional[int]:
        if self.status_block is None:
            return None
        return self.status_block[c.SB_BAUD_LO] | (self.status_block[c.SB_BAUD_HI] << 8)

    @property
    def active_baudrate(self) -> Optional[int]:
        """Baudrate laut Status Block (bei Autobaud die gemessene)."""
        brg = self.active_brg
        return None if brg is None else brg_to_baud(brg)


# --- eingehende Reports ----------------------------------------------------

def parse_report(report: bytes) -> List[Tuple[int, bytes]]:
    """Zerlegt einen eingehenden Report in (Tag, Nutzdaten)-Gruppen.

    Ein unbekannter Tag (auch Füllbytes 0x00) beendet die Auswertung des
    Reports. Eine abgeschnittene Gruppe löst ``ProtocolError`` aus.
    """
    groups: List[Tuple[int, bytes]] = []
    i = 0
    n = len(report)
    while i < n:
        tag = report[i]
        if tag == c.Group.END:
            break
        size = c.GROUP_FIXED_SIZES.get(tag)
        if size is not None:
            if i + 1 + size > n:
                raise ProtocolError(f"Gruppe 0x{tag:02X} abgeschnitten")
            groups.append((tag, bytes(report[i + 1:i + 1 + size])))
            i += 1 + size
        elif tag in c.GROUP_VARIABLE:
            if i + 1 >= n:
                raise ProtocolError(f"Gruppe 0x{tag:02X} ohne Länge")
            length = report[i + 1]
            if i + 2 + length > n:
                raise ProtocolError(f"Gruppe 0x{tag:02X} abgeschnitten")
            groups.append((tag, bytes(report[i + 2:i + 2 + length])))
            i += 2 + length
        else:
            break
    return groups

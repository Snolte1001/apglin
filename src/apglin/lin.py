"""LIN-Grundlagen: geschützte ID (PID), Prüfsummen, Baudrate <-> BRG."""

from enum import Enum
from typing import Iterable, Optional

from .constants import FOSC_HZ

# Diagnoseframes verwenden nach LIN 2.x immer die klassische Prüfsumme.
CLASSIC_ONLY_IDS = (0x3C, 0x3D)


class ChecksumType(str, Enum):
    CLASSIC = "classic"      # nur Datenbytes (LIN 1.x, Diagnose)
    ENHANCED = "enhanced"    # PID + Datenbytes (LIN 2.x)
    AUTO = "auto"            # enhanced, für 0x3C/0x3D classic


def protected_id(frame_id: int) -> int:
    """Liefert die geschützte ID (6-Bit-ID + Paritätsbits P0/P1)."""
    if not 0 <= frame_id <= 0x3F:
        raise ValueError(f"LIN-ID 0x{frame_id:02X} außerhalb 0x00..0x3F")
    bit = [(frame_id >> i) & 1 for i in range(6)]
    p0 = bit[0] ^ bit[1] ^ bit[2] ^ bit[4]
    p1 = 1 ^ (bit[1] ^ bit[3] ^ bit[4] ^ bit[5])
    return frame_id | (p0 << 6) | (p1 << 7)


def frame_id_of(pid: int) -> int:
    return pid & 0x3F


def parity_ok(pid: int) -> bool:
    return protected_id(pid & 0x3F) == pid


def checksum(data: Iterable[int], pid: Optional[int] = None) -> int:
    """LIN-Prüfsumme. Mit ``pid`` enhanced, ohne classic."""
    total = pid if pid is not None else 0
    for byte in data:
        total += byte
        if total > 0xFF:
            total -= 0xFF
    return (~total) & 0xFF


def resolve_checksum_type(frame_id: int, kind: ChecksumType) -> ChecksumType:
    kind = ChecksumType(kind)
    if kind is ChecksumType.AUTO:
        return ChecksumType.CLASSIC if frame_id in CLASSIC_ONLY_IDS else ChecksumType.ENHANCED
    return kind


def frame_checksum(frame_id: int, data: bytes, kind: ChecksumType = ChecksumType.AUTO) -> int:
    kind = resolve_checksum_type(frame_id, kind)
    pid = protected_id(frame_id)
    return checksum(data, pid if kind is ChecksumType.ENHANCED else None)


def detect_checksum_type(pid: int, data: bytes, value: int) -> Optional[ChecksumType]:
    """Ermittelt, welche Prüfsummenart zu ``value`` passt (None = keine)."""
    if checksum(data, pid) == value:
        return ChecksumType.ENHANCED
    if checksum(data) == value:
        return ChecksumType.CLASSIC
    return None


def baud_to_brg(baudrate: int) -> int:
    if baudrate <= 0:
        raise ValueError("Baudrate muss positiv sein")
    brg = round(FOSC_HZ / baudrate / 4) - 1
    if not 0 <= brg <= 0xFFFF:
        raise ValueError(f"Baudrate {baudrate} nicht einstellbar")
    return brg


def brg_to_baud(brg: int) -> int:
    return round(FOSC_HZ / (4 * (brg + 1)))

"""USB-HID-Zugriff. ``Transport`` lässt sich für Tests ersetzen."""

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .constants import PRODUCT_IDS, REPORT_SIZE, VENDOR_ID
from .exceptions import DeviceNotFoundError, TransportError


class Transport:
    """Schnittstelle: 64-Byte-Reports ohne Report-ID schreiben und lesen."""

    def write(self, report: bytes) -> None:
        raise NotImplementedError

    def read(self, timeout: float) -> Optional[bytes]:
        """Ein Report oder ``None`` nach ``timeout`` Sekunden."""
        raise NotImplementedError

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class DeviceInfo:
    path: bytes
    vendor_id: int
    product_id: int
    serial_number: str
    product: str
    manufacturer: str

    def __str__(self) -> str:
        name = self.product or "?"
        serial = f" SN={self.serial_number}" if self.serial_number else ""
        return f"{self.vendor_id:04X}:{self.product_id:04X} {name}{serial} ({self.path.decode(errors='replace')})"


def _hid():
    try:
        import hid  # Paket "hidapi"
    except ImportError as exc:
        raise TransportError("Python-Paket 'hidapi' fehlt (pip install hidapi)") from exc
    return hid


def list_devices(product_ids: Sequence[int] = PRODUCT_IDS) -> List[DeviceInfo]:
    hid = _hid()
    found = []
    for pid in product_ids:
        for info in hid.enumerate(VENDOR_ID, pid):
            found.append(DeviceInfo(
                path=info["path"],
                vendor_id=info["vendor_id"],
                product_id=info["product_id"],
                serial_number=info.get("serial_number") or "",
                product=info.get("product_string") or "",
                manufacturer=info.get("manufacturer_string") or "",
            ))
    return found


class HidTransport(Transport):
    def __init__(self, path: bytes):
        hid = _hid()
        self._dev = hid.device()
        try:
            self._dev.open_path(path)
        except (OSError, IOError) as exc:
            raise TransportError(
                f"Gerät {path!r} lässt sich nicht öffnen: {exc}. "
                "Unter Linux udev-Regel prüfen (siehe README)."
            ) from exc
        self.path = path

    @classmethod
    def open(cls, serial_number: Optional[str] = None, index: int = 0,
             product_ids: Sequence[int] = PRODUCT_IDS) -> "HidTransport":
        devices = list_devices(product_ids)
        if serial_number is not None:
            devices = [d for d in devices if d.serial_number == serial_number]
        if len(devices) <= index:
            what = f" mit Seriennummer {serial_number}" if serial_number else ""
            raise DeviceNotFoundError(f"Kein LIN Serial Analyzer{what} gefunden")
        return cls(devices[index].path)

    def write(self, report: bytes) -> None:
        if len(report) != REPORT_SIZE:
            raise ValueError(f"Report muss {REPORT_SIZE} Byte haben")
        try:
            written = self._dev.write(b"\x00" + bytes(report))
        except (OSError, IOError, ValueError) as exc:
            raise TransportError(f"Schreiben fehlgeschlagen: {exc}") from exc
        if written < 0:
            raise TransportError("Schreiben fehlgeschlagen (Gerät getrennt?)")

    def read(self, timeout: float) -> Optional[bytes]:
        try:
            data = self._dev.read(REPORT_SIZE, max(1, int(timeout * 1000)))
        except (OSError, IOError, ValueError) as exc:
            raise TransportError(f"Lesen fehlgeschlagen: {exc}") from exc
        if not data:
            return None
        return bytes(data)

    def close(self) -> None:
        try:
            self._dev.close()
        except Exception:
            pass

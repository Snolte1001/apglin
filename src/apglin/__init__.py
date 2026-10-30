"""Python-Bibliothek für den Microchip APGDT001 LIN Serial Analyzer."""

from .analyzer import LinAnalyzer
from .exceptions import (
    ApgLinError,
    ConfigurationError,
    DeviceNotFoundError,
    DeviceTimeoutError,
    ProtocolError,
    TransportError,
)
from .frames import FrameError, LinFrame
from .lin import ChecksumType, checksum, frame_checksum, parity_ok, protected_id
from .protocol import ControlBlock, DeviceStatus
from .transport import DeviceInfo, HidTransport, Transport, list_devices

__version__ = "0.1.0"

__all__ = [
    "LinAnalyzer",
    "LinFrame",
    "FrameError",
    "ChecksumType",
    "ControlBlock",
    "DeviceStatus",
    "DeviceInfo",
    "Transport",
    "HidTransport",
    "list_devices",
    "protected_id",
    "parity_ok",
    "checksum",
    "frame_checksum",
    "ApgLinError",
    "ConfigurationError",
    "DeviceNotFoundError",
    "DeviceTimeoutError",
    "ProtocolError",
    "TransportError",
]

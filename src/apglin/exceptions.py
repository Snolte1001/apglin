class ApgLinError(Exception):
    """Basisklasse aller Fehler dieser Bibliothek."""


class DeviceNotFoundError(ApgLinError):
    pass


class TransportError(ApgLinError):
    """USB/HID-Zugriff fehlgeschlagen oder Gerät getrennt."""


class ProtocolError(ApgLinError):
    """Unerwartete oder fehlerhafte Antwort des Geräts."""


class DeviceTimeoutError(ApgLinError):
    """Das Gerät hat nicht rechtzeitig geantwortet."""


class ConfigurationError(ApgLinError):
    """Der Control Block wurde nicht wie geschrieben übernommen."""

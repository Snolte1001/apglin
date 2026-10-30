"""Bedienteil Radio Audi 8W1 919 616 (Dreh-Drück-Knopf mit Kippfunktion).

Der Analyzer fragt als Master ID 0x10 ab und gibt Drehungen und Tasten aus.
Belegung siehe docs/AUDI_8W1919616.md (an einem Gerät gemessen).

    python examples/audi_8w1919616.py
"""

import time
from dataclasses import dataclass

FRAME_ID = 0x10
BAUDRATE = 19200
POLL_PAUSE_S = 0.02

PRESSED = 0x01
TILT_LEFT = 0x04
TILT_RIGHT = 0x08
KNOWN_BUTTONS = PRESSED | TILT_LEFT | TILT_RIGHT


@dataclass(frozen=True)
class KnobState:
    rotation: int        # Rasten seit der letzten Abfrage, + rechts, - links
    buttons: int         # Byte 1 roh

    @property
    def pressed(self) -> bool:
        return bool(self.buttons & PRESSED)

    @property
    def tilt_left(self) -> bool:
        return bool(self.buttons & TILT_LEFT)

    @property
    def tilt_right(self) -> bool:
        return bool(self.buttons & TILT_RIGHT)

    @property
    def unknown_buttons(self) -> int:
        return self.buttons & ~KNOWN_BUTTONS & 0xFF


def decode(data: bytes) -> KnobState:
    """Zwei Datenbytes von ID 0x10 → Zustand."""
    if len(data) != 2:
        raise ValueError(f"2 Datenbytes erwartet, {len(data)} erhalten")
    rotation = data[0] - 0x100 if data[0] & 0x80 else data[0]
    return KnobState(rotation=rotation, buttons=data[1])


def button_names(buttons: int) -> str:
    names = [name for bit, name in ((PRESSED, "gedrückt"), (TILT_LEFT, "links gekippt"),
                                    (TILT_RIGHT, "rechts gekippt")) if buttons & bit]
    if buttons & ~KNOWN_BUTTONS & 0xFF:
        names.append(f"unbekannt 0x{buttons & ~KNOWN_BUTTONS & 0xFF:02X}")
    return ", ".join(names) or "losgelassen"


def main() -> None:
    from apglin import LinAnalyzer

    with LinAnalyzer.open() as lin:
        lin.configure_lin(BAUDRATE)
        buttons = 0
        position = 0

        def on_frame(frame):
            nonlocal buttons, position
            if frame.frame_id != FRAME_ID or not frame.ok:
                return
            state = decode(frame.data)
            if state.rotation:
                position += state.rotation
                print(f"{frame.timestamp:9.3f} s  Drehung {state.rotation:+d}  (Summe {position:+d})")
            if state.buttons != buttons:
                buttons = state.buttons
                print(f"{frame.timestamp:9.3f} s  {button_names(buttons)}")

        lin.add_listener(on_frame)
        print("Strg+C beendet.")
        try:
            while True:
                # Nächsten Header erst nach dem vorigen senden und nie
                # nachholen: zwei Header direkt hintereinander zerstören die
                # Antwort des Slaves.
                lin.send_header(FRAME_ID, wait=True)
                time.sleep(POLL_PAUSE_S)
                lin.clear_received()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

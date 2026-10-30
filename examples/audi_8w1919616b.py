"""Tastenfeld Audi 8W1 919 616 B (Tasten 1-8 mit Beleuchtung).

Das Feld hat einen einzigen mechanischen Schalter; welche Ziffer gemeint
ist, erkennt es kapazitiv an der Fingerposition. Der Analyzer schaltet als
Master die Beleuchtung (ID 0x01), fragt Position (0x2E) und Schalter (0x20)
ab und gibt Berühren und Drücken aus. Belegung siehe docs/AUDI_8W1919616B.md
(an einem Gerät gemessen).

    python examples/audi_8w1919616b.py [--licht PROZENT]
"""

import argparse
import time
from dataclasses import dataclass
from typing import List, Tuple

BAUDRATE = 19200
ILLUMINATION_ID = 0x01   # Master → Slave: Helligkeit in Prozent, 1 Byte
STATUS_ID = 0x20         # Slave → Master: Position unentprellt, Schalter, Response_Error
STRENGTH_IDS = (0x2A, 0x2B)   # je 4 Werte, 16 Bit mit Vorzeichen, Big Endian
RAW_IDS = (0x2C, 0x2D)        # je 4 Werte, 16 Bit ohne Vorzeichen, Big Endian
KEYS_ID = 0x2E           # Slave → Master: berührte Taste(n), entprellt (Bitmaske, Nummer)
POLL_PAUSE_S = 0.02

STATUS_SWITCH = 0x10          # Byte 1 von 0x20: mechanischer Schalter gedrückt
STATUS_RESPONSE_ERROR = 0x40  # Byte 1 von 0x20


def keys_from_mask(mask: int) -> List[int]:
    """Bitmaske → Tastennummern (Bit 0 = Taste 1)."""
    return [i + 1 for i in range(8) if mask & (1 << i)]


@dataclass(frozen=True)
class Keys:
    """ID 0x2E: entprellt berührte Taste(n); Drücken zeigt nur 0x20 (Schalter).

    Beim Rutschen von einer Taste zur nächsten sind kurz zwei Bits gesetzt,
    ``number`` bleibt dann auf der vorigen Taste."""
    mask: int
    number: int

    @property
    def pressed(self) -> List[int]:
        return keys_from_mask(self.mask)


def decode_keys(data: bytes) -> Keys:
    if len(data) != 8:
        raise ValueError(f"8 Datenbytes erwartet, {len(data)} erhalten")
    return Keys(mask=data[0], number=data[1])


@dataclass(frozen=True)
class Status:
    """ID 0x20: schneller, unentprellter Zustand.

    Wechselt fast mit jeder Antwort zwischen der Fingerposition (Maske und
    Nummer) und einem Inhalt mit Maske 0, dessen Wert vermutlich eine
    Annäherungsstufe ist. ``switch`` steht in beiden."""
    mask: int
    value: int            # mit Maske: Tastennummer, sonst 0-15 (Annäherung, Deutung)
    switch: bool          # mechanischer Schalter
    response_error: bool

    @property
    def invalid(self) -> bool:
        """``FF xF``: alle Maskenbits gesetzt, Ursache unbekannt."""
        return self.mask == 0xFF

    @property
    def pressed(self) -> List[int]:
        return [] if self.invalid else keys_from_mask(self.mask)


def decode_status(data: bytes) -> Status:
    if len(data) != 2:
        raise ValueError(f"2 Datenbytes erwartet, {len(data)} erhalten")
    return Status(mask=data[0], value=data[1] & 0x0F, switch=bool(data[1] & STATUS_SWITCH),
                  response_error=bool(data[1] & STATUS_RESPONSE_ERROR))


def decode_words(data: bytes, signed: bool) -> Tuple[int, int, int, int]:
    """0x2A/0x2B (signed) bzw. 0x2C/0x2D: vier 16-Bit-Werte, Big Endian."""
    if len(data) != 8:
        raise ValueError(f"8 Datenbytes erwartet, {len(data)} erhalten")
    return tuple(int.from_bytes(data[i:i + 2], "big", signed=signed) for i in range(0, 8, 2))


def strengths(frame_2a: bytes, frame_2b: bytes) -> dict:
    """Signalstärke der Tasten 2-8 (Taste 1 hat keinen eigenen Wert: in 0x2A Wort 0
    stehen bei gedrückter Taste Maske und Nummer)."""
    a = decode_words(frame_2a, True)
    b = decode_words(frame_2b, True)
    return {2: a[1], 3: a[2], 4: a[3], 5: b[0], 6: b[1], 7: b[2], 8: b[3]}


def illumination_payload(percent: int) -> bytes:
    """Datenbyte für ID 0x01: 0 = aus, 100 = voll (größere Werte wirken wie 100)."""
    if not 0 <= percent <= 0xFF:
        raise ValueError("0..255")
    return bytes([percent])


def main() -> None:
    from apglin import ChecksumType, LinAnalyzer

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--licht", type=int, default=100,
                        help="Tastenbeleuchtung in Prozent (0 = aus), Standard 100")
    args = parser.parse_args()

    with LinAnalyzer.open() as lin:
        lin.configure_lin(BAUDRATE)
        lin.send_frame(ILLUMINATION_ID, illumination_payload(args.licht),
                       ChecksumType.ENHANCED, wait=True)
        touched: List[int] = []
        switch = False

        def on_frame(frame):
            nonlocal touched, switch
            if not frame.ok:
                return
            if frame.frame_id == KEYS_ID:
                keys = decode_keys(frame.data).pressed
                for key in keys:
                    if key not in touched:
                        print(f"{frame.timestamp:9.3f} s  Taste {key} berührt")
                for key in touched:
                    if key not in keys:
                        print(f"{frame.timestamp:9.3f} s  Taste {key} nicht mehr berührt")
                touched = keys
            elif frame.frame_id == STATUS_ID:
                status = decode_status(frame.data)
                if status.switch != switch:
                    switch = status.switch
                    where = f" auf Taste {', '.join(map(str, touched))}" if touched else ""
                    print(f"{frame.timestamp:9.3f} s  {'gedrückt' if switch else 'losgelassen'}{where}")

        lin.add_listener(on_frame)
        print("Strg+C beendet.")
        try:
            while True:
                # Header erst nach dem vorigen senden, nie nachholen
                for frame_id in (KEYS_ID, STATUS_ID):
                    lin.send_header(frame_id, wait=True)
                    time.sleep(POLL_PAUSE_S)
                lin.clear_received()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

"""Kommandozeile: ``apglin list|info|monitor|send|request``."""

import argparse
import logging
import sys
from typing import List, Optional

from . import __version__
from .analyzer import LinAnalyzer
from .constants import CommMode
from .exceptions import ApgLinError
from .lin import ChecksumType
from .transport import list_devices


def _int(text: str) -> int:
    return int(text, 0)


def _hexbytes(parts: List[str]) -> bytes:
    return bytes(int(p, 16) for p in " ".join(parts).replace(",", " ").split())


def _open(args) -> LinAnalyzer:
    return LinAnalyzer.open(serial_number=args.serial, index=args.index)


def _configure(lin: LinAnalyzer, args) -> None:
    if args.raw:
        lin.add_event_listener(lambda ev: print("  ", ev, flush=True))
    lin.configure_lin(args.baud, autobaud=args.autobaud)


def _mode_name(mode) -> str:
    try:
        return f"{mode} ({CommMode(mode).name})"
    except ValueError:
        return str(mode)


def cmd_list(args) -> int:
    devices = list_devices()
    for dev in devices:
        print(dev)
    if not devices:
        print("Kein Gerät gefunden.")
        return 1
    return 0


def cmd_info(args) -> int:
    with _open(args) as lin:
        st = lin.request_status()
        print(f"Firmware:      {st.firmware_version_str}")
        lin_mode = st.comm_mode == CommMode.LIN
        print(f"Modus:         {_mode_name(st.comm_mode)}")
        if st.control_block:
            cb = st.control_block
            print(f"Baudrate (CB): {cb.baudrate if lin_mode else '– (nicht im LIN-Modus)'}")
            print(f"Autobaud:      {cb.autobaud}")
            print(f"Empfang:       {cb.receive}")
            print(f"Chip Select:   {cb.chip_select}")
            print(f"Control Block: {bytes(cb).hex(' ')}")
        print(f"Baudrate (SB): {st.active_baudrate if lin_mode else '–'}")
        print(f"Fehlercode:    0x{st.error_code:06X}")
        if st.buffers:
            print(f"Puffer:        {st.buffers}")
    return 0


def cmd_monitor(args) -> int:
    with _open(args) as lin:
        _configure(lin, args)
        print(f"Höre auf dem Bus ({'Autobaud' if args.autobaud else args.baud} Bd), Strg+C beendet.")
        try:
            while True:
                frame = lin.receive(timeout=0.5)
                if frame is not None:
                    print(frame, flush=True)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_send(args) -> int:
    data = _hexbytes(args.data)
    with _open(args) as lin:
        _configure(lin, args)
        for _ in range(args.repeat):
            lin.send_frame(args.id, data, ChecksumType(args.checksum))
            frame = lin.receive(timeout=1.0)
            print(frame if frame is not None else "Kein Echo empfangen (VBAT angeschlossen?)", flush=True)
    return 0


def cmd_request(args) -> int:
    with _open(args) as lin:
        _configure(lin, args)
        print(lin.request(args.id, timeout=args.timeout))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apglin", description="Microchip APGDT001 LIN Serial Analyzer")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--serial", help="Seriennummer des Geräts")
    p.add_argument("--index", type=int, default=0, help="Gerätenummer bei mehreren Geräten")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="angeschlossene Geräte auflisten").set_defaults(func=cmd_list)
    sub.add_parser("info", help="Firmware und Konfiguration anzeigen").set_defaults(func=cmd_info)

    def bus(sp):
        sp.add_argument("-b", "--baud", type=int, default=19200)
        sp.add_argument("--autobaud", action="store_true")
        sp.add_argument("--raw", action="store_true", help="Rohereignisse des Geräts ausgeben")

    mon = sub.add_parser("monitor", help="Frames mitschneiden")
    bus(mon)
    mon.set_defaults(func=cmd_monitor)

    snd = sub.add_parser("send", help="Frame als Master senden")
    bus(snd)
    snd.add_argument("id", type=_int, help="LIN-ID 0x00..0x3F")
    snd.add_argument("data", nargs="*", help="Datenbytes hex, z. B. 01 02 FF")
    snd.add_argument("-c", "--checksum", choices=[t.value for t in ChecksumType], default="auto")
    snd.add_argument("-n", "--repeat", type=int, default=1)
    snd.set_defaults(func=cmd_send)

    req = sub.add_parser("request", help="Header senden, Slave-Antwort anzeigen")
    bus(req)
    req.add_argument("id", type=_int)
    req.add_argument("-t", "--timeout", type=float, default=1.0)
    req.set_defaults(func=cmd_request)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        return args.func(args)
    except ApgLinError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

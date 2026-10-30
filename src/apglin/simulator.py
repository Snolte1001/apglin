"""Simuliertes Gerät für Tests und Dummy-Betrieb ohne Hardware.

Bildet das Protokoll so nach, wie es in docs/PROTOKOLL.md beschrieben ist.
Es ersetzt keinen Test an echter Hardware.
"""

import queue
import threading
import time
from typing import Callable, Dict, List, Optional

from . import constants as c
from .lin import ChecksumType, baud_to_brg, frame_checksum, frame_id_of, protected_id
from .transport import Transport

SIM_FIRMWARE_VERSION = 0x0305


class SimulatedTransport(Transport):
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._out: "queue.Queue[bytes]" = queue.Queue()
        self.control_block = bytearray(c.CONTROL_BLOCK_SIZE)
        self.status_block = bytearray(c.STATUS_BLOCK_SIZE)
        # Puffergrößen wie am echten Gerät gemessen (Firmware 0x2304)
        self.cbuf1_size = 40
        self.cbuf2_size = 128
        self.cbuf3 = bytearray()
        self.cbuf3_size = 40
        self.slave_profiles: Dict[int, bytes] = {}
        # Slave-Knoten am Bus: ID -> Antwortdaten (ohne Prüfsumme)
        self.responders: Dict[int, bytes] = {}
        self.leds = {1: 0, 2: 0}
        self.written: List[bytes] = []
        # Ereignisse, die das Gerät noch aus der Zeit vor einem Warmstart
        # nachliefert (z. B. Zeitüberläufe, während niemand gelesen hat)
        self.stale_events = b""
        self.closed = False
        self._script = bytearray()
        self._packet_id = 0
        self._t0 = clock()

    # --- Transport ---------------------------------------------------------

    def write(self, report: bytes) -> None:
        assert len(report) == c.REPORT_SIZE
        with self._lock:
            self.written.append(bytes(report))
            self._handle(report)

    def read(self, timeout: float) -> Optional[bytes]:
        try:
            return self._out.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.closed = True

    # --- Bus von außen -----------------------------------------------------

    def inject_frame(self, frame_id: int, data: bytes,
                     checksum: ChecksumType = ChecksumType.AUTO) -> None:
        """Ein fremder Master sendet einen kompletten Frame."""
        chk = frame_checksum(frame_id, bytes(data), checksum)
        with self._lock:
            self._bus_frame(protected_id(frame_id), bytes(data) + bytes([chk]))

    def inject_events(self, raw: bytes) -> None:
        """Rohe CBUF2-Ereignisse einspeisen."""
        with self._lock:
            self._emit_stream(bytes(raw))

    # --- Geräteverhalten ---------------------------------------------------

    def _report(self, payload: bytes) -> None:
        self._out.put(bytes(payload) + bytes(c.REPORT_SIZE - len(payload)))

    def _status(self) -> None:
        self._packet_id = (self._packet_id + 1) & 0xFF
        used = len(self._script)
        payload = bytes([c.Group.PACKET_ID, self._packet_id])
        payload += bytes([c.Group.FIRMWARE_VERSION, SIM_FIRMWARE_VERSION & 0xFF, SIM_FIRMWARE_VERSION >> 8])
        payload += bytes([c.Group.CONTROL_BLOCK]) + bytes(self.control_block)
        payload += bytes([c.Group.STATUS_BLOCK]) + bytes(self.status_block)
        payload += bytes([c.Group.CBUF_STATUS, used, self.cbuf1_size - used, 0, self.cbuf2_size,
                          len(self.cbuf3), self.cbuf3_size - len(self.cbuf3)])
        payload += bytes([c.Group.END])
        self._report(payload)

    def _apply_control_block(self) -> None:
        cb = self.control_block
        self.status_block[c.SB_COMM_MODE] = cb[c.CB_COMM_MODE]
        self.status_block[c.SB_BAUD_LO] = cb[c.CB_BRG_LO]
        self.status_block[c.SB_BAUD_HI] = cb[c.CB_BRG_HI]

    def _handle(self, r: bytes) -> None:
        cmd = r[0]
        if cmd == c.Command.CONTROL:
            sub = r[1]
            if sub == c.Control.STATUS_REQUEST:
                self._status()
            elif sub == c.Control.WARM_RESET:
                self._apply_control_block()
                self._t0 = self._clock()
                if self.stale_events:
                    self._emit_stream(self.stale_events)
                    self.stale_events = b""
            elif sub == c.Control.COMM_CLEAR:
                for i in (c.SB_ERROR_0, c.SB_ERROR_1, c.SB_ERROR_2):
                    self.status_block[i] = 0
        elif cmd == c.Command.SPECIAL_STATUS and r[1] == c.SPECIAL_STATUS_KEY:
            self._status()
        elif cmd == c.Command.WRITE_CONTROL_BLOCK:
            self.control_block[:] = r[1:1 + c.CONTROL_BLOCK_SIZE]
            self._apply_control_block()
        elif cmd == c.Command.WRITE_CBUF1:
            self._script += r[2:2 + r[1]]
            self._run_script()
        elif cmd == c.Command.WRITE_CBUF3:
            self.cbuf3 += r[2:2 + r[1]]
        elif cmd == c.Command.CLEAR_CBUF1:
            self._script.clear()
        elif cmd == c.Command.CLEAR_CBUF3:
            self.cbuf3.clear()

    def _run_script(self) -> None:
        s = self._script
        while s:
            op = s[0]
            if op == c.Script.LIN_SEND:
                if len(s) < 2 or len(s) < 2 + s[1]:
                    return
                n = s[1]
                pid, payload = s[2], bytes(s[3:2 + n])
                del s[:2 + n]
                if not payload and frame_id_of(pid) in self.responders:
                    answer = self.responders[frame_id_of(pid)]
                    payload = answer + bytes([frame_checksum(frame_id_of(pid), answer)])
                self._bus_frame(pid, payload)
            elif op in (c.Script.MARKER, c.Script.LED1, c.Script.LED2):
                if len(s) < 2:
                    return
                if op == c.Script.MARKER:
                    self._emit_stream(bytes([c.Event.MARKER, s[1]]))
                else:
                    self.leds[1 if op == c.Script.LED1 else 2] = s[1]
                del s[:2]
            elif op == c.Script.EVENT_TIMER_RESET:
                self._t0 = self._clock()
                del s[:1]
            elif op == c.Script.LIN_WRITE_SLAVE_PROFILE:
                if len(s) < 3 or len(s) < 3 + s[2]:
                    return
                self.slave_profiles[s[1]] = bytes(s[3:3 + s[2]])
                del s[:3 + s[2]]
            elif op == c.Script.LIN_READ_SLAVE_PROFILE:
                if len(s) < 2:
                    return
                data = self.slave_profiles.get(s[1], b"")
                self._emit_stream(bytes([c.Event.LIN_SLAVE_PROFILE_DATA, s[1], len(data)]) + data)
                del s[:2]
            else:
                s.clear()

    def _bus_frame(self, pid: int, payload: bytes) -> None:
        if not self.control_block[c.CB_LIN_FLAGS] & c.LIN_FLAG_RECEIVE:
            return
        # Wie Firmware 0x2304: kein LIN_BREAK_TX, vor jedem Byte ein Zeitstempel.
        ticks = int((self._clock() - self._t0) / c.EVENT_TICK_S)
        events = bytearray([c.Event.LIN_BREAK_RX])
        for byte in bytes([pid]) + payload:
            t = ticks & 0xFFFF
            events += bytes([c.Event.TIME, t & 0xFF, t >> 8, c.Event.DATA_BYTE, byte])
            ticks += 1
        self._emit_stream(bytes(events))

    def _emit_stream(self, events: bytes) -> None:
        for start in range(0, len(events), 62):
            chunk = events[start:start + 62]
            self._report(bytes([c.Group.CBUF2_DATA, len(chunk)]) + chunk)

    # Hilfen für Tests
    def set_baudrate(self, baudrate: int) -> None:
        brg = baud_to_brg(baudrate)
        self.status_block[c.SB_BAUD_LO] = brg & 0xFF
        self.status_block[c.SB_BAUD_HI] = brg >> 8

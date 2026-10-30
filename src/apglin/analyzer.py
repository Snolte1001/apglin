"""Hauptklasse ``LinAnalyzer``: Konfiguration, Senden, Empfangen."""

import collections
import dataclasses
import logging
import queue
import threading
import time
from typing import Callable, Iterator, List, Optional

from . import constants as c
from . import protocol
from .exceptions import ConfigurationError, DeviceTimeoutError, ProtocolError, TransportError
from .frames import FrameAssembler, LinFrame
from .lin import ChecksumType, frame_checksum, protected_id
from .protocol import BufferStatus, ControlBlock, DeviceStatus
from .stream import StreamEvent, StreamParser
from .transport import HidTransport, Transport

log = logging.getLogger(__name__)

SENT_MATCH_WINDOW_S = 2.0

FrameListener = Callable[[LinFrame], None]
EventListener = Callable[[StreamEvent], None]


class LinAnalyzer:
    """Steuert einen APGDT001 LIN Serial Analyzer.

    Ein Hintergrund-Thread liest ständig vom Gerät. Empfangene Frames landen
    in einer Warteschlange (``receive``/``frames``) und gehen zusätzlich an
    registrierte Listener. Listener laufen im Lesethread und dürfen nicht
    blockieren.
    """

    def __init__(self, transport: Transport, *, frame_timeout: float = 0.1,
                 response_timeout: float = 2.0, queue_size: int = 10000):
        self._transport = transport
        self.response_timeout = response_timeout

        self._write_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._script_lock = threading.Lock()
        self._rx_lock = threading.Lock()
        self._status_event = threading.Event()
        self._marker_cond = threading.Condition()
        self._marker_count = 0
        self._profile_cond = threading.Condition()
        self._profiles: dict = {}

        self._firmware_version: Optional[int] = None
        self._control_block: Optional[ControlBlock] = None
        self._status_block: Optional[bytes] = None
        self._buffers: Optional[BufferStatus] = None
        self._cbuf1_free = 0

        self._parser = StreamParser()
        self._assembler = FrameAssembler(timeout=frame_timeout)
        self._frames: "queue.Queue[LinFrame]" = queue.Queue(maxsize=queue_size)
        self._listeners: List[FrameListener] = []
        self._event_listeners: List[EventListener] = []
        self.dropped_frames = 0
        self.protocol_errors = 0
        # Eigene Sendungen (Zeitpunkt, PID, Daten inkl. Prüfsumme), um das
        # Loopback-Echo als ``transmitted`` zu kennzeichnen. Die Firmware
        # meldet kein „Break gesendet“.
        self._sent: "collections.deque" = collections.deque()
        self._sent_lock = threading.Lock()
        # Nach einem Reset alte Ereignisse verwerfen, bis die nächste
        # Statusantwort kommt (Gerät puffert z. B. Zeitüberläufe, solange
        # niemand liest).
        self._discard_stream = False
        self.discarded_stream_bytes = 0

        self._error: Optional[BaseException] = None
        self._running = True
        self._thread = threading.Thread(target=self._reader, name="apglin-reader", daemon=True)
        self._thread.start()

    # --- Lebenszyklus ------------------------------------------------------

    @classmethod
    def open(cls, serial_number: Optional[str] = None, index: int = 0, **kwargs) -> "LinAnalyzer":
        """Öffnet das erste (bzw. ``index``-te) gefundene Gerät."""
        return cls(HidTransport.open(serial_number=serial_number, index=index), **kwargs)

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)
        self._transport.close()

    def __enter__(self) -> "LinAnalyzer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._running and self._error is None

    # --- Lesethread --------------------------------------------------------

    def _reader(self) -> None:
        while self._running:
            try:
                report = self._transport.read(0.05)
            except TransportError as exc:
                log.error("Lesethread beendet: %s", exc)
                self._error = exc
                self._running = False
                self._wake_all()
                return
            except Exception as exc:  # pragma: no cover - Schutz des Threads
                log.exception("Unerwarteter Fehler im Lesethread")
                self._error = exc
                self._running = False
                self._wake_all()
                return
            with self._rx_lock:
                if report:
                    self._handle_report(report)
                self._dispatch(self._assembler.poll())

    def _wake_all(self) -> None:
        self._status_event.set()
        with self._marker_cond:
            self._marker_cond.notify_all()
        with self._profile_cond:
            self._profile_cond.notify_all()

    def _handle_report(self, report: bytes) -> None:
        try:
            groups = protocol.parse_report(report)
        except ProtocolError as exc:
            self.protocol_errors += 1
            log.warning("Report verworfen: %s (%s)", exc, report.hex(" "))
            return
        status_seen = False
        for tag, payload in groups:
            if tag == c.Group.FIRMWARE_VERSION:
                self._firmware_version = payload[0] | (payload[1] << 8)
            elif tag == c.Group.CONTROL_BLOCK:
                self._control_block = ControlBlock(payload)
            elif tag == c.Group.STATUS_BLOCK:
                self._status_block = payload
            elif tag == c.Group.CBUF_STATUS:
                self._buffers = BufferStatus.from_bytes(payload)
                status_seen = True
                if self._discard_stream:
                    self._discard_stream = False
                    self._parser.reset()
                    self._assembler.reset_time()
            elif tag == c.Group.CBUF2_DATA:
                if self._discard_stream:
                    self.discarded_stream_bytes += len(payload)
                    log.debug("Alte Ereignisse verworfen: %s", payload.hex(" "))
                else:
                    self._handle_stream(payload)
        if status_seen:
            self._status_event.set()

    def _handle_stream(self, payload: bytes) -> None:
        events = self._parser.feed(payload)
        for ev in events:
            for listener in list(self._event_listeners):
                self._call(listener, ev)
            if ev.tag == c.Event.MARKER and ev.args[0] == c.MARKER_SCRIPT_DONE:
                with self._marker_cond:
                    self._marker_count += 1
                    self._marker_cond.notify_all()
            elif ev.tag == c.Event.LIN_SLAVE_PROFILE_DATA:
                with self._profile_cond:
                    self._profiles[ev.args[0]] = bytes(ev.args[2:2 + ev.args[1]])
                    self._profile_cond.notify_all()
        self._dispatch(self._assembler.feed(events))

    def _mark_own(self, frame: LinFrame) -> LinFrame:
        raw = frame.data + (bytes([frame.checksum]) if frame.checksum is not None else b"")
        now = time.monotonic()
        with self._sent_lock:
            while self._sent and now - self._sent[0][0] > SENT_MATCH_WINDOW_S:
                self._sent.popleft()
            for entry in self._sent:
                if entry[1] == frame.pid and entry[2] == raw:
                    self._sent.remove(entry)
                    return dataclasses.replace(frame, transmitted=True)
        return frame

    def _dispatch(self, frames: List[LinFrame]) -> None:
        for frame in frames:
            if not frame.transmitted:
                frame = self._mark_own(frame)
            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                self.dropped_frames += 1
                try:
                    self._frames.get_nowait()
                    self._frames.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass
            for listener in list(self._listeners):
                self._call(listener, frame)

    @staticmethod
    def _call(listener, arg) -> None:
        try:
            listener(arg)
        except Exception:
            log.exception("Listener %r hat eine Ausnahme ausgelöst", listener)

    # --- Senden/Status (intern) --------------------------------------------

    def _check(self) -> None:
        if self._error is not None:
            raise TransportError(f"Verbindung zum Gerät verloren: {self._error}")
        if not self._running:
            raise TransportError("Analyzer ist geschlossen")

    def _write(self, report: bytes) -> None:
        self._check()
        with self._write_lock:
            self._transport.write(report)

    def _request(self, report: bytes, timeout: Optional[float]) -> DeviceStatus:
        timeout = self.response_timeout if timeout is None else timeout
        with self._status_lock:
            self._status_event.clear()
            self._write(report)
            if not self._status_event.wait(timeout):
                raise DeviceTimeoutError("Keine Statusantwort vom Gerät")
            self._check()
            return self.last_status

    def _control(self, sub: c.Control) -> None:
        self._write(protocol.control(sub))

    def _run_script(self, script: bytes, wait: bool, timeout: Optional[float]) -> None:
        timeout = self.response_timeout if timeout is None else timeout
        with self._script_lock:
            self._reserve_cbuf1(len(script))
            with self._marker_cond:
                start = self._marker_count
            for report in protocol.write_script(script):
                self._write(report)
            if not wait:
                return
            deadline = time.monotonic() + timeout
            with self._marker_cond:
                while self._marker_count == start:
                    self._check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DeviceTimeoutError("Skript wurde nicht als ausgeführt gemeldet")
                    self._marker_cond.wait(remaining)

    def _reserve_cbuf1(self, size: int) -> None:
        """Wartet, bis Skriptpuffer 1 genug Platz hat (wie die Microchip-DLL)."""
        if size < self._cbuf1_free:
            self._cbuf1_free -= size
            return
        for _ in range(6):
            status = self._request(protocol.special_status_request(), None)
            free = status.buffers.cbuf1_free if status.buffers else 0
            if size < free:
                self._cbuf1_free = free - size
                return
            time.sleep(0.1)
        raise DeviceTimeoutError("Skriptpuffer 1 des Geräts bleibt voll")

    # --- Status und Grundbefehle -------------------------------------------

    @property
    def last_status(self) -> DeviceStatus:
        """Zuletzt empfangener Status (ohne neue Anfrage)."""
        return DeviceStatus(
            firmware_version=self._firmware_version,
            control_block=self._control_block.copy() if self._control_block else None,
            status_block=self._status_block,
            buffers=self._buffers,
        )

    def request_status(self, timeout: Optional[float] = None) -> DeviceStatus:
        return self._request(protocol.control(c.Control.STATUS_REQUEST), timeout)

    @property
    def firmware_version(self) -> int:
        version = self.request_status().firmware_version
        if version is None:
            raise ProtocolError("Gerät hat keine Firmwareversion gemeldet")
        return version

    def cold_reset(self) -> DeviceStatus:
        self._control(c.Control.COLD_RESET)
        return self.request_status()

    def warm_reset(self) -> DeviceStatus:
        """Setzt die Kommunikation zurück; die Gerätezeit beginnt wieder bei 0."""
        self._discard_stream = True
        self._control(c.Control.WARM_RESET)
        self._write(protocol.clear_cbuf(2))
        return self.request_status()

    def clear_errors(self) -> DeviceStatus:
        self._control(c.Control.COMM_CLEAR)
        return self.request_status()

    def comm_reset(self) -> DeviceStatus:
        self._discard_stream = True
        self._control(c.Control.COMM_RESET)
        self._control(c.Control.WARM_RESET)
        self._write(protocol.clear_cbuf(2))
        return self.request_status()

    def save_configuration(self) -> None:
        """Speichert den aktuellen Control Block im EEPROM des Geräts."""
        self._control(c.Control.CONTROL_BLOCK_TO_EEPROM)
        self.request_status()

    def _reset_time_base(self) -> None:
        with self._rx_lock:
            self._assembler.reset_time()

    def write_control_block(self, block: ControlBlock, *, reset: bool = False,
                            verify: bool = True) -> DeviceStatus:
        """Schreibt die Konfiguration; ``reset`` umrahmt sie mit Kalt-/Warmstart."""
        if reset:
            self._discard_stream = True
            self._control(c.Control.COLD_RESET)
        self._write(protocol.write_control_block(bytes(block)))
        if reset:
            self._control(c.Control.WARM_RESET)
            self._write(protocol.clear_cbuf(2))
        else:
            self._control(c.Control.COMM_CLEAR)
        status = self.request_status()
        if verify and status.control_block != block:
            raise ConfigurationError(
                "Control Block nicht übernommen:\n"
                f"  geschrieben: {bytes(block).hex(' ')}\n"
                f"  gelesen:     {bytes(status.control_block or b'').hex(' ')}"
            )
        return status

    def _current_control_block(self) -> ControlBlock:
        block = self.request_status().control_block
        if block is None:
            raise ProtocolError("Gerät hat keinen Control Block gemeldet")
        return block

    # --- LIN-Konfiguration -------------------------------------------------

    def configure_lin(self, baudrate: Optional[int] = 19200, *, autobaud: bool = False,
                      receive: bool = True, chip_select: bool = True,
                      flush_byte_count: int = 10, flush_interval_ms: float = 10.0) -> DeviceStatus:
        """Versetzt das Gerät in den LIN-Modus.

        ``baudrate=None`` behält den Werkswert. ``autobaud`` misst die
        Baudrate fremder Master am Sync-Byte. Die Flush-Werte (10 Byte /
        10 ms) entsprechen der Microchip-Demo TB3180 und halten die
        Empfangslatenz klein; der Werkswert ist 53 Byte / 50,7 ms.
        """
        block = ControlBlock.lin_default()
        block.autobaud = autobaud
        block.receive = receive
        block.chip_select = chip_select
        block.set_flush(True, True, flush_byte_count, flush_interval_ms)
        if baudrate is not None:
            block.baudrate = baudrate
        status = self.write_control_block(block, reset=True)
        self._assembler.default_baudrate = block.baudrate
        return status

    def set_baudrate(self, baudrate: int) -> DeviceStatus:
        block = self._current_control_block()
        block.baudrate = baudrate
        status = self.write_control_block(block)
        self._assembler.default_baudrate = block.baudrate
        return status

    @property
    def baudrate(self) -> int:
        """Eingestellte Baudrate laut Control Block."""
        return self._current_control_block().baudrate

    @property
    def active_baudrate(self) -> Optional[int]:
        """Aktive bzw. per Autobaud gemessene Baudrate laut Status Block."""
        return self.request_status().active_baudrate

    def set_options(self, *, autobaud: Optional[bool] = None, receive: Optional[bool] = None,
                    chip_select: Optional[bool] = None) -> DeviceStatus:
        block = self._current_control_block()
        if autobaud is not None:
            block.autobaud = autobaud
        if receive is not None:
            block.receive = receive
        if chip_select is not None:
            block.chip_select = chip_select
        return self.write_control_block(block)

    def set_flush(self, *, on_count: bool = True, on_time: bool = True,
                  byte_count: int = 10, interval_ms: float = 10.0) -> DeviceStatus:
        """Wann das Gerät gesammelte Ereignisse an den PC schickt.

        Kürzere Intervalle senken die Latenz beim Empfang.
        """
        block = self._current_control_block()
        block.set_flush(on_count, on_time, byte_count, interval_ms)
        return self.write_control_block(block)

    # --- Senden ------------------------------------------------------------

    def send_frame(self, frame_id: int, data: bytes,
                   checksum: ChecksumType = ChecksumType.AUTO, *,
                   wait: bool = False, timeout: Optional[float] = None) -> None:
        """Sendet einen kompletten Frame als Master (Header + Daten + Prüfsumme)."""
        data = bytes(data)
        if len(data) > 8:
            raise ValueError("Höchstens 8 Datenbytes")
        payload = data + bytes([frame_checksum(frame_id, data, checksum)])
        self.send_raw(protected_id(frame_id), payload, wait=wait, timeout=timeout)

    def send_header(self, frame_id: int, *, wait: bool = False,
                    timeout: Optional[float] = None) -> None:
        """Sendet nur den Header; ein Slave am Bus liefert die Antwort."""
        self.send_raw(protected_id(frame_id), b"", wait=wait, timeout=timeout)

    def send_raw(self, pid: int, payload: bytes = b"", *, wait: bool = False,
                 timeout: Optional[float] = None) -> None:
        """Break, Sync, ``pid`` und ``payload`` unverändert (Prüfsumme selbst anhängen).

        Für Fehlertests: falsche Parität oder Prüfsumme werden nicht korrigiert.
        """
        payload = bytes(payload)
        script = protocol.script_lin_send(pid, payload) + protocol.script_marker()
        if payload:
            # Nur komplette Frames: beim reinen Header stammen die Daten vom Slave.
            with self._sent_lock:
                self._sent.append((time.monotonic(), pid & 0xFF, payload))
        self._run_script(script, wait, timeout)

    def request(self, frame_id: int, timeout: Optional[float] = None) -> LinFrame:
        """Header senden und die Slave-Antwort als Frame zurückgeben."""
        timeout = self.response_timeout if timeout is None else timeout
        pid = protected_id(frame_id)
        answer: "queue.Queue[LinFrame]" = queue.Queue()

        def catch(frame: LinFrame) -> None:
            if frame.pid == pid:
                answer.put(frame)

        self.add_listener(catch)
        try:
            self.send_header(frame_id)
            try:
                return answer.get(timeout=timeout)
            except queue.Empty:
                self._check()
                raise DeviceTimeoutError(f"Keine Antwort auf ID 0x{frame_id:02X}") from None
        finally:
            self.remove_listener(catch)

    # --- Empfangen ---------------------------------------------------------

    def receive(self, timeout: Optional[float] = None) -> Optional[LinFrame]:
        """Nächster empfangener Frame oder ``None`` nach ``timeout``."""
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            self._check()
            return None

    def frames(self, timeout: Optional[float] = None) -> Iterator[LinFrame]:
        """Iteriert über empfangene Frames; endet nach ``timeout`` ohne Frame."""
        while True:
            frame = self.receive(timeout)
            if frame is None:
                return
            yield frame

    def clear_received(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return

    def add_listener(self, listener: FrameListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: FrameListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def add_event_listener(self, listener: EventListener) -> None:
        """Rohereignisse des Geräts, z. B. zur Fehlersuche."""
        self._event_listeners.append(listener)

    def remove_event_listener(self, listener: EventListener) -> None:
        try:
            self._event_listeners.remove(listener)
        except ValueError:
            pass

    # --- Slave-Profil (experimentell) ---------------------------------------

    def write_slave_profile_entry(self, pid: int, data: bytes) -> None:
        """Legt die Antwort für ``pid`` im Slave-Profil ab (experimentell)."""
        self._run_script(protocol.script_lin_write_slave_profile(pid, data), False, None)

    def read_slave_profile_entry(self, pid: int, timeout: Optional[float] = None) -> bytes:
        timeout = self.response_timeout if timeout is None else timeout
        with self._profile_cond:
            self._profiles.pop(pid & 0xFF, None)
        self._run_script(protocol.script_lin_read_slave_profile(pid)
                         + protocol.script_marker(), False, None)
        deadline = time.monotonic() + timeout
        with self._profile_cond:
            while (pid & 0xFF) not in self._profiles:
                self._check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DeviceTimeoutError(f"Slave-Profil für PID 0x{pid:02X} nicht gelesen")
                self._profile_cond.wait(remaining)
            return self._profiles.pop(pid & 0xFF)

    def load_slave_profile(self, raw: bytes) -> DeviceStatus:
        """Lädt ein komplettes Slave-Profil (Rohformat der Microchip-Software)
        in Puffer 3 und schaltet Slave-Antworten ein (experimentell)."""
        status = self._request(protocol.special_status_request(), None)
        capacity = (status.buffers.cbuf3_used + status.buffers.cbuf3_free) if status.buffers else 0
        if len(raw) > capacity:
            raise ValueError(f"Profil mit {len(raw)} Byte größer als Puffer 3 ({capacity} Byte)")
        self._write(protocol.clear_cbuf(3))
        block = self._current_control_block()
        block.slave_profile = True
        self._write(protocol.write_control_block(bytes(block)))
        for report in protocol.write_cbuf3(raw):
            self._write(report)
        status = self.request_status()
        if status.control_block != block:
            raise ConfigurationError("Slave-Profil-Freigabe nicht übernommen")
        return status

    def enable_slave_profile(self, enabled: bool) -> DeviceStatus:
        block = self._current_control_block()
        block.slave_profile = enabled
        return self.write_control_block(block)

    # --- Sonstiges ---------------------------------------------------------

    def set_led(self, led: int, value: int) -> None:
        self._run_script(protocol.script_led(led, value), False, None)

    def reset_timer(self) -> None:
        """Setzt die Gerätezeit (Frame-Zeitstempel) auf 0."""
        self._run_script(protocol.script_event_timer_reset(), False, None)
        self._reset_time_base()

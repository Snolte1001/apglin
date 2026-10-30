"""Abläufe am echten Gerät, deren Mitschnitte in ``tests/recordings`` liegen.

``test_recordings.py`` spielt die Mitschnitte mit denselben Funktionen ab.
Neu aufnehmen (APGDT001 mit VBAT):

    python tests/hw_scenarios.py [Name ...]

Abläufe ``slave_*`` brauchen den Slave vom 30.10.2026 am Bus (antwortet bei
19200 Bd nur auf ID 0x10 mit ``00 00``, schläft nach 4 s Busruhe), die
übrigen laufen mit und ohne ihn. ``live`` ist nur bei der Aufnahme gesetzt;
Pausen, die den Slave einschlafen lassen, entfallen beim Abspielen.
"""

import datetime
import sys
import time
from pathlib import Path

from apglin import ChecksumType, LinAnalyzer
from apglin import protocol
from apglin.lin import frame_checksum, protected_id

RECORDINGS = Path(__file__).with_name("recordings")
SCENARIOS = {}


SLAVE_ID = 0x10
SLAVE_SLEEP_S = 5.0


def scenario(func):
    SCENARIOS[func.__name__] = func
    return func


def _frames(lin, timeout=0.3):
    return list(lin.frames(timeout))


@scenario
def loopback_19200(lin, live=False):
    lin.configure_lin(19200)
    lin.send_frame(0x0C, bytes.fromhex("110400000000C000"), wait=True)
    return {"frames": _frames(lin)}


@scenario
def classic_9600(lin, live=False):
    lin.configure_lin(9600)
    lin.send_frame(0x3C, bytes(range(1, 9)), wait=True)
    return {"frames": _frames(lin), "status": lin.request_status()}


@scenario
def request_no_slave(lin, live=False):
    lin.configure_lin(19200)
    return {"answer": lin.request(0x21, timeout=0.5)}


@scenario
def slave_responses(lin, live=False):
    lin.configure_lin(19200)
    status = lin.set_slave_responses({
        0x21: b"\x01\x02\x03",
        0x22: b"\x11\x22",
        0x3C: bytes(range(0x11, 0x19)),
    })
    answers = {i: lin.request(i, timeout=0.5) for i in (0x21, 0x22, 0x3C, 0x10)}
    lin.enable_slave_profile(False)
    off = lin.request(0x21, timeout=0.5)
    return {"status": status, "answers": answers, "off": off}


@scenario
def slave_profile_entries(lin, live=False):
    lin.configure_lin(19200)
    pid = protected_id(0x21)
    # Gerät rechnet die Prüfsumme (classic); 0xB3 = zusätzlich Bit „enhanced“
    raw = (protocol.slave_profile_entry(pid, b"\x01\x02\x03", device_checksum=True)
           + bytes([0xB3, protected_id(0x22), 0x01, 0x02, 0x03]))
    lin.load_slave_profile(raw)
    result = {
        "device_checksum": lin.request(0x21, timeout=0.5),
        "enhanced_bit": lin.request(0x22, timeout=0.5),
        "read": lin.read_slave_profile_entry(pid),
        "read_frame_id": lin.read_slave_profile_entry(0x21),
    }
    lin.write_slave_profile_entry(pid, b"\x0A\x0B\x0C\x0D")
    time.sleep(0.05)
    result["read_after_write"] = lin.read_slave_profile_entry(pid)
    result["answer_after_write"] = lin.request(0x21, timeout=0.5)
    lin.write_slave_profile_entry(protected_id(0x10), b"\x99")
    time.sleep(0.05)
    result["read_unknown"] = lin.read_slave_profile_entry(protected_id(0x10))
    lin.enable_slave_profile(False)
    return result


@scenario
def warm_reset_clears_profile(lin, live=False):
    lin.configure_lin(19200)
    lin.set_slave_responses({0x21: b"\x01"})
    before = lin.request(0x21, timeout=0.5)
    status = lin.warm_reset()
    after = lin.request(0x21, timeout=0.5)
    lin.enable_slave_profile(False)
    return {"before": before, "status": status, "after": after}


@scenario
def autobaud_loopback(lin, live=False):
    events = []
    lin.add_event_listener(events.append)
    lin.configure_lin(9600, autobaud=True)
    lin.send_frame(0x0C, b"\x01\x02\x03\x04", wait=True)
    frames = _frames(lin)
    return {"frames": frames, "status": lin.request_status(), "events": events}


@scenario
def slave_scan(lin, live=False):
    lin.configure_lin(19200)
    if live:
        time.sleep(SLAVE_SLEEP_S)
    # erster Header weckt den Slave, erst die folgenden werden beantwortet
    ids = (SLAVE_ID, 0x0F, SLAVE_ID, 0x11, 0x3D, SLAVE_ID)
    return {"answers": [lin.request(i, timeout=0.3) for i in ids]}


@scenario
def slave_wakeup_retry(lin, live=False):
    lin.configure_lin(19200)
    if live:
        time.sleep(SLAVE_SLEEP_S)
    first = lin.request(SLAVE_ID, timeout=0.3, retries=2)
    return {"first": first, "second": lin.request(SLAVE_ID, timeout=0.3)}


@scenario
def slave_wrong_baudrate(lin, live=False):
    lin.configure_lin(9600)
    return {"answer": lin.request(SLAVE_ID, timeout=0.3, retries=1),
            "status": lin.request_status()}


@scenario
def slave_autobaud(lin, live=False):
    events = []
    lin.add_event_listener(events.append)
    lin.configure_lin(19200, autobaud=True)
    answer = lin.request(SLAVE_ID, timeout=0.3, retries=1)
    return {"answer": answer, "status": lin.request_status(), "events": events}


@scenario
def slave_collision(lin, live=False):
    events = []
    lin.add_event_listener(events.append)
    lin.configure_lin(19200)
    awake = lin.request(SLAVE_ID, timeout=0.3, retries=1)
    lin.set_slave_responses({SLAVE_ID: b"4", 0x21: b""})
    events.clear()
    collision = lin.request(SLAVE_ID, timeout=0.3)
    time.sleep(0.2)
    collision_events = list(events)
    lin.clear_received()
    own = lin.request(0x21, timeout=0.3)
    lin.enable_slave_profile(False)
    return {"awake": awake, "collision": collision, "events": collision_events,
            "own": own, "status": lin.request_status()}


@scenario
def slave_audi_illumination(lin, live=False):
    """Beleuchtung über ID 0x01 (1 Byte, Prozent) und Response_Error (Bit 4 in 0x10)."""
    lin.configure_lin(19200)
    pid = protected_id(0x01)

    def knob():
        return lin.request(SLAVE_ID, timeout=0.3, retries=1)

    def frame(payload, raw=False):
        if raw:
            lin.send_raw(pid, payload, wait=True)
        else:
            lin.send_frame(0x01, payload, ChecksumType.ENHANCED, wait=True)
        time.sleep(0.02)

    result = {"awake": knob()}
    for name, payload, raw in (
        ("valid", b"\x32", False),
        ("bad_checksum", b"\x64\x00", True),
        ("too_long", b"\x64\x9a\x00", True),
        ("classic", bytes([0x64, frame_checksum(0x01, b"\x64", ChecksumType.CLASSIC)]), True),
        ("valid_100", b"\x64", False),
    ):
        frame(payload, raw)
        result[name] = (knob(), knob())
    lin.send_header(0x01, wait=True)
    time.sleep(0.02)
    result["header_only"] = (knob(), knob())
    return result


TASTENFELD_IDS = (0x20, 0x2A, 0x2B, 0x2C, 0x2D, 0x2E)


@scenario
def tastenfeld_basics(lin, live=False):
    """Tastenfeld 8W1 919 616 B (Tasten 1-8): Frames, Kennung, Beleuchtung, Response_Error."""
    lin.configure_lin(19200)
    pid = protected_id(0x01)

    def key_status():
        return lin.request(0x20, timeout=0.3, retries=1)

    result = {"awake": key_status()}
    result["frames"] = {i: lin.request(i, timeout=0.3) for i in TASTENFELD_IDS}
    result["unused"] = lin.request(0x10, timeout=0.3)
    lin.send_frame(0x3C, bytes([0x7F, 0x06, 0xB2, 0x00, 0xFF, 0x7F, 0xFF, 0xFF]),
                   ChecksumType.CLASSIC, wait=True)
    time.sleep(0.02)
    result["product_id"] = lin.request(0x3D, timeout=0.3, retries=1)
    for name, payload, raw in (("valid", b"\x64", False), ("bad_checksum", b"\x64\x00", True),
                               ("too_long", b"\x64\x9a\x00", True), ("valid_again", b"\x64", False)):
        if raw:
            lin.send_raw(pid, payload, wait=True)
        else:
            lin.send_frame(0x01, payload, ChecksumType.ENHANCED, wait=True)
        time.sleep(0.02)
        result[name] = (key_status(), key_status())
    return result


def record(names):
    from apglin.recording import RecordingTransport
    from apglin.transport import HidTransport

    RECORDINGS.mkdir(exist_ok=True)
    for name in names:
        transport = RecordingTransport(HidTransport.open())
        lin = LinAnalyzer(transport)
        try:
            firmware = lin.request_status().firmware_version_str
            transport.entries.clear()
            result = SCENARIOS[name](lin, live=True)
            time.sleep(0.2)
        finally:
            lin.close()
        bus = ("Slave am Bus (Audi-Bedienteil 8W1 919 616, ID 0x10)" if name.startswith("slave_")
               else "Slave am Bus (Audi-Tastenfeld 8W1 919 616 B)" if name.startswith("tastenfeld_")
               else "kein weiterer Busteilnehmer")
        transport.save(RECORDINGS / f"{name}.txt",
                       f"{name}: APGDT001, Firmware {firmware}, VBAT, {bus}\n"
                       f"aufgenommen {datetime.date.today().isoformat()} mit tests/hw_scenarios.py")
        print(f"{name}: {len(transport.entries)} Reports")
        for key, value in result.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    record(sys.argv[1:] or list(SCENARIOS))

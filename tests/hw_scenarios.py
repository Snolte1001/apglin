"""Abläufe am echten Gerät, deren Mitschnitte in ``tests/recordings`` liegen.

``test_recordings.py`` spielt die Mitschnitte mit denselben Funktionen ab.
Neu aufnehmen (APGDT001 mit VBAT, ohne weitere Busteilnehmer):

    python tests/hw_scenarios.py [Name ...]
"""

import datetime
import sys
import time
from pathlib import Path

from apglin import ChecksumType, LinAnalyzer
from apglin import protocol
from apglin.lin import protected_id

RECORDINGS = Path(__file__).with_name("recordings")
SCENARIOS = {}


def scenario(func):
    SCENARIOS[func.__name__] = func
    return func


def _frames(lin, timeout=0.3):
    return list(lin.frames(timeout))


@scenario
def loopback_19200(lin):
    lin.configure_lin(19200)
    lin.send_frame(0x0C, bytes.fromhex("110400000000C000"), wait=True)
    return {"frames": _frames(lin)}


@scenario
def classic_9600(lin):
    lin.configure_lin(9600)
    lin.send_frame(0x3C, bytes(range(1, 9)), wait=True)
    return {"frames": _frames(lin), "status": lin.request_status()}


@scenario
def request_no_slave(lin):
    lin.configure_lin(19200)
    return {"answer": lin.request(0x21, timeout=0.5)}


@scenario
def slave_responses(lin):
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
def slave_profile_entries(lin):
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
def warm_reset_clears_profile(lin):
    lin.configure_lin(19200)
    lin.set_slave_responses({0x21: b"\x01"})
    before = lin.request(0x21, timeout=0.5)
    status = lin.warm_reset()
    after = lin.request(0x21, timeout=0.5)
    lin.enable_slave_profile(False)
    return {"before": before, "status": status, "after": after}


@scenario
def autobaud_loopback(lin):
    events = []
    lin.add_event_listener(events.append)
    lin.configure_lin(9600, autobaud=True)
    lin.send_frame(0x0C, b"\x01\x02\x03\x04", wait=True)
    frames = _frames(lin)
    return {"frames": frames, "status": lin.request_status(), "events": events}


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
            result = SCENARIOS[name](lin)
            time.sleep(0.2)
        finally:
            lin.close()
        transport.save(RECORDINGS / f"{name}.txt",
                       f"{name}: APGDT001, Firmware {firmware}, VBAT, kein weiterer Busteilnehmer\n"
                       f"aufgenommen {datetime.date.today().isoformat()} mit tests/hw_scenarios.py")
        print(f"{name}: {len(transport.entries)} Reports")
        for key, value in result.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    record(sys.argv[1:] or list(SCENARIOS))

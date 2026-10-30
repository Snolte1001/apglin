"""Regressionstests aus Mitschnitten am APGDT001 (Firmware 0x2304).

Jeder Test spielt einen Mitschnitt aus ``recordings/`` ab und führt dabei
denselben Ablauf aus ``hw_scenarios.py`` aus. Die Wiedergabe prüft jeden
Report, den die Bibliothek schreibt, Byte für Byte gegen die Aufnahme.
"""

import pytest

from apglin import ChecksumType, FrameError, LinAnalyzer
from apglin import constants as c
from apglin.recording import ReplayTransport

import hw_scenarios


def replay(name):
    transport = ReplayTransport.load(hw_scenarios.RECORDINGS / f"{name}.txt")
    lin = LinAnalyzer(transport, response_timeout=1.0)
    try:
        result = hw_scenarios.SCENARIOS[name](lin)
    finally:
        lin.close()
    assert transport.remaining_writes == 0
    return result


def test_every_scenario_has_a_recording():
    for name in hw_scenarios.SCENARIOS:
        assert (hw_scenarios.RECORDINGS / f"{name}.txt").exists(), name


def test_loopback_19200():
    (frame,) = replay("loopback_19200")["frames"]
    assert frame.pid == 0x4C and frame.data == bytes.fromhex("110400000000C000")
    assert frame.checksum == 0xDD and frame.checksum_type is ChecksumType.ENHANCED
    assert frame.transmitted and frame.ok
    assert frame.baudrate == 19231
    # Warmstart setzt die Zeit zurück, alte Ereignisse sind verworfen
    assert frame.timestamp < 0.1


def test_classic_9600():
    result = replay("classic_9600")
    (frame,) = result["frames"]
    assert frame.frame_id == 0x3C and frame.checksum == 0xDB
    assert frame.checksum_type is ChecksumType.CLASSIC and frame.transmitted
    assert result["status"].active_baudrate == 9597


def test_request_no_slave():
    answer = replay("request_no_slave")["answer"]
    assert answer.pid == 0x61 and answer.data == b""
    assert answer.errors == FrameError.NO_RESPONSE


def test_slave_responses():
    result = replay("slave_responses")
    assert result["status"].control_block.slave_profile
    assert result["status"].buffers.cbuf3_used == 22
    a = result["answers"]
    assert a[0x21].data == b"\x01\x02\x03" and a[0x21].checksum == 0x98
    assert a[0x21].checksum_type is ChecksumType.ENHANCED
    assert a[0x22].data == b"\x11\x22" and a[0x22].checksum_type is ChecksumType.ENHANCED
    assert a[0x3C].data == bytes(range(0x11, 0x19))
    assert a[0x3C].checksum_type is ChecksumType.CLASSIC
    assert all(a[i].ok and not a[i].transmitted for i in (0x21, 0x22, 0x3C))
    # ID ohne Eintrag: keine Antwort
    assert a[0x10].errors == FrameError.NO_RESPONSE
    # Slave-Profil abgeschaltet: keine Antwort mehr
    assert result["off"].errors == FrameError.NO_RESPONSE


def test_slave_profile_entries():
    r = replay("slave_profile_entries")
    # Gerät rechnet nur classic, das Bit „enhanced“ ändert nichts
    assert r["device_checksum"].checksum == 0xF9
    assert r["device_checksum"].checksum_type is ChecksumType.CLASSIC
    assert r["enhanced_bit"].checksum == 0xF9
    # Lesen per PID, nicht per Frame-ID
    assert r["read"] == b"\x01\x02\x03"
    assert r["read_frame_id"] == b""
    # Schreiben überschreibt an Ort und Stelle, Länge bleibt
    assert r["read_after_write"] == b"\x0A\x0B\x0C"
    assert r["answer_after_write"].data == b"\x0A\x0B\x0C"
    assert r["answer_after_write"].checksum == 0xDE
    # Schreiben auf eine PID ohne Eintrag legt nichts an
    assert r["read_unknown"] == b""


def test_warm_reset_clears_profile():
    r = replay("warm_reset_clears_profile")
    assert r["before"].data == b"\x01" and r["before"].ok
    assert r["status"].buffers.cbuf3_used == 0
    assert r["status"].control_block.slave_profile
    assert r["after"].errors == FrameError.NO_RESPONSE


def test_autobaud_loopback():
    r = replay("autobaud_loopback")
    (frame,) = r["frames"]
    assert frame.ok and frame.transmitted and frame.baudrate == 9597
    assert r["status"].control_block.autobaud
    # Eigene Sendung: keine Messung (kein Ereignis 0x85), BRG bleibt
    assert r["status"].active_baudrate == 9597
    assert not [e for e in r["events"] if e.tag == c.Event.LIN_AUTOBAUD]
    assert not [e for e in r["events"] if e.tag == c.Event.LIN_BREAK_TX]


def test_replay_detects_changed_command_sequence():
    from apglin.recording import ReplayMismatch

    transport = ReplayTransport.load(hw_scenarios.RECORDINGS / "loopback_19200.txt")
    lin = LinAnalyzer(transport, response_timeout=0.2)
    try:
        with pytest.raises(ReplayMismatch):
            lin.configure_lin(9600)
    finally:
        lin.close()


# --- mit Slave am Bus (antwortet bei 19200 Bd nur auf ID 0x10) ---------------

def test_slave_scan_wakes_up_on_first_header():
    a = replay("slave_scan")["answers"]
    # erster Header nach 5 s Busruhe weckt den Slave nur
    assert a[0].frame_id == 0x10 and a[0].errors == FrameError.NO_RESPONSE
    for frame in (a[2], a[5]):
        assert frame.frame_id == 0x10 and frame.data == b"\x00\x00"
        assert frame.checksum == 0xAF and frame.checksum_type is ChecksumType.ENHANCED
        assert frame.ok and not frame.transmitted
    for frame in (a[1], a[3], a[4]):
        assert frame.errors == FrameError.NO_RESPONSE


def test_slave_wakeup_retry():
    r = replay("slave_wakeup_retry")
    assert r["first"].ok and r["first"].data == b"\x00\x00"
    assert r["second"].ok


def test_slave_wrong_baudrate():
    r = replay("slave_wrong_baudrate")
    assert r["answer"].errors == FrameError.NO_RESPONSE
    assert not r["status"].has_error


def test_slave_autobaud():
    r = replay("slave_autobaud")
    assert r["answer"].ok and r["answer"].data == b"\x00\x00"
    assert r["status"].control_block.autobaud
    assert r["status"].active_baudrate == 19231
    assert not [e for e in r["events"] if e.tag == c.Event.LIN_AUTOBAUD]


def test_slave_collision_with_own_profile():
    r = replay("slave_collision")
    assert r["awake"].ok
    # Slave und eigenes Profil antworten gleichzeitig: nach der PID meldet
    # das Gerät einen Break, keine Daten, keinen Fehler
    assert r["collision"].errors == FrameError.NO_RESPONSE
    tags = [e.tag for e in r["events"] if e.tag != c.Event.TIME]
    assert tags == [c.Event.LIN_BREAK_RX, c.Event.MARKER, c.Event.DATA_BYTE, c.Event.LIN_BREAK_RX]
    assert not r["status"].has_error
    assert r["own"].ok and r["own"].data == b"\x01"


def test_slave_audi_illumination_response_error():
    r = replay("slave_audi_illumination")
    assert r["awake"].ok
    # gültiger 1-Byte-Frame an 0x01: kein Fehler
    for key in ("valid", "valid_100", "header_only"):
        assert [f.data for f in r[key]] == [b"\x00\x00", b"\x00\x00"], key
    # falsche Prüfsumme, zu lang, classic: Bit 4 (Response_Error) genau einmal
    for key in ("bad_checksum", "too_long", "classic"):
        first, second = r[key]
        assert first.data == b"\x00\x10" and first.ok, key
        assert second.data == b"\x00\x00", key

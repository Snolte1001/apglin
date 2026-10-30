import pytest

from apglin import constants as c
from apglin import protocol
from apglin.exceptions import ProtocolError
from apglin.lin import protected_id
from apglin.protocol import ControlBlock


def test_control_report():
    r = protocol.control(c.Control.STATUS_REQUEST)
    assert len(r) == 64
    assert r[:2] == b"\x01\x02" and not any(r[2:])


def test_lin_send_matches_dll_layout():
    # Microchip-DLL LIN.Transmit: 03 len 84 n PID data... 1F 77
    pid = protected_id(0x0C)
    payload = bytes.fromhex("1104000000 00C000DD".replace(" ", ""))
    script = protocol.script_lin_send(pid, payload) + protocol.script_marker()
    (report,) = protocol.write_script(script)
    assert report[:5] == bytes([0x03, len(payload) + 5, 0x84, len(payload) + 1, 0x4C])
    assert report[5:5 + len(payload)] == payload
    assert report[5 + len(payload):7 + len(payload)] == b"\x1f\x77"


def test_header_only():
    assert protocol.script_lin_send(0x4C, b"") == bytes([0x84, 1, 0x4C])


def test_lin_send_limit():
    with pytest.raises(ValueError):
        protocol.script_lin_send(0x4C, bytes(10))


def test_script_chunking():
    reports = protocol.write_script(bytes(range(100)))
    assert [r[1] for r in reports] == [62, 38]
    assert reports[1][2] == 62


def test_clear_cbuf():
    assert protocol.clear_cbuf(3)[0] == 0x0A


def test_control_block_defaults():
    cb = ControlBlock.lin_default()
    assert cb.comm_mode == c.CommMode.LIN
    assert cb.autobaud and cb.receive and cb.chip_select and not cb.slave_profile
    assert cb.flush_on_count and cb.flush_on_time
    assert cb.flush_byte_count == 0x35
    assert cb.flush_interval_ms == pytest.approx(50.7, abs=0.1)


def test_control_block_without_autobaud_matches_dll():
    cb = ControlBlock.lin_default()
    cb.autobaud = False
    assert cb.data[c.CB_LIN_FLAGS] == 0x48


def test_control_block_baudrate():
    cb = ControlBlock.lin_default()
    cb.baudrate = 19200
    assert cb.data[c.CB_BRG_LO] == 259 & 0xFF and cb.data[c.CB_BRG_HI] == 1
    assert cb.baudrate == 19231


def test_parse_status_report():
    cb = bytes(ControlBlock.lin_default())
    sb = bytearray(20)
    sb[c.SB_COMM_MODE] = 10
    report = (bytes([0x88, 7, 0x81, 0x05, 0x03, 0x82]) + cb + bytes([0x83]) + sb
              + bytes([0x84, 0, 255, 0, 255, 0, 255, 0x80]))
    report += bytes(64 - len(report))
    groups = dict(protocol.parse_report(report))
    assert groups[c.Group.FIRMWARE_VERSION] == b"\x05\x03"
    assert groups[c.Group.CONTROL_BLOCK] == cb
    assert groups[c.Group.STATUS_BLOCK][c.SB_COMM_MODE] == 10
    assert groups[c.Group.CBUF_STATUS] == bytes([0, 255, 0, 255, 0, 255])


def test_parse_stream_group_and_padding():
    report = bytes([0x86, 3, 0x83, 0x10, 0x4C]) + bytes(59)
    assert protocol.parse_report(report) == [(0x86, bytes([0x83, 0x10, 0x4C]))]


def test_parse_truncated():
    with pytest.raises(ProtocolError):
        protocol.parse_report(bytes([0x86, 10, 1, 2]))

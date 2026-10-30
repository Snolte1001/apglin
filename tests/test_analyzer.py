import pytest

from apglin import ConfigurationError, DeviceTimeoutError, LinAnalyzer, TransportError
from apglin import constants as c
from apglin.lin import ChecksumType, protected_id
from apglin.protocol import ControlBlock
from apglin.simulator import SIM_FIRMWARE_VERSION, SimulatedTransport
from apglin.transport import Transport


@pytest.fixture
def sim():
    return SimulatedTransport()


@pytest.fixture
def lin(sim):
    analyzer = LinAnalyzer(sim, frame_timeout=0.05, response_timeout=1.0)
    yield analyzer
    analyzer.close()


def test_status(lin):
    st = lin.request_status()
    assert st.firmware_version == SIM_FIRMWARE_VERSION
    assert st.buffers.cbuf1_free == 40
    assert not st.has_error


def test_configure_lin_sequence(lin, sim):
    lin.configure_lin(19200)
    cmds = [r[:2] for r in sim.written]
    # Kaltstart, Control Block, Warmstart, CBUF2 leeren, Statusanfrage
    assert cmds[:5] == [b"\x01\x00", sim.written[1][:2], b"\x01\x01", b"\x09\x00", b"\x01\x02"]
    assert sim.written[1][0] == c.Command.WRITE_CONTROL_BLOCK
    st = lin.request_status()
    assert st.comm_mode == c.CommMode.LIN
    assert st.control_block.baudrate == 19231
    assert not st.control_block.autobaud
    assert lin.baudrate == 19231
    assert lin.active_baudrate == 19231


def test_configure_verify_fails(lin, sim):
    original = sim._apply_control_block

    def broken():
        sim.control_block[0] = 0x00
        original()

    sim._apply_control_block = broken
    with pytest.raises(ConfigurationError):
        lin.configure_lin(19200)


def test_set_baudrate_and_options(lin):
    lin.configure_lin(19200)
    lin.set_baudrate(9600)
    assert lin.baudrate == 9597
    lin.set_options(autobaud=True, chip_select=False)
    cb = lin.request_status().control_block
    assert cb.autobaud and not cb.chip_select and cb.receive


def test_send_frame(lin, sim):
    lin.configure_lin(19200)
    lin.send_frame(0x0C, bytes.fromhex("110400000000C000"), wait=True)
    script_report = [r for r in sim.written if r[0] == c.Command.WRITE_CBUF1][-1]
    assert script_report[2:5] == bytes([0x84, 10, 0x4C])
    assert script_report[13] == 0xDD
    frame = lin.receive(timeout=1.0)
    assert frame.frame_id == 0x0C and frame.checksum == 0xDD and frame.ok
    assert frame.transmitted


def test_classic_checksum_forced(lin):
    lin.configure_lin(19200)
    lin.send_frame(0x10, b"\x01", ChecksumType.CLASSIC)
    frame = lin.receive(timeout=1.0)
    assert frame.checksum_type is ChecksumType.CLASSIC


def test_request_gets_slave_answer(lin, sim):
    lin.configure_lin(19200)
    sim.responders[0x21] = b"\xAA\xBB"
    frame = lin.request(0x21)
    assert frame.data == b"\xAA\xBB" and frame.ok


def test_request_without_slave(lin):
    lin.configure_lin(19200)
    frame = lin.request(0x22)
    assert frame.data == b"" and not frame.ok


def test_receive_from_other_master(lin, sim):
    lin.configure_lin(19200)
    received = []
    lin.add_listener(received.append)
    sim.inject_frame(0x05, b"\x01\x02\x03")
    frame = lin.receive(timeout=1.0)
    assert frame.data == b"\x01\x02\x03" and not frame.transmitted
    assert received == [frame]
    assert lin.receive(timeout=0.1) is None


def test_receive_disabled(lin, sim):
    lin.configure_lin(19200, receive=False)
    sim.inject_frame(0x05, b"\x01")
    assert lin.receive(timeout=0.2) is None


def test_slave_profile_entry_needs_loaded_profile(lin):
    # wie Firmware 0x2304: Schreiben auf eine PID ohne Eintrag legt nichts an
    lin.configure_lin(19200)
    lin.write_slave_profile_entry(0x4C, b"\x01\x02")
    assert lin.read_slave_profile_entry(0x4C) == b""


def test_slave_profile_entry_overwrites_in_place(lin):
    lin.configure_lin(19200)
    lin.set_slave_responses({0x0C: b"\x01\x02"})
    assert lin.read_slave_profile_entry(0x4C) == b"\x01\x02\xB0"
    lin.write_slave_profile_entry(0x4C, b"\x0A\x0B\x0C\x0D")
    assert lin.read_slave_profile_entry(0x4C) == b"\x0A\x0B\x0C"


def test_set_slave_responses_answers_own_header(lin, sim):
    lin.configure_lin(19200)
    lin.set_slave_responses({0x21: b"\x01\x02\x03", 0x3C: b"\x05"})
    frame = lin.request(0x21)
    assert frame.data == b"\x01\x02\x03" and frame.checksum == 0x98
    assert frame.checksum_type is ChecksumType.ENHANCED and not frame.transmitted
    assert lin.request(0x3C).checksum_type is ChecksumType.CLASSIC
    assert lin.request(0x22).data == b""
    lin.enable_slave_profile(False)
    assert lin.request(0x21).data == b""


def test_warm_reset_clears_slave_profile(lin, sim):
    lin.configure_lin(19200)
    lin.set_slave_responses({0x21: b"\x01"})
    assert lin.warm_reset().buffers.cbuf3_used == 0
    assert lin.request(0x21).data == b""


def test_set_slave_responses_too_long(lin):
    lin.configure_lin(19200)
    with pytest.raises(ValueError):
        lin.set_slave_responses({0x21: bytes(9)})
    with pytest.raises(ValueError):
        lin.set_slave_responses({i: bytes(8) for i in range(5)})


def test_load_slave_profile_too_big(lin):
    lin.configure_lin(19200)
    with pytest.raises(ValueError):
        lin.load_slave_profile(bytes(41))


def test_load_slave_profile(lin, sim):
    lin.configure_lin(19200)
    lin.load_slave_profile(bytes(range(30)))
    assert sim.cbuf3 == bytes(range(30))
    assert lin.request_status().control_block.slave_profile


def test_cbuf1_flow_control(lin, sim):
    lin.configure_lin(19200)
    special = [r for r in sim.written if r[0] == c.Command.SPECIAL_STATUS]
    for _ in range(30):
        lin.send_frame(0x01, bytes(8))
    special = [r for r in sim.written if r[0] == c.Command.SPECIAL_STATUS]
    # 30 Skripte à 15 Byte passen nicht in einen 40-Byte-Puffer ohne Nachfragen
    assert len(special) >= 2


def test_led_and_timer(lin, sim):
    lin.set_led(2, 1)
    lin.reset_timer()
    lin.request_status()
    assert sim.leds[2] == 1


class DeadTransport(Transport):
    def write(self, report):
        pass

    def read(self, timeout):
        raise TransportError("weg")


def test_transport_failure_surfaces():
    lin = LinAnalyzer(DeadTransport())
    lin._thread.join(1)
    assert not lin.is_open
    with pytest.raises(TransportError):
        lin.request_status()


class SilentTransport(Transport):
    def write(self, report):
        pass

    def read(self, timeout):
        return None


def test_status_timeout():
    lin = LinAnalyzer(SilentTransport(), response_timeout=0.1)
    with pytest.raises(DeviceTimeoutError):
        lin.request_status()
    lin.close()


def test_control_block_copy_is_independent(lin):
    lin.configure_lin(19200)
    cb = lin.last_status.control_block
    cb.autobaud = True
    assert not lin.last_status.control_block.autobaud
    assert isinstance(cb, ControlBlock)


def test_configure_lin_uses_low_latency_flush(lin):
    # wie Microchip-Demo TB3180: Set_Buffer_Flush_Parameters(true, true, 10, 10)
    cb = lin.configure_lin(19200).control_block
    assert cb.flush_on_count and cb.flush_on_time
    assert cb.flush_byte_count == 10
    assert cb.data[c.CB_FLUSH_INTERVAL] == round(10 / 0.409)


def test_own_frame_marked_but_foreign_identical_frame_not(lin, sim):
    lin.configure_lin(19200)
    lin.send_frame(0x05, b"\x01\x02")
    assert lin.receive(timeout=1.0).transmitted
    sim.inject_frame(0x05, b"\x01\x02")
    assert not lin.receive(timeout=1.0).transmitted


def test_stale_events_before_configure_are_discarded(lin, sim):
    # Hardwarebefund: nach Minuten im LIN-Modus ohne Leser lieferte das Gerät
    # vier Zeitüberläufe nach, der erste Frame stand dann bei 107,2 s.
    sim.stale_events = bytes([c.Event.TIME_ROLLOVER] * 4)
    lin.configure_lin(19200)
    lin.send_frame(0x0C, bytes.fromhex("110400000000C000"))
    frame = lin.receive(timeout=1.0)
    assert frame.timestamp < 1.0
    assert lin.discarded_stream_bytes == 4


def test_frame_str_marks_direction(lin, sim):
    lin.configure_lin(19200)
    lin.send_frame(0x05, b"\x01")
    assert " TX ID=0x05" in str(lin.receive(timeout=1.0))
    sim.inject_frame(0x06, b"\x01")
    assert " RX ID=0x06" in str(lin.receive(timeout=1.0))

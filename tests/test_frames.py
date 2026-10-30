from apglin.constants import Event
from apglin.frames import FrameAssembler, FrameError
from apglin.lin import ChecksumType, protected_id
from apglin.stream import StreamParser


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def frame_events(pid, payload, ticks=0):
    ev = bytes([Event.LIN_BREAK_RX, Event.TIME, ticks & 0xFF, ticks >> 8, Event.DATA_BYTE, pid])
    for b in payload:
        ev += bytes([Event.DATA_BYTE, b])
    return ev


def test_parser_handles_split_events():
    raw = frame_events(0x4C, b"\x01\x02", ticks=0x1234)
    p = StreamParser()
    events = []
    for i in range(len(raw)):
        events += p.feed(raw[i:i + 1])
    assert [e.tag for e in events] == [Event.LIN_BREAK_RX, Event.TIME, Event.DATA_BYTE,
                                       Event.DATA_BYTE, Event.DATA_BYTE]
    assert events[1].args == b"\x34\x12"


def test_parser_variable_lengths():
    p = StreamParser()
    events = p.feed(bytes([Event.DATA_BYTES, 3, 1, 2]))
    assert events == []
    events = p.feed(bytes([3, Event.LIN_SLAVE_PROFILE_DATA, 0x4C, 2, 9]))
    assert events[0].args == bytes([3, 1, 2, 3])
    events = p.feed(bytes([8]))
    assert events[0].args == bytes([0x4C, 2, 9, 8])


def test_parser_unknown_tag_drops_rest():
    p = StreamParser()
    events = p.feed(bytes([0x83, 0x42, 0x10, 0x01]))
    assert events[0].tag == Event.LIN_BREAK_RX
    assert events[1].tag == 0x42
    assert p.unknown_tags == 1
    assert p.feed(bytes([0x83]))[0].tag == Event.LIN_BREAK_RX


def assemble(raw, clock=None):
    clock = clock or Clock()
    asm = FrameAssembler(timeout=0.1, clock=clock)
    frames = asm.feed(StreamParser().feed(raw))
    clock.now += 1.0
    return frames + asm.poll()


def test_frame_ends_with_next_break_and_timeout():
    pid = protected_id(0x0C)
    data = bytes.fromhex("110400000000C000")
    raw = frame_events(pid, data + b"\xDD", ticks=1000) + frame_events(protected_id(0x10), b"", ticks=2000)
    frames = assemble(raw)
    assert len(frames) == 2
    f = frames[0]
    assert f.frame_id == 0x0C and f.data == data and f.checksum == 0xDD
    assert f.checksum_type is ChecksumType.ENHANCED and f.ok
    assert abs(f.timestamp - 1000 * 0.000409) < 1e-9
    assert frames[1].errors == FrameError.NO_RESPONSE


def test_frame_finishes_after_nine_bytes():
    clock = Clock()
    asm = FrameAssembler(clock=clock)
    frames = asm.feed(StreamParser().feed(frame_events(protected_id(0x0C), bytes(8) + b"\x00")))
    assert len(frames) == 1  # ohne Timeout


def test_frame_errors():
    frames = assemble(frame_events(0x4C ^ 0x80, b"\x01\x00"))
    assert FrameError.PARITY in frames[0].errors
    assert FrameError.CHECKSUM in frames[0].errors
    frames = assemble(frame_events(0x4C, b"\x01") + bytes([Event.LIN_STATUS_ERROR, 1]))
    assert FrameError.BUS_ERROR in frames[0].errors


def test_time_rollover_and_autobaud():
    raw = bytes([Event.TIME_ROLLOVER]) + bytes([Event.LIN_BREAK_RX, Event.LIN_AUTOBAUD, 3, 1]) \
        + bytes([Event.TIME, 10, 0, Event.DATA_BYTE, 0x4C, Event.DATA_BYTE, 0xB3])
    (f,) = assemble(raw)
    assert abs(f.timestamp - (26.8 + 10 * 0.000409)) < 1e-9
    assert f.baudrate == 19231


def test_transmitted_flag():
    (f,) = assemble(bytes([Event.LIN_BREAK_TX]) + frame_events(0x4C, b"\x01\x02"))
    assert f.transmitted


# Mitschnitt vom APGDT001 (Firmware 0x2304), Loopback-Echo von
# „apglin send 0x0C 11 04 00 00 00 00 C0 00“ bei 19200 Bd
HW_ECHO_0C = bytes.fromhex(
    "83"
    "133300 104c  133600 1011  133700 1004  133900 1000  133a00 1000"
    "133b00 1000  133c00 1000  133e00 10c0  133f00 1000  134000 1c77"
    "134100 10dd  134100"
)


def test_hardware_echo_recording():
    (f,) = assemble(HW_ECHO_0C)
    assert f.frame_id == 0x0C and f.pid == 0x4C
    assert f.data == bytes.fromhex("110400000000C000")
    assert f.checksum == 0xDD and f.checksum_type is ChecksumType.ENHANCED and f.ok
    assert abs(f.timestamp - 0x33 * 0.000409) < 1e-9
    assert not f.transmitted  # Firmware meldet kein LIN_BREAK_TX

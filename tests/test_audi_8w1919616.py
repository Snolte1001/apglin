"""Belegung des Audi-Bedienteils 8W1 919 616, geprüft an echten Frames.

Die Frames stammen aus einer Sitzung am 30.10.2026 (Bedienteil am APGDT001,
ID 0x10 alle ~30 ms abgefragt), jeweils mit der Angabe, was am Bedienteil
getan wurde.
"""

import sys
from pathlib import Path

import pytest

from apglin.lin import ChecksumType, detect_checksum_type, protected_id

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import audi_8w1919616 as knob  # noqa: E402

CAPTURE = Path(__file__).with_name("recordings") / "audi_8w1919616_frames.txt"


def load_segments():
    segments = {}
    current = None
    for line in CAPTURE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("== "):
            current = segments.setdefault(line[3:], [])
            continue
        parts = line.split()
        if parts[1] == "-":
            current.append((float(parts[0]), None, None))
        else:
            current.append((float(parts[0]), bytes.fromhex("".join(parts[1:3])), int(parts[3], 16)))
    return segments


SEGMENTS = load_segments()


def segment(prefix):
    (name,) = [n for n in SEGMENTS if n.startswith(prefix)]
    return [knob.decode(data) for _, data, _ in SEGMENTS[name] if data is not None]


def rotations(states):
    return [s.rotation for s in states if s.rotation]


def test_all_frames_are_enhanced_with_two_bytes():
    pid = protected_id(knob.FRAME_ID)
    frames = [f for rows in SEGMENTS.values() for f in rows if f[1] is not None]
    assert len(frames) > 100
    for _, data, chk in frames:
        assert len(data) == 2
        assert detect_checksum_type(pid, data, chk) is ChecksumType.ENHANCED


def test_first_header_only_wakes_up():
    rows = SEGMENTS["Weck-Header: erster Header nach Busruhe ohne Antwort"]
    assert rows[0][1] is None
    assert rows[1][1] == b"\x00\x00"


def test_single_detents():
    assert rotations(segment("1x links")) == [-1]
    assert rotations(segment("1x rechts")) == [1]
    assert rotations(segment("5x links")) == [-1] * 5 + [1] * 5


def test_fast_rotation_never_exceeds_one_detent_per_frame():
    turns = rotations(segment("schnell"))
    assert set(turns) == {-1, 1}
    # erst alle links, dann alle rechts
    assert turns == sorted(turns)
    assert turns.count(-1) == 12 and turns.count(1) == 16


def test_rotation_is_reported_for_one_frame_only():
    for prefix in ("1x links", "schnell", "gedrückt und dabei links gedreht"):
        states = segment(prefix)
        for prev, cur in zip(states, states[1:]):
            assert not (prev.rotation and cur.rotation)


@pytest.mark.parametrize("prefix, buttons", [
    ("kurz gedrückt", [0x01, 0x00]),
    ("links gekippt", [0x04, 0x00]),
    ("rechts gekippt", [0x08, 0x00]),
    ("lange gedrückt", [0x01, 0x00]),
    ("gedrückt und dabei rechts gekippt", [0x01, 0x09, 0x01, 0x00]),
    ("gedrückt und dabei links gekippt", [0x01, 0x05, 0x01, 0x00]),
])
def test_buttons(prefix, buttons):
    assert [s.buttons for s in segment(prefix)] == buttons
    assert all(s.rotation == 0 for s in segment(prefix))


def test_long_press_has_no_own_code():
    (name,) = [n for n in SEGMENTS if n.startswith("lange gedrückt")]
    (t_down, down, _), (t_up, up, _) = SEGMENTS[name]
    assert down == b"\x00\x01" and up == b"\x00\x00"
    assert t_up - t_down > 3.0


def test_rotation_while_pressed():
    left = segment("gedrückt und dabei links gedreht")
    assert rotations(left) == [-1] * 10
    assert all(s.pressed for s in left if s.rotation)
    right = segment("gedrückt und dabei rechts gedreht")
    # zehn Rasten gedrückt, eine nach dem Loslassen
    assert [(s.rotation, s.pressed) for s in right if s.rotation] == [(1, True)] * 10 + [(1, False)]


def test_bit1_never_seen():
    states = [knob.decode(data) for rows in SEGMENTS.values() for _, data, _ in rows if data]
    # Bit 4 (Response_Error) kommt nur nach fehlerhaften Frames an ID 0x01,
    # siehe test_recordings; beim Bedienen nie
    assert not any(s.unknown_buttons or s.response_error for s in states)


def test_decode():
    s = knob.decode(b"\xff\x09")
    assert s.rotation == -1 and s.pressed and s.tilt_right and not s.tilt_left
    assert knob.button_names(0x05) == "gedrückt, links gekippt"
    assert knob.button_names(0x02) == "unbekannt 0x02"
    assert knob.button_names(0x00) == "losgelassen"
    with pytest.raises(ValueError):
        knob.decode(b"\x00")


def test_response_error_bit():
    s = knob.decode(b"\x00\x11")
    assert s.response_error and s.pressed and not s.unknown_buttons
    assert knob.button_names(0x10) == "losgelassen"


def test_illumination_payload():
    # am Gerät beobachtet: 00 aus, 01 sehr dunkel, 0A dunkel, 32 sichtbar
    # dunkler als FF, 64 gleich hell wie FF
    assert knob.illumination_payload(0) == b"\x00"
    assert knob.illumination_payload(100) == b"\x64"
    with pytest.raises(ValueError):
        knob.illumination_payload(256)

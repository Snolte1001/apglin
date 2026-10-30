"""docs/AUDI_8W1919616.ldf gegen Decoder und echte Frames prüfen."""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LDF = ROOT / "docs" / "AUDI_8W1919616.ldf"
sys.path.insert(0, str(ROOT / "examples"))
import audi_8w1919616 as knob  # noqa: E402
from test_audi_8w1919616 import SEGMENTS  # noqa: E402


def ldf_text():
    return LDF.read_text(encoding="utf-8")


def frame_block(name):
    match = re.search(rf"{name}: (0x[0-9A-Fa-f]+), (\w+), (\d+) \{{(.*?)\}}", ldf_text(), re.S)
    assert match, name
    signals = dict((s, int(o)) for s, o in re.findall(r"(\w+), (\d+);", match.group(4)))
    return int(match.group(1), 16), match.group(2), int(match.group(3)), signals


def bit(mask):
    return 8 + mask.bit_length() - 1


def test_ldf_frames_match_decoder():
    assert 'LIN_protocol_version = "2.1"' in ldf_text()
    assert "LIN_speed = 19.2 kbps" in ldf_text() and knob.BAUDRATE == 19200
    fid, pub, length, sig = frame_block("BT_Status")
    assert (fid, pub, length) == (knob.FRAME_ID, "BT", 2)
    assert sig == {
        "BT_Drehung": 0,
        "BT_Taste_gedrueckt": bit(knob.PRESSED),
        "BT_Kippen_links": bit(knob.TILT_LEFT),
        "BT_Kippen_rechts": bit(knob.TILT_RIGHT),
        "BT_Response_Error": bit(knob.RESPONSE_ERROR),
    }
    assert frame_block("BT_Beleuchtung_Frame")[:3] == (knob.ILLUMINATION_ID, "Master", 1)


def test_ldf_node_attributes_from_read_by_identifier():
    # Antwort am Gerät: 40 06 F2 49 00 01 30 00
    text = ldf_text()
    assert "configured_NAD = 0x40;" in text
    assert "product_id = 0x0049, 0x3001, 0x00;" in text
    assert "response_error = BT_Response_Error;" in text


def test_ldfparser_decodes_captured_frames_like_decoder():
    ldfparser = pytest.importorskip("ldfparser")
    ldf = ldfparser.parse_ldf(str(LDF), encoding="utf-8")
    frame = ldf.get_unconditional_frame("BT_Status")
    for rows in SEGMENTS.values():
        for _, data, _ in rows:
            if data is None:
                continue
            state = knob.decode(data)
            signals = frame.decode(data)
            rotation = signals["BT_Drehung"]
            assert (0 if rotation == "keine Drehung" else rotation) == state.rotation
            assert (signals["BT_Taste_gedrueckt"] == "betaetigt") == state.pressed
            assert (signals["BT_Kippen_links"] == "betaetigt") == state.tilt_left
            assert (signals["BT_Kippen_rechts"] == "betaetigt") == state.tilt_right
    assert ldf.get_unconditional_frame("BT_Beleuchtung_Frame").encode({"BT_Beleuchtung": 100}) == b"\x64"

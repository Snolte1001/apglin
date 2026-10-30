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


# --- Tastenfeld 8W1 919 616 B ---------------------------------------------------

LDF_B = ROOT / "docs" / "AUDI_8W1919616B.ldf"
import audi_8w1919616b as pad  # noqa: E402
from test_audi_8w1919616b import ROWS as PAD_ROWS  # noqa: E402


def test_ldf_b_node_attributes_and_frames():
    text = LDF_B.read_text(encoding="utf-8")
    assert "configured_NAD = 0x60;" in text
    assert "product_id = 0x0049, 0x3002, 0x00;" in text
    assert "response_error = TF_Response_Error;" in text
    for name, fid, length in (("TF_Beleuchtung_Frame", pad.ILLUMINATION_ID, 1), ("TF_Status", pad.STATUS_ID, 2),
                              ("TF_Staerke_A", 0x2A, 8), ("TF_Staerke_B", 0x2B, 8), ("TF_Roh_A", 0x2C, 8),
                              ("TF_Roh_B", 0x2D, 8), ("TF_Tasten", pad.KEYS_ID, 8)):
        assert re.search(rf"{name}: 0x{fid:02X}, \w+, {length} \{{", text), name


def test_ldfparser_b_matches_decoder_on_captured_frames():
    ldfparser = pytest.importorskip("ldfparser")
    ldf = ldfparser.parse_ldf(str(LDF_B), encoding="utf-8")
    status_f = ldf.get_unconditional_frame("TF_Status")
    keys_f = ldf.get_unconditional_frame("TF_Tasten")
    words_f = {0x2A: ("TF_Staerke_A", ("W0", "T2", "T3", "T4"), "TF_Staerke", True),
               0x2B: ("TF_Staerke_B", ("T5", "T6", "T7", "T8"), "TF_Staerke", True),
               0x2C: ("TF_Roh_A", ("1", "2", "3", "4"), "TF_Roh", False),
               0x2D: ("TF_Roh_B", ("5", "6", "7", "8"), "TF_Roh", False)}
    for _, fid, data, _ in PAD_ROWS:
        if fid == pad.STATUS_ID:
            s, sig = pad.decode_status(data), status_f.decode(data)
            assert sig["TF_Status_Maske"] == s.mask and sig["TF_Status_Wert"] == s.value
            assert (sig["TF_Schalter"] == "ja") == s.switch
        elif fid == pad.KEYS_ID:
            sig = keys_f.decode(data)
            number = sig["TF_Tasten_Nummer"]
            assert sig["TF_Tasten_Maske"] == data[0]
            assert (0 if number == "keine Taste" else number) == data[1]
        elif fid in words_f:
            name, keys, prefix, signed = words_f[fid]
            sig = ldf.get_unconditional_frame(name).decode(data)
            words = [sig[f"{prefix}_{k}_H"] * 256 + sig[f"{prefix}_{k}_L"] for k in keys]
            if signed:
                words = [w - 0x10000 if w & 0x8000 else w for w in words]
            assert tuple(words) == pad.decode_words(data, signed)

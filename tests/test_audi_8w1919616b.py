"""Belegung des Audi-Tastenfelds 8W1 919 616 B, geprüft an echten Frames.

Frames aus einer Sitzung am 30.10.2026 (Tastenfeld am APGDT001, alle IDs
abgefragt), jeweils mit der Angabe, was am Tastenfeld getan wurde.
"""

import sys
from pathlib import Path

import pytest

from apglin.lin import ChecksumType, detect_checksum_type, protected_id

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import audi_8w1919616b as pad  # noqa: E402

CAPTURE = Path(__file__).with_name("recordings") / "audi_8w1919616b_frames.txt"
LENGTHS = {0x20: 2, 0x2A: 8, 0x2B: 8, 0x2C: 8, 0x2D: 8, 0x2E: 8}


def load():
    segments, rows, current = {}, [], None
    for line in CAPTURE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("== "):
            current = segments.setdefault(line[3:], [])
            continue
        parts = line.split()
        if parts[2] == "-":
            continue
        row = (float(parts[0]), int(parts[1], 16), bytes.fromhex("".join(parts[2:-1])), int(parts[-1], 16))
        current.append(row)
        rows.append(row)
    return segments, rows


SEGMENTS, ROWS = load()


def segment(prefix):
    (name,) = [n for n in SEGMENTS if n.startswith(prefix)]
    return SEGMENTS[name]


def presses(rows):
    """(Start, Ende, Tastennummer) aus 0x2E."""
    out, start = [], None
    for t, fid, data, _ in rows:
        if fid != pad.KEYS_ID:
            continue
        keys = pad.decode_keys(data)
        if keys.mask and start is None:
            start = (t, keys.number)
        elif not keys.mask and start:
            out.append((start[0], t, start[1]))
            start = None
    return out


PRESSES = presses(ROWS)


def test_frames_lengths_and_enhanced_checksum():
    assert len(ROWS) > 2000
    for _, fid, data, chk in ROWS:
        if not any(data) and fid in (0x2C, 0x2D):
            continue   # einmal beim Aufwachen nur Nullen
        assert len(data) == LENGTHS[fid]
        assert detect_checksum_type(protected_id(fid), data, chk) is ChecksumType.ENHANCED


@pytest.mark.parametrize("prefix, keys", [
    ("Taste 1 gedrückt", [1]),
    ("Taste 1 kurz", [1]),
    ("Taste 2", [2]),
    ("Taste 3 bis 8", [3, 4, 6, 7, 8, 5, 6, 6, 7, 7, 8]),
    ("Ruhe, nicht", []),
    ("Ruhe, Bedienung", []),
    ("Hand angenähert", []),
    ("Pause", []),
])
def test_keys_frame_per_segment(prefix, keys):
    assert [k for _, _, k in presses(segment(prefix))] == keys


def test_keys_frame_layout():
    for _, fid, data, _ in ROWS:
        if fid != pad.KEYS_ID:
            continue
        keys = pad.decode_keys(data)
        assert not any(data[2:])
        if keys.mask:
            # immer genau eine Taste: Bit n-1 und Nummer n
            assert keys.pressed == [keys.number]
        else:
            assert keys.number == 0


def test_status_frame_matches_keys_frame():
    for start, end, key in PRESSES:
        status = [pad.decode_status(d) for t, fid, d, _ in ROWS
                  if fid == pad.STATUS_ID and start - 0.3 <= t <= end]
        with_key = [s for s in status if s.mask and not s.invalid]
        assert with_key, key
        assert all(s.pressed == [key] and s.value == key for s in with_key)
    # außerhalb der Drücke aus 0x2E meldet 0x20 nur einmal kurz Taste 5
    # (beim Streifen vor 6, 7, 8): 0x2E ist entprellt, 0x20 nicht
    def inside(t):
        return any(a - 0.5 <= t <= b + 0.5 for a, b, _ in PRESSES)
    outside = [(round(t, 1), pad.decode_status(d).pressed) for t, fid, d, _ in ROWS
               if fid == pad.STATUS_ID and not inside(t) and pad.decode_status(d).pressed]
    assert outside == [(556.6, [5])]


def test_status_switch_bit_and_no_response_error():
    status = [pad.decode_status(d) for _, fid, d, _ in ROWS if fid == pad.STATUS_ID]
    assert any(s.switch and s.mask for s in status)
    assert not any(s.response_error for s in status)


def test_strength_follows_pressed_key():
    last = {0x2A: bytes(8), 0x2B: bytes(8)}
    peaks = {}
    for t, fid, data, _ in ROWS:
        if fid in last:
            last[fid] = data
            for start, end, key in PRESSES:
                if start <= t <= end and end - start > 1.0 and key >= 2:
                    s = pad.strengths(last[0x2A], last[0x2B])
                    peaks.setdefault(key, {k: 0 for k in s})
                    for k, v in s.items():
                        peaks[key][k] = max(peaks[key][k], v)
    assert sorted(peaks) == [2, 3, 4, 5, 6, 7, 8]
    for key, values in peaks.items():
        assert values[key] >= 9
        assert values[key] == max(values.values())


def test_word0_of_2a_carries_mask_and_number_while_pressed():
    for t, fid, data, _ in ROWS:
        # FF FF = Rauschwert -1, sonst Maske und Nummer der gedrückten Taste
        if fid == 0x2A and data[0] and data[:2] != b"\xff\xff":
            assert data[0] == 1 << (data[1] - 1)


def test_raw_values_do_not_react_to_keys():
    values = [pad.decode_words(d, False) for _, fid, d, _ in ROWS if fid in pad.RAW_IDS and any(d)]
    for i in range(4):
        column = [v[i] for v in values]
        assert 400 < min(column) and max(column) < 700


def test_decoders():
    s = pad.decode_status(b"\x40\x57")
    assert s.pressed == [7] and s.value == 7 and s.switch and s.response_error
    assert pad.decode_status(b"\xff\x1f").invalid and pad.decode_status(b"\xff\x1f").pressed == []
    assert pad.decode_keys(bytes.fromhex("8008000000000000")).pressed == [8]
    assert pad.decode_words(b"\x00\x0f\xff\xff\x00\x00\x02\x08", True) == (15, -1, 0, 520)
    assert pad.illumination_payload(100) == b"\x64"
    with pytest.raises(ValueError):
        pad.decode_status(b"\x00")


# --- Berühren gegen Drücken (zweite Sitzung) -----------------------------------

TOUCH = Path(__file__).with_name("recordings") / "audi_8w1919616b_touch.txt"


def touch(prefix):
    segments, current = {}, None
    for line in TOUCH.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("== "):
            current = segments.setdefault(line[3:], [])
            continue
        parts = line.split()
        current.append((int(parts[1], 16), bytes.fromhex("".join(parts[2:-1]))))
    (name,) = [n for n in segments if n.startswith(prefix)]
    return segments[name]


def test_touch_without_press_sets_position_but_not_switch():
    rows = touch("Finger auf Taste 4")
    keys = [pad.decode_keys(d) for fid, d in rows if fid == pad.KEYS_ID]
    status = [pad.decode_status(d) for fid, d in rows if fid == pad.STATUS_ID]
    assert [k.pressed for k in keys] == [[4]]
    assert {tuple(s.pressed) for s in status} == {(4,), ()}
    assert not any(s.switch for s in status)


def test_mechanical_switch_is_bit4_of_status():
    status = [pad.decode_status(d) for fid, d in touch("Taste 4 heruntergedrückt") if fid == pad.STATUS_ID]
    assert status and all(s.switch for s in status)
    # Position wechselt mit Maske 0 ab, der Schalter bleibt
    assert {tuple(s.pressed) for s in status} == {(4,), ()}
    released = [pad.decode_status(d) for fid, d in touch("Druck gelöst") if fid == pad.STATUS_ID]
    assert released and not any(s.switch for s in released)


def test_sliding_sets_two_bits_briefly():
    keys = [pad.decode_keys(d) for fid, d in touch("Finger von Taste 4") if fid == pad.KEYS_ID]
    assert [(k.pressed, k.number) for k in keys] == [([4, 8], 4), ([8], 8)]
    assert [pad.decode_keys(d).pressed for fid, d in touch("Finger weg") if fid == pad.KEYS_ID] == [[]]

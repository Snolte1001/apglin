import pytest

from apglin.lin import (
    ChecksumType,
    baud_to_brg,
    brg_to_baud,
    checksum,
    detect_checksum_type,
    frame_checksum,
    parity_ok,
    protected_id,
)


@pytest.mark.parametrize("frame_id, pid", [
    (0x00, 0x80), (0x01, 0xC1), (0x0C, 0x4C), (0x15, 0x55),
    (0x3C, 0x3C), (0x3D, 0x7D), (0x3F, 0xBF),
])
def test_protected_id(frame_id, pid):
    assert protected_id(frame_id) == pid
    assert parity_ok(pid)


def test_protected_id_range():
    with pytest.raises(ValueError):
        protected_id(0x40)


def test_parity_detects_flipped_bit():
    assert not parity_ok(0x4C ^ 0x80)


# Werte aus Hardwaremitschnitten mit dem APGDT001 (siehe docs/PROTOKOLL.md, Quellen)
@pytest.mark.parametrize("data, expected", [
    ("11 04 00 00 00 00 C0 00", 0xDD),
    ("0F 04 00 00 00 00 C0 02", 0xDD),
    ("10 00 00 00 00 00 C0 0A", 0xD8),
    ("11 04 10 00 00 00 C0 00", 0xCD),
    ("0F 04 08 02 00 00 C0 02", 0xD3),
])
def test_enhanced_checksum_reference(data, expected):
    assert frame_checksum(0x0C, bytes.fromhex(data), ChecksumType.ENHANCED) == expected


def test_classic_checksum_carry():
    # 0xFF + 0x01 = 0x100 -> 0x01, invertiert 0xFE
    assert checksum([0xFF, 0x01]) == 0xFE
    assert checksum([]) == 0xFF


def test_auto_uses_classic_for_diagnostics():
    data = bytes(8)
    assert frame_checksum(0x3C, data) == checksum(data)
    assert frame_checksum(0x10, data) == checksum(data, protected_id(0x10))


def test_detect_checksum_type():
    pid = protected_id(0x0C)
    data = bytes.fromhex("1104000000 00C000".replace(" ", ""))
    assert detect_checksum_type(pid, data, 0xDD) is ChecksumType.ENHANCED
    assert detect_checksum_type(pid, data, checksum(data)) is ChecksumType.CLASSIC
    assert detect_checksum_type(pid, data, 0x00) is None


@pytest.mark.parametrize("baud, brg", [(19200, 259), (9600, 520), (10417, 479), (2400, 2082)])
def test_brg(baud, brg):
    assert baud_to_brg(baud) == brg
    assert abs(brg_to_baud(brg) - baud) / baud < 0.002

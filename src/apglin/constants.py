"""Protokollkonstanten des APGDT001 / PICkit Serial Analyzer.

Herkunft und Belastbarkeit der Werte sind in docs/PROTOKOLL.md beschrieben.
"""

from enum import IntEnum

# --- USB -------------------------------------------------------------------

VENDOR_ID = 0x04D8                  # Microchip
PRODUCT_ID_LIN_ANALYZER = 0x0A04    # APGDT001 LIN Serial Analyzer
PRODUCT_ID_PICKIT_SERIAL = 0x0036   # DV164122 PICkit Serial Analyzer (gleiche Firmware-Basis)
PRODUCT_IDS = (PRODUCT_ID_LIN_ANALYZER, PRODUCT_ID_PICKIT_SERIAL)

REPORT_SIZE = 64                    # HID-Report ohne Report-ID (Report-ID ist immer 0)

# --- Zeitbasis -------------------------------------------------------------

FOSC_HZ = 20_000_000                # Bezugstakt für BRG-Berechnung
EVENT_TICK_S = 0.000409             # Auflösung der Ereigniszeitstempel
EVENT_ROLLOVER_S = 26.8             # Überlauf des 16-Bit-Ereigniszeitgebers
FLUSH_TICK_MS = 0.409               # Einheit des Flush-Intervalls im Control Block


# --- Befehle Host -> Gerät (Byte 0 des Reports) ----------------------------

class Command(IntEnum):
    CONTROL = 0x01               # Unterbefehl in Byte 1, siehe Control
    WRITE_CONTROL_BLOCK = 0x02   # 24 Byte Control Block
    WRITE_CBUF1 = 0x03           # Skript in Skriptpuffer 1
    WRITE_CBUF3 = 0x05           # Daten in Puffer 3 (Slave-Profil)
    CLEAR_CBUF1 = 0x08
    CLEAR_CBUF2 = 0x09
    CLEAR_CBUF3 = 0x0A
    SPECIAL_STATUS = 0x0B        # Byte 1 = 0xAB


class Control(IntEnum):
    COLD_RESET = 0x00
    WARM_RESET = 0x01
    STATUS_REQUEST = 0x02
    CONTROL_BLOCK_TO_EEPROM = 0x03
    EEPROM_TO_CONTROL_BLOCK = 0x04
    FLUSH_CBUF2 = 0x05
    COMM_RESET = 0x06
    COMM_CLEAR = 0x07            # Statusfehler löschen


SPECIAL_STATUS_KEY = 0xAB


# --- Gruppen Gerät -> Host (Tags im eingehenden Report) ---------------------

class Group(IntEnum):
    END = 0x80                   # Ende der Nutzdaten im Report
    FIRMWARE_VERSION = 0x81      # 2 Byte, Little Endian
    CONTROL_BLOCK = 0x82         # 24 Byte
    STATUS_BLOCK = 0x83          # 20 Byte
    CBUF_STATUS = 0x84           # 6 Byte Füllstände der drei Puffer
    CBUF1_DATA = 0x85            # Länge + Daten
    CBUF2_DATA = 0x86            # Länge + Daten (Ereignisstrom)
    CBUF3_DATA = 0x87            # Länge + Daten
    PACKET_ID = 0x88             # 1 Byte


GROUP_FIXED_SIZES = {
    Group.FIRMWARE_VERSION: 2,
    Group.CONTROL_BLOCK: 24,
    Group.STATUS_BLOCK: 20,
    Group.CBUF_STATUS: 6,
    Group.PACKET_ID: 1,
}
GROUP_VARIABLE = (Group.CBUF1_DATA, Group.CBUF2_DATA, Group.CBUF3_DATA)


# --- Skriptbefehle (Inhalt von CBUF1) --------------------------------------

class Script(IntEnum):
    LED1 = 0x12                      # + Zustand
    LED2 = 0x13                      # + Zustand
    MARKER = 0x1F                    # + Wert, wird als Ereignis MARKER zurückgemeldet
    EVENT_TIMER_RESET = 0x20
    LIN_SEND = 0x84                  # + Anzahl, PID, Daten..., Prüfsumme
    LIN_WRITE_SLAVE_PROFILE = 0x86   # + PID, Anzahl, Daten...
    LIN_READ_SLAVE_PROFILE = 0x87    # + PID


MARKER_SCRIPT_DONE = 0x77
MAX_SCRIPT_CHUNK = 62                # Nutzbytes je Report bei WRITE_CBUF1/3
MAX_LIN_PAYLOAD = 9                  # Datenbytes + Prüfsumme je LIN_SEND


# --- Ereignisse im CBUF2-Strom ---------------------------------------------

class Event(IntEnum):
    # allgemein (Bit 4 gesetzt)
    DATA_BYTE = 0x10
    DATA_BYTES = 0x11
    MACRO_LOOP = 0x12
    TIME = 0x13
    TIME_ROLLOVER = 0x14
    MACRO_DONE = 0x15
    MACRO_ROLLOVER = 0x16
    MACRO_ABORT = 0x17
    TIMEOUT_AB0 = 0x18
    TIMEOUT_AB1 = 0x19
    STATUS_ERROR = 0x1A
    END_OF_SCRIPT = 0x1B
    MARKER = 0x1C
    # LIN-Modus
    LIN_BYTE_RX = 0x80
    LIN_BYTE_TX = 0x81
    LIN_STATUS_ERROR = 0x82
    LIN_BREAK_RX = 0x83
    LIN_BREAK_TX = 0x84
    LIN_AUTOBAUD = 0x85
    LIN_CHECKSUM_ERROR = 0x86
    LIN_ID_PARITY_ERROR = 0x87
    LIN_SLAVE_PROFILE_DATA = 0x88


# Anzahl fester Argumentbytes je Ereignis; None = variable Länge
EVENT_ARG_SIZES = {
    Event.DATA_BYTE: 1,
    Event.DATA_BYTES: None,
    Event.MACRO_LOOP: 1,
    Event.TIME: 2,
    Event.TIME_ROLLOVER: 0,
    Event.MACRO_DONE: 2,
    Event.MACRO_ROLLOVER: 0,
    Event.MACRO_ABORT: 0,
    Event.TIMEOUT_AB0: 0,
    Event.TIMEOUT_AB1: 0,
    Event.STATUS_ERROR: 1,
    Event.END_OF_SCRIPT: 0,
    Event.MARKER: 1,
    Event.LIN_BYTE_RX: 1,
    Event.LIN_BYTE_TX: 1,
    Event.LIN_STATUS_ERROR: 1,
    Event.LIN_BREAK_RX: 0,
    Event.LIN_BREAK_TX: 0,
    Event.LIN_AUTOBAUD: 2,
    Event.LIN_CHECKSUM_ERROR: 1,
    Event.LIN_ID_PARITY_ERROR: 1,
    Event.LIN_SLAVE_PROFILE_DATA: None,
}


# --- Control Block (24 Byte) -----------------------------------------------

CONTROL_BLOCK_SIZE = 24

CB_FLAGS = 0                 # Bit 7: Flush nach Zeit, Bit 6: Flush nach Anzahl, Bit 5: Busy-LED manuell
CB_FLUSH_BYTE_COUNT = 3
CB_FLUSH_INTERVAL = 4        # Einheit FLUSH_TICK_MS
CB_COMM_MODE = 8
CB_LIN_FLAGS = 16
CB_BRG_LO = 22
CB_BRG_HI = 23

CB_FLAG_BUSY_LED_MANUAL = 0x20
CB_FLAG_FLUSH_ON_COUNT = 0x40
CB_FLAG_FLUSH_ON_TIME = 0x80

LIN_FLAG_CHIP_SELECT = 0x08      # Transceiver-CS high (Normalbetrieb)
LIN_FLAG_SLAVE_PROFILE = 0x20    # Slave-Antworten aus CBUF3
LIN_FLAG_RECEIVE = 0x40          # Empfang einschalten
LIN_FLAG_AUTOBAUD = 0x80


class CommMode(IntEnum):
    IDLE = 0
    I2C_MASTER = 1
    SPI_MASTER = 2
    SPI_SLAVE = 3
    USART_ASYNC = 4
    USART_SYNC_MASTER = 5
    USART_SYNC_SLAVE = 6
    I2C_SLAVE = 7
    I2C_BITBANG_MASTER = 8
    I2C_SLAVE_BITBANG_MASTER = 9
    LIN = 10
    MICROWIRE_MASTER = 11
    MTOUCH2 = 12


# Werkseinstellung des Control Blocks für den LIN-Modus (mit Autobaud)
LIN_DEFAULT_CONTROL_BLOCK = bytes([
    0xC0, 0x00, 0x00, 0x35, 0x7C, 0x00, 0x00, 0x00,
    0x0A, 0x33, 0x20, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC8, 0x98, 0x0F, 0x00, 0x00, 0x00, 0xF3, 0x01,
])


# --- Status Block (20 Byte) ------------------------------------------------

STATUS_BLOCK_SIZE = 20

SB_ERROR_0 = 0
SB_ERROR_1 = 4
SB_FLAGS = 5                 # Bit 0: Kommunikation aktiv
SB_COMM_MODE = 6
SB_ERROR_2 = 12
SB_BAUD_LO = 18              # gemessene/aktive BRG
SB_BAUD_HI = 19

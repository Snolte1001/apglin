# apglin

Python-Bibliothek für den **Microchip APGDT001 LIN Serial Analyzer**, direkt
über USB-HID, ohne die Windows-DLL (`PICkitS.dll`) und ohne die Java-Software
von Microchip. Läuft unter Linux und Windows.

> **Stand 0.1.0:** Das Protokoll ist aus den Microchip-Unterlagen, der
> PICkitS-DLL, der Microchip-Java-Bibliothek (TB3180) und
> Hardware-Mitschnitten Dritter rekonstruiert. **An einem APGDT001
> (Firmware `0x2304`, Windows) getestet:** Status, Konfiguration, Senden
> mit Loopback-Echo, Master Request mit fremdem Slave und ohne Slave,
> Slave-Profil (Gerät antwortet auch auf eigene Header), 9600/19200 Bd.
> **Noch nicht getestet:** fremder Master (Autobaud-Messung, Mitschnitt),
> Datenframes an fremde Knoten, Linux.
> Details: [docs/PROTOKOLL.md](docs/PROTOKOLL.md).

## Gerät

| | |
|---|---|
| USB | HID, VID `04D8`, PID `0A04` |
| Firmware-Basis | PICkit Serial Analyzer (PIC18F2450), Transceiver MCP2021 |
| LIN | Master senden, Master Request, Mitschneiden, Autobaud, Slave-Antworten |
| Baudrate | 20 MHz / (4 · (BRG + 1)), z. B. 19200 → 19231 Bd |
| Versorgung | VBAT 8–30 V (nominal 12–14,4 V) an Klemme oder Hohlstecker, sonst kein LIN |
| Master/Slave | Jumper E1 innen: gesteckt = Master-Pull-up 1 kΩ (Auslieferung) |
| Loopback | eigene Sendungen erscheinen im Mitschnitt, Test ohne Bus möglich |

## Installation

```bash
pip install git+https://github.com/Snolte1001/apglin.git
```

Linux: Zugriff auf `/dev/hidraw*` freigeben.

```bash
sudo cp udev/99-apgdt001.rules /etc/udev/rules.d/
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Windows: kein Treiber nötig.

## Beispiele

```python
from apglin import LinAnalyzer, ChecksumType

with LinAnalyzer.open() as lin:
    print(lin.request_status().firmware_version_str)
    lin.configure_lin(19200)

    # Frame als Master senden (Prüfsumme wird berechnet)
    lin.send_frame(0x0C, bytes([0x11, 0x04, 0, 0, 0, 0, 0xC0, 0]))

    # Header senden, Antwort eines Slaves lesen; retries=1 weckt einen
    # schlafenden Slave (nach 4 s Busruhe beantwortet er erst den 2. Header)
    answer = lin.request(0x21, timeout=1.0, retries=1)
    print(answer.data.hex(), answer.ok)

    # Bus mitschneiden
    for frame in lin.frames(timeout=5.0):
        print(frame)
```

Das Gerät als Slave (beantwortet auch die eigenen Header, Test ohne
zweiten Knoten möglich; ein Warmstart bzw. `configure_lin` löscht das
Profil, höchstens vier Einträge mit 8 Byte):

```python
lin.set_slave_responses({0x21: b"", 0x22: b"""})
print(lin.request(0x21))    # RX ID=0x21 ... 01 02 03 chk=98 enhanced
lin.enable_slave_profile(False)
```

Callback statt Warteschlange (läuft im Lesethread, nicht blockieren):

```python
lin.add_listener(lambda frame: print(frame.frame_id, frame.data))
```

`LinFrame` enthält `pid`, `frame_id`, `data` (ohne Prüfsumme), `checksum`,
`checksum_type` (erkannt: classic/enhanced), `errors` (`FrameError`),
`timestamp` (Gerätezeit) und `baudrate`.

Prüfsumme: `ChecksumType.AUTO` (Standard) rechnet enhanced, für die
Diagnose-IDs `0x3C`/`0x3D` classic. Für Fehlertests sendet `send_raw(pid,
payload)` die Bytes unverändert.

### Ohne Hardware

```python
from apglin import LinAnalyzer
from apglin.simulator import SimulatedTransport

sim = SimulatedTransport()
sim.responders[0x21] = b"\xAA\xBB"    # Slave am Bus
lin = LinAnalyzer(sim)
lin.configure_lin(19200)
print(lin.request(0x21))
```

Ein vollständiges Beispiel mit echtem Slave (Audi-Bedienteil
8W1 919 616, Drehen/Drücken/Kippen):
[`examples/audi_8w1919616.py`](examples/audi_8w1919616.py), Belegung in
[docs/AUDI_8W1919616.md](docs/AUDI_8W1919616.md).

Mehrere Header hintereinander: jeweils `send_header(..., wait=True)` und
eine Pause, nie nachholen, sonst überschreibt der nächste Header die
Slave-Antwort.

## Kommandozeile

```bash
apglin list
```

```bash
apglin info
```

```bash
apglin monitor --baud 19200 --raw
```

```bash
apglin send 0x0C 11 04 00 00 00 00 C0 00
```

```bash
apglin request 0x21
```

## Aufbau

| Modul | Inhalt |
|---|---|
| `analyzer.py` | `LinAnalyzer`: Lesethread, Status, Konfiguration, Senden, Empfangen |
| `protocol.py` | Reports kodieren/dekodieren, `ControlBlock`, `DeviceStatus` |
| `stream.py` | Ereignisstrom aus Puffer 2 zerlegen |
| `frames.py` | Frames aus Ereignissen zusammensetzen, `LinFrame` |
| `lin.py` | PID, Prüfsummen, Baudrate ↔ BRG |
| `transport.py` | HID-Zugriff (`hidapi`), austauschbar |
| `simulator.py` | simuliertes Gerät für Tests |
| `recording.py` | USB-Verkehr mitschneiden und wiedergeben |

## Tests

```bash
pip install -e .[test]
```

```bash
pytest
```

`tests/test_recordings.py` spielt Mitschnitte vom echten Gerät ab
(`tests/recordings/`) und prüft jeden Report, den die Bibliothek schreibt,
gegen die Aufnahme. Neu aufnehmen mit angeschlossenem Gerät:

```bash
python tests/hw_scenarios.py
```

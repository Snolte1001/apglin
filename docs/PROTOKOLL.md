# USB-Protokoll des APGDT001 LIN Serial Analyzer

Microchip veröffentlicht keine Protokollbeschreibung. Die offizielle Software
(bis v2.x .NET mit `PICkitS.dll`, ab v3.0.0 Java) und die Java-Bibliothek aus
TB3180 (`LINdllResource.jar`) sind die einzigen vorgesehenen Schnittstellen.
Diese Beschreibung ist aus den unten genannten Quellen zusammengetragen.

Die Java-Bibliothek (2016) ist eine direkte Portierung der .NET-DLL: gleiche
Klassen, Methoden und Konstanten. Beide Implementierungen stimmen in allen
hier beschriebenen Bytefolgen überein.

**Belastbarkeit** – jede Angabe trägt eine Kennung:

| Kennung | Bedeutung |
|---|---|
| **[HW]** | von Dritten an echter Hardware bestätigt |
| **[DLL]** | übereinstimmend aus `PICkitS.dll` und `LINdllResource.jar` abgeleitet (die Original-Software arbeitet so) |
| **[UG]** | steht im LIN Serial Analyzer User's Guide DS51675B |
| **[ANNAHME]** | eigene Deutung, an Hardware zu prüfen |

## Gerät

- USB-HID-Gerät, VID `0x04D8`, PID `0x0A04` **[HW]**. Der PICkit Serial
  Analyzer (DV164122, PID `0x0036`) nutzt dieselbe Firmware-Basis **[DLL]**.
- Kein Treiber nötig (HID-Klasse). Unter Linux `hidraw`, dafür die udev-Regel
  aus `udev/` installieren.
- Mikrocontroller PIC18F2450, LIN-Transceiver MCP2021-500 **[UG]**. Firmware
  ist die des PICkit Serial Analyzer (DS51647, Kapitel 9) **[UG]**.
- Versorgung über VBAT der Klemme (VBAT, LIN, GND) oder Hohlstecker 6,5 × 2,5 mm
  (Mitte +, beide intern verbunden): 8,0–30,0 V, nominal 12,0–14,4 V, < 5 mA
  für den Transceiverteil **[UG]**. Ohne VBAT keine LIN-Kommunikation
  (Bench-Aufbau Dritter: 12 V) **[HW]**.
- Jumper E1 (intern): gesteckt = Master mit 1 kΩ Pull-up, offen = Slave mit
  30 kΩ. Auslieferung als Master **[UG]**.
- Fest verdrahtete Loopback-Strecke: Eigene Sendungen erscheinen im
  Empfangsstrom, ein Test ohne Bus ist möglich **[UG]**.
- LEDs: grün USB, gelb Busy, blau LIN (Bus versorgt) **[UG]**.
- Reports: 64 Byte Nutzdaten, Report-ID 0 **[DLL]**.

## Ausgehende Reports (Host → Gerät)

Byte 0 = Befehl, Rest mit `0x00` aufgefüllt. **[DLL]**

| Byte 0 | Byte 1 … | Bedeutung |
|---|---|---|
| `01` | `00` | Kaltstart (vor Control-Block-Schreiben) |
| `01` | `01` | Warmstart (übernimmt Control Block, Zeitbasis = 0) |
| `01` | `02` | Statusanfrage → Statusreport |
| `01` | `03` | Control Block → EEPROM |
| `01` | `04` | EEPROM → Control Block |
| `01` | `05` | CBUF2 leeren (flush) |
| `01` | `06` | Kommunikations-Reset |
| `01` | `07` | Statusfehler löschen |
| `02` | 24 Byte | Control Block schreiben |
| `03` | n, n Byte | Skript an CBUF1 anhängen (n ≤ 62, sonst auf mehrere Reports verteilen) |
| `05` | n, n Byte | Daten an CBUF3 (Slave-Profil) |
| `08`/`09`/`0A` | – | CBUF1/2/3 löschen |
| `0B` | `AB` | „spezielle“ Statusanfrage, liefert aktuelle Pufferfüllstände |

## Eingehende Reports (Gerät → Host)

Folge von Gruppen `Tag [Daten]`, Ende mit `80` oder Füllbytes. **[DLL]**

| Tag | Länge | Inhalt |
|---|---|---|
| `80` | 0 | Ende |
| `81` | 2 | Firmwareversion, Little Endian |
| `82` | 24 | Control Block |
| `83` | 20 | Status Block |
| `84` | 6 | Füllstände: CBUF1 belegt/frei, CBUF2 belegt/frei, CBUF3 belegt/frei **[DLL]**. Leeres Gerät meldet `00 28 00 80 00 28`, also Puffergrößen 40 / 128 / 40 Byte **[HW]** |
| `85` | 1+n | CBUF1-Daten (Länge + Daten) |
| `86` | 1+n | CBUF2-Daten = Ereignisstrom, siehe unten |
| `87` | 1+n | CBUF3-Daten |
| `88` | 1 | Paketzähler |

Ein Statusreport enthält `88 81 82 83 84 80`. Statusanfrage und Zerlegung
funktionieren am Gerät (Windows, hidapi) **[HW]**. Die DLL wartet auf Gruppe `84`
als Abschluss der Statusantwort.

## Control Block (24 Byte)

Nach dem Einschalten (Idle, Modus 0) meldet das Gerät
`C0 00 00 01 FF 00 … 00` **[HW]**.

| Offset | Bedeutung |
|---|---|
| 0 | Bit 7 Flush nach Zeit, Bit 6 Flush nach Byteanzahl, Bit 5 Busy-LED manuell **[DLL]** |
| 3 | Flush-Byteanzahl (LIN-Standard `0x35`) **[DLL]** |
| 4 | Flush-Intervall in 0,409 ms (LIN-Standard `0x7C` ≈ 50,7 ms) **[DLL]** |
| 8 | Kommunikationsmodus, LIN = `0x0A` **[DLL]** |
| 16 | LIN-Optionen: Bit 7 Autobaud, Bit 6 Empfang ein, Bit 5 Slave-Profil aktiv, Bit 3 Transceiver-CS high **[DLL]** |
| 22/23 | BRG Low/High, Baudrate = 20 MHz / (4 · (BRG + 1)) **[DLL]** |

Werkseinstellung LIN mit Autobaud **[DLL]**:

```
C0 00 00 35 7C 00 00 00 0A 33 20 00 00 00 00 00 C8 98 0F 00 00 00 F3 01
```

Ohne Autobaud ist Offset 16 `0x48`. Die Bytes 9, 10, 17, 18 sind in ihrer
Bedeutung unbekannt und werden unverändert übernommen.

Ablauf beim Umschalten auf LIN **[DLL]**: Kaltstart → Control Block →
Warmstart → Statusanfrage, die 24 Byte des Echos müssen gleich sein.
`apglin` leert vor der Statusanfrage zusätzlich CBUF2 (siehe Ereignisstrom).

Die Microchip-Demo zu TB3180 stellt danach Flush auf 10 Byte / 10 ms
(`Set_Buffer_Flush_Parameters(true, true, 10, 10)`), damit Frames ohne
spürbare Verzögerung beim PC ankommen. `apglin` übernimmt das in
`configure_lin`.
Spätere Änderungen (Baudrate, Optionen): Control Block → Fehler löschen →
Statusanfrage und vergleichen.

## Status Block (20 Byte)

| Offset | Bedeutung |
|---|---|
| 0, 4, 12 | Fehlerbytes; ungleich 0 = Fehler **[DLL]** |
| 5 | Bit 0: Kommunikation aktiv **[DLL]** |
| 6 | aktiver Kommunikationsmodus **[DLL]** |
| 18/19 | aktive BRG (bei Autobaud gemessen) **[DLL]** |

## Skriptbefehle (CBUF1) im LIN-Modus

| Befehl | Argumente | Wirkung |
|---|---|---|
| `84` | n, PID, n−1 Byte | Break + Sync + PID + Bytes senden **[HW]**. Die PID wird mit Paritätsbits übergeben, die Prüfsumme hängt der Host als letztes Byte an **[HW]** (auch die Microchip-Demo rechnet PID und Prüfsumme selbst). n−1 ≤ 9. n = 1 sendet nur den Header (Master Request) **[DLL]** |
| `86` | PID, n, n Byte | Eintrag im Slave-Profil schreiben **[DLL]** |
| `87` | PID | Eintrag lesen → Ereignis `88` **[DLL]** |
| `1F` | Wert | Marker, kommt als Ereignis `1C Wert` zurück; `77` = Skript fertig **[DLL]** |
| `20` | – | Ereigniszeit auf 0 **[DLL]** |
| `12`/`13` | Wert | LED 1/2 **[DLL]** |

Beispiel (ID `0x0C`, 8 Byte, enhanced) **[HW]**:

```
03 0E 84 0A 4C 11 04 00 00 00 00 C0 00 DD 1F 77
```

Vor dem Schreiben prüft die DLL per `0B AB`, ob CBUF1 genug freie Bytes hat.

## Ereignisstrom (CBUF2)

| Tag | Argumente | Bedeutung |
|---|---|---|
| `10` | 1 | Datenbyte am Bus (PID, Daten, Prüfsumme) **[DLL]** |
| `11` | n + n | mehrere Datenbytes **[DLL]** |
| `12` | 1 | Makro-Schleife |
| `13` | 2 | Zeitstempel, Little Endian, × 0,409 ms **[DLL]** |
| `14` | – | Überlauf Zeitstempel (+26,8 s) **[DLL]** |
| `15` | 2 | Makro fertig |
| `16`–`19`, `1B` | – | Makro-Überlauf/-Abbruch, Timeouts, Skriptende |
| `1A` | 1 | Statusfehler |
| `1C` | 1 | Marker (siehe `1F`) |
| `80`/`81` | 1 | Byte empfangen/gesendet (USART-Ebene) |
| `82` | 1 | LIN-Statusfehler, beendet den Frame **[DLL]** |
| `83` | – | Break empfangen = Frame-Beginn **[DLL]** |
| `84` | – | Break gesendet |
| `85` | 2 | Autobaud: gemessene BRG **[DLL]** |
| `86` | 1 | Prüfsummenfehler |
| `87` | 1 | ID-Paritätsfehler |
| `88` | PID, n, n Byte | Slave-Profil-Daten (Antwort auf Skript `87`) **[DLL]** |

Frame-Aufbau wie in der DLL: Break `83` startet einen Frame, der folgende
Zeitstempel gehört zu ihm, das erste `10`-Byte ist die PID, die weiteren
sind Daten mit der Prüfsumme als letztem Byte. Der Frame endet mit dem
nächsten Break, nach 9 Bytes, bei `82` oder nach einer Pause (DLL: 100 ms).
Das Sync-Byte `0x55` erscheint nicht im Strom **[HW]**. Vor jedem Byte steht
ein eigener Zeitstempel `13` **[HW]**.

Eigene Sendungen kommen über die Loopback-Strecke als normale Frames zurück
**[UG] [HW]**. Ein `84` (Break gesendet) liefert Firmware `0x2304` dabei
nicht **[HW]**. `apglin` kennzeichnet eigene Frames deshalb, indem es das
Echo mit den zuletzt gesendeten Frames vergleicht (PID, Daten, Prüfsumme,
höchstens 2 s alt). Der Marker `1C 77` kommt vor dem Echo des letzten Bytes
an **[HW]**; „Skript fertig“ heißt also nicht „Echo vollständig“.

Mitschnitt (Firmware `0x2304`, 19200 Bd, Loopback, ID `0x0C`) **[HW]**:

```
83  13 33 00 10 4C  13 36 00 10 11  13 37 00 10 04  13 39 00 10 00
    13 3A 00 10 00  13 3B 00 10 00  13 3C 00 10 00  13 3E 00 10 C0
    13 3F 00 10 00  13 40 00 1C 77  13 41 00 10 DD  13 41 00
```

Die Abstände der Zeitstempel passen zu 409 µs je Tick: 1–2 Ticks je Byte
bei 19200 Bd (0,52 ms je Byte), etwa 2,5 Ticks bei 9600 Bd (1,04 ms) **[HW]**.

Bleibt das Gerät im LIN-Modus, ohne dass jemand liest, puffert es
Ereignisse wie Zeitüberläufe `14` und liefert sie beim nächsten Öffnen nach.
Beobachtet: erster Frame bei 107,2 s = 4 × 26,8 s statt 0,02 s **[HW]**.
`apglin` leert deshalb nach dem Warmstart CBUF2 (`09`) und verwirft alle
Ereignisse bis zur nächsten Statusantwort.

Fehlerarten der Microchip-Software **[UG]**: Parität, Prüfsumme, „No Answer“
(Header ohne Daten innerhalb des Bus-Timeouts) und Bus-Timeout. TX-Bit-,
Framing-, Break- und SWBreak-Fehler sind laut User's Guide nicht
implementiert.

## Hardwaretests

Erledigt (30.10.2026, APGDT001, Firmware `0x2304`, Windows):

- Statusantwort und Firmwareversion lesen (`apglin info`).
- `configure_lin` (19200 Bd) mit Echo-Vergleich des Control Blocks.
- Senden mit Loopback-Echo, PID `0x4C` und Prüfsumme `DD` (enhanced) wie in
  den Mitschnitten Dritter.
- Sync-Byte nicht im Strom, kein `84`, Zeitstempel vor jedem Byte, Marker
  vor dem letzten Echo-Byte.
- 20 Frames hintereinander (`send -n 20`) mit 40-Byte-Skriptpuffer, Abstand
  16–24 ms.
- Master Request ohne Slave (`request 0x21`): Echo der PID `0x61`, keine
  Daten → `NO_RESPONSE`.
- 9600 Bd, Diagnose-ID `0x3C` mit classic-Prüfsumme `DB`.
- Nach Pause im LIN-Modus beginnt die Zeit wieder bei ~0,02 s (alte
  Ereignisse verworfen), eigene Frames als `TX` erkannt.

Offen:

1. Senden gegen einen zweiten LIN-Knoten oder Logikanalysator prüfen.
2. Master Request mit echtem Slave.
3. Autobaud und Mitschnitt mit fremdem Master (`monitor --autobaud`).
4. Format eines kompletten Slave-Profils für CBUF3 (`load_slave_profile`).

## Quellen

- Microchip, *LIN Serial Analyzer User's Guide*, DS51675B (2008):
  Hardware, Versorgung, Jumper, Loopback, Fehlerarten.
- Microchip, *PICkit Serial Analyzer User's Guide*, DS51647 (Firmware, Kapitel 9).
- Microchip, *PICkit Serial DLL (PICkitS.dll) Function Prototypes* v2.4.
- Microchip, TB3180 *LIN Analyzer Library Demo*: `LIN_Library_API_Demo.java`
  und die Bibliothek `LINdllResource.jar` (2016, per `javap` gelesen; nach
  Microchip-Lizenz nur mit Microchip-Produkten nutzbar, kein Code übernommen).
- Dekompilierte C#-Quelle der PICkitS.dll: github.com/claydonkey/PicKitS
  (keine Lizenz angegeben; nur zum Verständnis des Protokolls gelesen, kein Code übernommen).
- Hardware-Mitschnitte mit APGDT001 bei 19200 Bd: github.com/lakewoodphone/xiao-lin-bench
  (`BENCH_EVIDENCE.md`, `tools/send-apg-lin-frame.ps1`).

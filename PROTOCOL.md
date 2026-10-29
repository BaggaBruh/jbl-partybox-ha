# JBL PartyBox protocol

Reconstructed from the JBL PartyBox Android app 3.14.20 (`com.harman.sdk`) and verified on a
PartyBox speaker from Home Assistant OS / BlueZ.
"Verified" below means seen on that speaker; everything else comes from the decompiled app.

## Transport

### GATT characteristics

| Role | UUID |
|---|---|
| Service (BLE) | `65786365-6c70-6f69-6e74-2e636f6d0000` |
| RX: speaker → host (notify) | `65786365-6c70-6f69-6e74-2e636f6d0001` |
| TX: host → speaker (write / write without response) | `65786365-6c70-6f69-6e74-2e636f6d0002` |
| Unknown (write) | `65786365-6c70-6f69-6e74-2e636f6d0af1` |

- Pro/Ultimate models (`cmd_format_1`) notify on TX instead of RX.
- The app (`GattControllerImpl.doServicesDiscovered`) looks for RX/TX in the PartyBox service,
  then in a per-device service UUID, then in `0x1801`; only the characteristic UUIDs matter.
- Advertising service data UUIDs used for discovery: `fddf` (PartyBox), `dffd` / `fc69` (Pro).
- The app requests MTU 512 and sends one command at a time.

### Classic Bluetooth (verified)

The speaker is dual-mode on one public address. Once a host is connected to it over classic
Bluetooth (A2DP audio), BlueZ reports it as connected but exposes **no** BLE GATT services, so
BLE libraries like bleak find nothing. The speaker also serves its GATT database over classic
Bluetooth: ATT on L2CAP PSM 31 (it lists GAP `0x1800` in SDP). Layout seen over BR/EDR:

| Value handle | Characteristic | Properties |
|---|---|---|
| `0x0003` | `2a05` Service Changed | indicate |
| `0x0007` | `2a00` Device Name | read |
| `0x0009` | `2a01` Appearance | read |
| `0x000c` | `…0af1` | write, write without response |
| `0x000e` | `…0002` TX | write, write without response |
| `0x0010` | `…0001` RX | read, notify |

- There's no discoverable CCCD (Find Information after `0x0010` returns Attribute Not Found,
  writing `0x0011` returns Invalid Handle), but the speaker **sends notifications anyway**.
- A GATT peer may also send ATT requests to the host on this channel; answer them (MTU
  response, Attribute Not Found / Request Not Supported) rather than ignoring them.
- SPP (`0x1101`) is listed too, but it answers every frame with ASCII `FAIL`. The app only uses
  SPP/GATT-over-BR/EDR for speakers that advertise a 128-bit UUID containing `04ff<pid>`;
  this one doesn't.

### Link ownership (verified, important)

The speaker closes the ATT channel ~4–5 s after it opens **if the classic link was created by
the speaker** (e.g. its automatic audio reconnect after power-on). On a link the host created
(BlueZ `Device1.Connect`), the channel stays open indefinitely. So to (re)connect reliably:
disconnect the device in BlueZ if the speaker linked itself, call `Device1.Connect`, wait for
the A2DP transport to appear, then open PSM 31. Sessions on host-created links also answer
`AA 11 00` with device info (`AA 12 05 00 44 02 16 80`); speaker-created ones don't.

### Keep-alive

The app sends `AA EA 00` (heartbeat) every 5 s (`HmDevice.delayDuration`); the speaker can
change the interval with command `0xEB` (payload = seconds).

## Frames

```
AA <cmd> <len> <payload…>
```

`AA` is the identifier, `len` is the payload length. No checksum. Notifications can carry
several frames back to back, so parse them as a stream.

Generic acknowledgement from the speaker: `AA 00 02 <cmd> <status>` (status `00` = OK).

## Commands

`<i>` = device index, `00` for the connected speaker.

### Player (verified)

| Action | Frame |
|---|---|
| Set volume (0–32; the app's slider max is 32) | `AA 43 04 <i> 42 01 <vol>` |
| Primary / secondary volume (stereo pair of different models) | `AA 43 04 <i> 46 01 <vol>` / `… 47 01 <vol>` |
| Mute on/off | `AA 43 04 <i> 43 01 <01/00>` |
| Pause / play / previous / next | `AA 43 04 <i> 41 01 <01/02/03/04>` |
| Request player info | `AA 41 00` |

Player info reply / notification: `AA 42 <len> <i>` followed by `<token> <len> <value>` items.
Tokens: `41` status (0 off, 1 paused, 2 playing), `42` volume, `43` mute, `44` song (ASCII),
`45` artist (ASCII), `46`/`47` primary/secondary volume. The speaker pushes these by itself
when something changes (e.g. the volume knob), and also replies `Not Playing` for song/artist.

### Sound

| Action | Frame |
|---|---|
| Bass boost off / 1 / 2 (verified read) | `AA 63 01 <00/01/02>`; request `AA 61 00` → `AA 62 01 <level>` |
| Custom EQ (verified read) | `AA 93 06 <i> C1 <on 01/off 00> <bass> <mid> <treble>`; request `AA 91 01 00` |
| Feedback tones (verified read) | `AA F3 01 <01/00>`; request `AA F1 00` → `AA F2 01 <state>` |
| Volume boost | `AA 13 04 <i> 54 01 <01/00>` |

EQ band bytes: the app maps levels −2…+2 to bytes `04 03 02 01 00` (`EQFragment.mapCustomLevelToBandValues`).
EQ reply seen: `AA 92 08 00 C1 01 00 02 02 00 00` (category, on, bands, plus two extra bytes).

### Light show

| Action | Frame |
|---|---|
| Main switch (newer models) | `AA 33 04 <i> 99 01 <01/00>` |
| Pattern (older models turn lights off with pattern `00`) | `AA 33 04 <i> 31 01 <pattern>` |
| Static colour | `AA 33 09 <i> 36 01 01 32 03 <r> <g> <b>` |
| Request light info | `AA 31 00` → `AA 32 …` |

Patterns: 0 off, 1 Rock, 2 Neon, 3 Club, 4 Flow, 5 Ripple, 6 Cross, 7 Flash, 8 custom, 9 Loop,
10 Bounce, 11 Trim, 12 Switch, 13 Freeze. The app picks main switch vs pattern OFF per model
(`LightShowControlViewModel`); sending both is harmless (both are ACKed on the test speaker).

### Device

| Action | Frame |
|---|---|
| Request device info | `AA 11 00` → `AA 12 …` |
| Request battery | `AA 13 02 <i> 33` (test speaker only ACKs) |
| Rename (≤16 bytes UTF-8) | `AA 13 <len+3> <i> C1 <len> <name>` |
| Lock top panel | `AA 13 04 <i> 55 01 <01/00>` |
| Power off | `AA 03 01 04` |
| Request feature info | `AA A1 00` → `AA A2 …` (not parsed) |

Device info (`AA 12 <len> <i>` + `<token> <len> <value>`, verified format): `31` product id,
`32` model id, `33` battery (bit 7 charging, bits 0–6 percent, `F0` = mains without battery),
`36` audio source (0 none, 1 Bluetooth, 2 aux, 3 USB, 4 optical, 7 USB audio), `37` MAC,
`40` serial, `41` firmware, `44` settings CRC (also in the BLE advertisement), `C1` name.

### Unsolicited frames seen

| Frame | Meaning |
|---|---|
| `AA 85 06 <mac>` | `SET_SECONDARY_SPEAKER_ADDRESS`: address of a linked partner speaker |
| `AA 54 02 <n> <n>` | DJ-effect status (`NOTIFY_DJEFFECT_STATUS`) |
| `AA 74 02 <n> <n>` | Karaoke status (`NOTIFY_KARAOKE_STATUS`) |
| `AA 12 04 00 36 01 00` | Audio source changed |

# AGENTS.md

Guide for AI coding assistants (Claude, Codex, Copilot, Cursor, …) working on this repo.
Read this first; [PROTOCOL.md](PROTOCOL.md) has the byte-level details.

## What this is

A Home Assistant app (add-on) that controls a JBL PartyBox speaker over Bluetooth, replacing
the official JBL PartyBox phone app. The protocol was reverse engineered from the Android app
(v3.14.20). It has a web UI (HA ingress panel) and publishes the speaker to HA via MQTT
discovery for automations. It's in daily use on the owner's HA OS install with the Mosquitto app.

## Layout

| Path | Purpose |
|---|---|
| `repository.yaml` | HA add-on repository manifest |
| `jbl_partybox/config.yaml` | Add-on manifest: version, options, `host_dbus`, `host_network`, `services: mqtt:want` |
| `jbl_partybox/Dockerfile`, `requirements.txt` | `python:3.12-slim` image; bleak, aiohttp, dbus-fast, aiomqtt (pinned) |
| `jbl_partybox/DOCS.md`, `CHANGELOG.md`, `icon.png`, `logo.png` | Shown by HA in the app's Documentation/Changelog tabs and store |
| `jbl_partybox/app/protocol.py` | Frame builders/parsers (pure, no I/O) |
| `jbl_partybox/app/att.py` | Minimal GATT client over classic Bluetooth (ATT on L2CAP PSM 31) |
| `jbl_partybox/app/bluez.py` | BlueZ D-Bus helpers: device info/dump, link state, host-side relink |
| `jbl_partybox/app/speaker.py` | `SpeakerManager`: connect, transports, reconnect loop, watchdog, actions, log |
| `jbl_partybox/app/mqtt_bridge.py` | MQTT discovery entities + command handling |
| `jbl_partybox/app/server.py` | aiohttp: ingress UI, REST (`api/…`), WebSocket push, startup |
| `jbl_partybox/app/static/index.html` | Single-file vanilla JS UI (relative URLs for ingress) |
| `tests/` | Standalone simulation scripts (fakes for BlueZ/sockets/broker); run individually |
| `tools/ble_test.py` | Direct bleak CLI test; `tools/mock_server.py [--demo]` UI with a fake speaker |
| `tools/make_icons.py` | Regenerates `icon.png`/`logo.png` (Pillow, Windows fonts) |
| `docs/` | README images: icon and light/dark screenshots (taken from `mock_server.py --demo`) |
| `tools/decompile/` | How to regenerate the decompiled APK (not committed) |

## How a connection works (hard-won; don't undo without new evidence)

1. **BLE first** (bleak). On this speaker it never works in practice: HA's host is connected to
   the speaker over classic Bluetooth for A2DP audio, bleak sees "already connected" and finds no
   GATT services (`NoGattError`).
2. **GATT over classic Bluetooth** (`att.AttLink`): L2CAP SEQPACKET to PSM 31, discover
   characteristics, write commands to TX `…0002`, notifications arrive on RX `…0001`. There is
   no usable CCCD; notifications come anyway. Needs `host_network: true` (AF_BLUETOOTH sockets
   only exist in the host network namespace).
3. **Link ownership rule**: the speaker closes our ATT channel ~4–5 s after opening if the
   classic link was created *by the speaker* (its auto-reconnect after power-on). On a link
   created by the host (`Device1.Connect`) it stays up. Therefore the reconnect loop always
   re-creates the link from the host (`bluez.relink`), waits for the A2DP transport
   (`MediaTransport1`), settles 1 s, then opens ATT. Startup auto-connect uses the same path.
4. The speaker may send ATT *requests* to us on the same channel; `AttLink` must answer them
   (and only treat response opcodes as replies to its own requests).
5. Watchdog: 30 s of silence → probe with `AA 41 00`; 60 s → drop and reconnect.
6. The working transport ("gatt"/"att") is saved in `/data/last_device.json`.

Dead ends already tried: SPP/RFCOMM (speaker answers `FAIL`), removing the device from BlueZ
(doesn't force BLE), waiting longer before connecting on a speaker-created link (still dropped).

## Gotchas

- `connect()` returns only after the initial queries (~1.5 s); a drop during that time must be
  retried by the reconnect loop, which checks `status` after `connect()` returns.
- `_on_disconnect` ignores events unless `status == "connected"`; `connect()` handles its own failures.
- Device info replies are TLV (`token len value`), not the fixed-length layout the app's SPP code uses.
- Lights: send both main switch (`0x99`) and pattern OFF/ON; which one a model honours varies.
- Volume boost / lock panel aren't reported by the speaker; UI/MQTT show the last value set.
- MQTT: retained `…/set` messages are ignored on purpose (stale replays on every reconnect).
  The owner has an HA automation that sets the volume on connect; that's intentional.
- HA ingress serves the UI under a path prefix: keep all URLs relative (`api/state`, not `/api/state`).
- `server.py` only accepts the ingress gateway `172.30.32.2` and localhost (`ALLOWED_CLIENTS`):
  with `host_network` the server is otherwise open to the whole LAN without authentication.
- Bump `version` in `jbl_partybox/config.yaml` on every change, or HA won't offer the update;
  add a matching entry to `jbl_partybox/CHANGELOG.md` and update the version badge in `README.md`.

## Working on it

- Changes are verified with the simulation scripts in `tests/` (they monkeypatch BlueZ, sockets
  and timings) and then by the owner on real hardware. Real Bluetooth can't run on Windows dev
  machines; ask the owner for the panel's **Debug** log (every tx/rx frame plus connection
  events) when behaviour on the speaker matters.
- Deploy: copy `jbl_partybox/` to `/addons/` on the HA host (Samba share `addons`), then
  Update/Rebuild in Settings → Apps.
- Dev setup: `pip install -r requirements-dev.txt` (dbus-fast is Linux-only; on Windows the
  BlueZ helpers degrade to no-ops). On Windows, aiomqtt needs `WindowsSelectorEventLoopPolicy`.
- To trace a protocol detail, regenerate `decompiled/` (see `tools/decompile/README.md`) and
  start from `com/harman/sdk/utils/PacketFormat.java` and `impl/*ControlImpl.java`.
- Don't commit APKs or decompiled sources (JBL's copyrighted code; `.gitignore` covers them).
- The repo is public: never commit real MAC addresses, speaker names, logs from the owner's
  setup or personal details. Use placeholders like `12:34:56:78:9A:BC` / "Living Room".
- Keep the AI disclaimer in `README.md` and `jbl_partybox/DOCS.md` accurate if the testing
  situation changes (e.g. more models verified).

## Style

Python 3.12, asyncio throughout, no frameworks beyond aiohttp/bleak/dbus-fast/aiomqtt. Short
functions, comments explain *why* (often citing the decompiled class that justified a choice).
Debug-log anything a remote tester needs to diagnose a failure.

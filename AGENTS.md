# AGENTS.md

Guide for AI coding assistants (Claude, Codex, Copilot, Cursor, …) working on this repo.
Read this first; [PROTOCOL.md](PROTOCOL.md) has the byte-level details, and
[Project status](#project-status) says where the work stands.

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
| `jbl_partybox/app/bluez.py` | BlueZ D-Bus helpers: adapters and health check, discovery, pairing, device info/dump, link state, host-side relink |
| `jbl_partybox/app/speaker.py` | `SpeakerManager`: connect, transports, reconnect loop, watchdog, actions, log |
| `jbl_partybox/app/mqtt_bridge.py` | MQTT discovery entities + command handling |
| `jbl_partybox/app/diagnostics.py` | Scrubbed report for bug reports (pure, tested) |
| `jbl_partybox/app/server.py` | aiohttp: ingress UI, REST, WebSocket push, startup. Routes under `api/`: `state`, `scan`, `connect` (optional `pair`), `disconnect`, `action`, `adapter`, `adapter/power`, `autoconnect`, `autoconnect/resume`, `forget`, `diagnostics`, `ws` |
| `jbl_partybox/app/static/index.html` | Single-file vanilla JS UI (relative URLs for ingress) |
| `tests/` | Simulation scripts with asserts; `run_all.py` runs them in parallel, `fastclock.py` simulates time |
| `.github/workflows/tests.yml` | Runs `tests/run_all.py` on pushes and pull requests |
| `tools/ble_test.py` | Direct bleak CLI test; `tools/mock_server.py [--demo]` UI with a fake speaker |
| `tools/make_icons.py` | Regenerates `icon.png`/`logo.png` (Pillow, Windows fonts) |
| `docs/` | README images: icon and light/dark screenshots (taken from `mock_server.py --demo`) |
| `tools/decompile/` | How to regenerate the decompiled APK (not committed) |

## How a connection works (hard-won; don't undo without new evidence)

1. **Transport order.** A speaker remembered with transport "att" tries classic Bluetooth first,
   with BLE only as a fallback. Any other speaker tries BLE (bleak) first. On the tested speaker
   BLE never works in practice: HA's host is connected to it over classic Bluetooth for A2DP audio,
   bleak sees "already connected" and finds no GATT services (`NoGattError`), which triggers step 2.
2. **GATT over classic Bluetooth** (`att.AttLink`): L2CAP SEQPACKET to PSM 31, discover
   characteristics, write commands to TX `…0002`, notifications arrive on RX `…0001`. There is
   no usable CCCD; notifications come anyway. Needs `host_network: true` (AF_BLUETOOTH sockets
   only exist in the host network namespace).
3. **Link ownership rule**: the speaker closes our ATT channel ~4–5 s after opening if the
   classic link was created *by the speaker* (its auto-reconnect after power-on). On a link
   created by the host (`Device1.Connect`) it stays up. Therefore the reconnect loop always
   re-creates the link from the host (`bluez.relink`), waits for the A2DP transport
   (`MediaTransport1`), settles 1 s, then opens ATT. Startup auto-connect uses the same path, and
   `_manual_connect` re-links first when the speaker is already linked or remembered as "att".
4. The speaker may send ATT *requests* to us on the same channel; `AttLink` must answer them
   (and only treat response opcodes as replies to its own requests).
5. Watchdog: 30 s of silence → probe with `AA 41 00`; 60 s → drop and reconnect.
6. `/data/last_device.json` stores the last speaker, the auto-connect setting (`last`, `off` or an
   address), the selected adapter and every speaker connected to before, with its working transport
   ("gatt"/"att"). Auto-connect is set in the panel; the `auto_connect` option in `config.yaml` is
   only the starting value when the file has no setting yet.
7. User actions beat auto-connect: `scan()` and `connect_to()` call `_stop_connecting()`, which
   cancels the reconnect and manual tasks and aborts a BlueZ connect in flight with
   `Device1.Disconnect`. `_hold_auto()` then pauses auto-connect for `AUTO_HOLD_SECONDS`; the pause
   must always expire, or one forgotten scan would end auto reconnect for good.

Dead ends already tried: SPP/RFCOMM (speaker answers `FAIL`), removing the device from BlueZ
(doesn't force BLE), waiting longer before connecting on a speaker-created link (still dropped).

## Gotchas

- Capabilities (`protocol.capabilities`) only hide a control on evidence: an explicit `0` in the
  speaker's `AA A2` report, or the model table. A missing token keeps the control, because hiding
  something that works is worse than offering something that doesn't.
- `bluez.adapter` is the selected adapter. Every Device1 lookup must go through `_on_adapter`, and
  `att.source_address` binds the L2CAP socket to it.
- `diagnostics.py` must never let MAC addresses, names, serials or song titles through. Add a case
  to `tests/test_features.py` when a new frame carries personal data.
- Tests that import `fastclock` run on simulated time: `asyncio.sleep` returns at once, so use it
  freely, and anything needing real time (the MQTT broker) must not import it.
- `connect()` returns only after the initial queries (~1.5 s); a drop during that time must be
  retried by the reconnect loop, which checks `status` after `connect()` returns.
- `_on_disconnect` ignores events unless `status == "connected"`; `connect()` handles its own failures.
- Device info replies are TLV (`token len value`), not the fixed-length layout the app's SPP code uses.
- Lights: send both main switch (`0x99`) and pattern OFF/ON; which one a model honours varies.
- Volume boost / lock panel: the tested speaker doesn't report them, so UI/MQTT show the last value
  set. `parse_device_info` still reads their tokens in case another model sends them.
- The Dockerfile copies `config.yaml` into the image so `diagnostics.py` can read the app version;
  without it the report says "unknown".
- MQTT: retained `…/set` messages are ignored on purpose (stale replays on every reconnect).
  The owner has an HA automation that sets the volume on connect; that's intentional.
- HA ingress serves the UI under a path prefix: keep all URLs relative (`api/state`, not `/api/state`).
- `server.py` only accepts the ingress gateway `172.30.32.2` and localhost (`ALLOWED_CLIENTS`):
  with `host_network` the server is otherwise open to the whole LAN without authentication.
- HA only offers an update when `version` in `jbl_partybox/config.yaml` changes. See
  [Versions and releases](#versions-and-releases).

## Working on it

- Changes are verified with `python tests/run_all.py` (the scripts monkeypatch BlueZ, sockets and
  timings) and then by the owner on real hardware. Real Bluetooth can't run on Windows dev
  machines; ask the owner for the panel's **Debug** log (every tx/rx frame plus connection
  events) when behaviour on the speaker matters.
- Deploy: assistants have no access to the HA host, so don't look for it. The owner copies
  `jbl_partybox/` to `/addons/` on the host (Samba share `addons`) and runs Update/Rebuild in
  Settings → Apps. To hand over a test build, make sure the folder is ready; don't zip it.
- Dev setup: create a virtual environment, then `pip install -r requirements-dev.txt` (dbus-fast is
  Linux-only; on Windows the BlueZ helpers degrade to no-ops). On Windows, aiomqtt needs
  `WindowsSelectorEventLoopPolicy`. CI and the Docker image use Python 3.12, so prefer that locally.
- UI preview: `python tools/mock_server.py --demo`. The owner's `.claude/launch.json` isn't in the
  repo and may point to a virtual environment that no longer exists; recreate it if so.
- Retake the README screenshots in `docs/` when the UI changes visibly.
- To trace a protocol detail, regenerate `decompiled/` (see `tools/decompile/README.md`) and
  start from `com/harman/sdk/utils/PacketFormat.java` and `impl/*ControlImpl.java`. The APKs and
  `decompiled/` exist only on the owner's PC (APKs in the ignored `apks/` folder), never in a clone.
- Don't commit APKs or decompiled sources (JBL's copyrighted code; `.gitignore` covers them).
- The repo is public: never commit real MAC addresses, speaker names, logs from the owner's
  setup or personal details. Use placeholders like `12:34:56:78:9A:BC` / "Living Room".
- Keep the AI disclaimer in `README.md` and `jbl_partybox/DOCS.md` accurate if the testing
  situation changes (e.g. more models verified).

## Versions and releases

- Bump the version once per commit that changes the app (`jbl_partybox/`). Everything in that
  commit goes in one changelog entry, even if it was built across several requests. Don't bump for
  changes outside the app, such as docs or CI.
- A bump changes three places: `version` in `jbl_partybox/config.yaml`, a new entry in
  `jbl_partybox/CHANGELOG.md` and the version badge in `README.md`.
- Commits go directly to `main`. Commit, push and release only when the owner asks; each is a
  separate request, and approval for one doesn't cover the next.
- A release is a GitHub release tagged `vX.Y.Z` on the pushed commit, with notes based on the
  changelog. Pushing alone updates HA users but not the Releases page.
- `tests/run_all.py` also runs in CI on every push to `main`; check that it passed after pushing.

## Project status

Last updated for version 1.2.0. Keep this short, and free of personal details.

- **Released:** 1.2.0 is the latest release. The next version is 1.3.0.
- **Hardware:** only one PartyBox model has been tested. The owner reported that 1.2.0 looks good on
  it, without saying which features were tried. So treat these as unproven on hardware until someone
  confirms them: pairing, adapter selection, classic Bluetooth scanning, aborting a BlueZ connect,
  and the `AA A2` feature report (whose reply format is known only from the decompiled app).
- **Decided against for now:** adding the Encore 2 AI and On-The-Go 2 AI to the model table, and
  publishing the model list below in the user docs.

### Models in the app (3.14.20)

From `assets/ProductListConfig.json` and `AddNewProductConfig.json`. This shows what the JBL app
supports, not what this project has tested. The app also loads a newer list remotely.

| Group | Models (product id) |
|---|---|
| MCS platform | 110 (`1f5f`), 310 (`1f5d`), 710 (`1f61`), Encore (`2062`), Encore Essential (`2063`) |
| QCC platform | Club 120 (`20e2`), Stage 320 (`20dd`), 520 (`2120`), 720 (`2156`), Encore 2 (`2118`), Encore Essential 2 (`212e`), On-The-Go 2 (`215b`) |
| Constants only | Encore 2 AI (`2173`), On-The-Go 2 AI (`216a`); not in `protocol.PRODUCTS`, so no model name shows |
| Likely problem | Ultimate (`2095`): MTK platform, Wi-Fi model, notifies on TX, but `AttLink` only listens on RX |
| Not in the APK | PartyBox 100, 200, 300, 1000 and the original On-The-Go |

## Style

Python 3.12, asyncio throughout, no frameworks beyond aiohttp/bleak/dbus-fast/aiomqtt. Short
functions, comments explain *why* (often citing the decompiled class that justified a choice).
Debug-log anything a remote tester needs to diagnose a failure.

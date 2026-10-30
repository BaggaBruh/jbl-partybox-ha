# Changelog

## 1.2.0

- **Pair from the panel:** Scan also finds speakers in pairing mode, and **Pair and connect** pairs
  and trusts them.
- **Settings** menu (gear icon) with the Bluetooth adapter selector, the auto-connect setting and
  a diagnostics download.
- **Bluetooth health check:** the panel says when there's no adapter, BlueZ can't be reached or the
  adapter is off, with a button to switch it on.
- **Model capabilities:** the panel and MQTT only offer what your speaker supports, using the
  speaker's own feature report and the model table from the JBL app. Shows the model name.
- **Download diagnostics:** a report for bug reports with personal details removed.

## 1.1.0

- The panel lists speakers you connected to before and paired audio devices, not only scan results.
- New **Auto-connect** setting in the panel: last used speaker, a specific speaker, or off.
  It replaces the `auto_connect` option, whose value is used once as the starting point.
- Scanning or pressing Connect force-stops auto-connect, including a Bluetooth connect in flight.
  It stays paused for 2 minutes after a scan or failed connect, then resumes (or use **Resume now**).
- A new Connect replaces one in progress; connections in progress can be cancelled.
- A manual connect sets up the Bluetooth link from the host, like automatic reconnects.
- If a speaker you switched to disappears, the app returns to the auto-connect speaker after 1 minute.
- **Forget** removes a speaker from the panel's list (it stays paired in Bluetooth).

## 1.0.0: first public release

- Control a JBL PartyBox from Home Assistant: volume, mute, playback, bass boost, custom EQ,
  light show, feedback tones, volume boost, panel lock, rename and power off.
- Sidebar panel with a debug console, and 22 MQTT-discovered entities for automations.
- Works when Home Assistant is also the speaker's audio source, through GATT over classic
  Bluetooth, with fast host-initiated auto reconnect.
- Tested on one PartyBox model; reports from other models are welcome.

## 0.4.3

- Security: the web panel only accepts connections from the Home Assistant ingress gateway.
  Before, with host networking, anyone on the LAN could reach it on port 8099.

## 0.4.2

- Every reconnect re-creates the Bluetooth link from the host, the only kind of link the
  speaker keeps the control channel open on. Reconnects take ~5–10 s instead of 20–30 s.
- Startup auto-connect uses the same path, so the first connection after a restart is stable.
- Host connects to a speaker that's off time out after 12 s instead of hanging for 30 s.
- Retained MQTT commands are ignored.

## 0.4.1

- Faster retries: no slow BLE scan on automatic retries, BlueZ polled every second.
- Debug log shows how long each reconnect took.

## 0.4.0

- MQTT discovery: the speaker appears in Home Assistant with 22 entities (volume, mute,
  playback, bass boost, EQ, light show, settings, battery, connectivity).
- Watchdog reconnects links that die silently.

## 0.3.x

- GATT over classic Bluetooth (ATT on L2CAP PSM 31) for speakers that are connected to the
  host for audio; replaces the SPP attempt.
- Answers the speaker's own ATT requests; tolerates the missing CCCD.
- Re-links from the host after a quick drop; waits for audio before opening the channel.
- Light toggle sends both the main-switch and pattern-OFF commands.
- Device info parsed in the speaker's token/length/value format.

## 0.2.0

- Classic Bluetooth fallback when no BLE GATT service is found.

## 0.1.x

- First version: BLE GATT connection, web UI, PartyBox protocol from the Android app.
- Finds the PartyBox characteristics in any GATT service.

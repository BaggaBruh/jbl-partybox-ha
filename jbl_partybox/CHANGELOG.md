# Changelog

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

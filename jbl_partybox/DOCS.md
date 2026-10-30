# JBL PartyBox

Control a JBL PartyBox speaker from Home Assistant over Bluetooth: volume, playback, bass boost,
EQ, light show and speaker settings, from the sidebar panel or from automations.

## Before you start

- The Home Assistant host needs a Bluetooth adapter within range of the speaker.
- Close the JBL PartyBox app on your phone: the speaker accepts one control connection at a time.
- For automations, install the **Mosquitto broker** app and set up the **MQTT** integration.
  This app finds the broker and its credentials by itself.

## Connecting

1. Open **PartyBox** in the sidebar.
2. Press **Scan**, then **Connect** next to your speaker.

The speaker is remembered: the app reconnects after drop-outs, power cycles and restarts.
**Disconnect** in the panel stops that until you connect again.

The list shows speakers you connected to before and audio devices paired with the host, even when
they're off, so you don't have to scan for them. **Forget** removes a speaker from the list; it
doesn't unpair it.

### Adding a new speaker

Put the speaker in Bluetooth pairing mode (usually a long press on its Bluetooth button), press
**Scan**, then **Pair and connect**.

### Settings

The gear icon opens the settings: the **Bluetooth adapter** to use (useful with a USB dongle, and
speakers have to be paired on the adapter you choose), **Auto-connect** and **Download diagnostics**.

### Auto-connect

The **Auto-connect** setting picks what the app connects to by itself: the last used
speaker (default), a specific speaker, or nothing.

**Scan** and **Connect** always win. They stop any automatic attempt immediately, and auto-connect
stays paused for 2 minutes after a scan or a failed connect so you can choose in peace; **Resume
now** ends the pause early. If you switch to another speaker and it disappears, the app retries it
for a minute and then returns to the auto-connect speaker. A speaker that's already connected stays
connected during a scan.

## Automations

The speaker shows up under Settings → Devices & services → MQTT with entities for volume, mute,
play/pause/next/previous, bass boost, custom EQ, the light show (on/off, colour, patterns as
effects), feedback tones, volume boost, panel lock, power off, player status, song, artist,
audio source and a **Connected** sensor. Everything except **Connected** is unavailable while
the speaker is disconnected, so you can trigger on **Connected** turning on, for example to set
a start-up volume.

## Options

| Option | Default | Description |
|---|---|---|
| `heartbeat_interval` | `5` | Seconds between keep-alive packets |
| `scan_timeout` | `8` | Seconds per scan |
| `mqtt_discovery` | `true` | Publish the speaker to Home Assistant over MQTT |
| `discovery_prefix` | `homeassistant` | MQTT discovery prefix |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | | Only for a broker other than the Mosquitto app |

## Troubleshooting

Open **Debug** at the bottom of the panel. It logs every frame sent to and received from the
speaker, plus connection events, and lets you send raw frames such as `AA 41 00`.

- **A red notice about Bluetooth:** the app can't find or use an adapter. If the adapter is off,
  the notice has a button to switch it on.
- **A control is missing:** your speaker reported that it doesn't support it, or the JBL app's
  model table says so.
- **Doesn't find the speaker:** make sure it's on and not connected to the JBL app.
- **Connects, then drops after a few seconds:** the app re-links from the host automatically;
  if it keeps happening, the `re-linking …` lines in the log show why.
- **A control does nothing:** not every model supports every command; the log shows whether the
  speaker acknowledged it (`ack … status 0`).

**Download diagnostics** in the settings saves a report for bug reports, with MAC addresses, names,
serial numbers and song titles removed.

Questions are welcome in [Discussions](https://github.com/BaggaBruh/jbl-partybox-ha/discussions);
please add your speaker model to the
[Tested models](https://github.com/BaggaBruh/jbl-partybox-ha/discussions/2) thread.

## Disclaimer

This app was built with substantial AI assistance (Anthropic's Claude) from a reverse-engineered,
unofficial protocol, and has been tested on one PartyBox model only. It's provided as is under
the MIT license. Not affiliated with JBL or Harman. See the
[project README](https://github.com/BaggaBruh/jbl-partybox-ha#ai-disclaimer) for details.

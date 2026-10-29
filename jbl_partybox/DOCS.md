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
| `auto_connect` | `true` | Reconnect to the last speaker automatically |
| `heartbeat_interval` | `5` | Seconds between keep-alive packets |
| `scan_timeout` | `8` | Seconds per scan |
| `mqtt_discovery` | `true` | Publish the speaker to Home Assistant over MQTT |
| `discovery_prefix` | `homeassistant` | MQTT discovery prefix |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | | Only for a broker other than the Mosquitto app |

## Troubleshooting

Open **Debug** at the bottom of the panel. It logs every frame sent to and received from the
speaker, plus connection events, and lets you send raw frames such as `AA 41 00`.

- **Doesn't find the speaker:** make sure it's on and not connected to the JBL app.
- **Connects, then drops after a few seconds:** the app re-links from the host automatically;
  if it keeps happening, the `re-linking …` lines in the log show why.
- **A control does nothing:** not every model supports every command; the log shows whether the
  speaker acknowledged it (`ack … status 0`).

## Disclaimer

This app was built with substantial AI assistance (Anthropic's Claude) from a reverse-engineered,
unofficial protocol, and has been tested on one PartyBox model only. It's provided as is under
the MIT license. Not affiliated with JBL or Harman. See the
[project README](https://github.com/BaggaBruh/jbl-partybox-ha#ai-disclaimer) for details.

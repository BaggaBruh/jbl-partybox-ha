"""Publishes the connected speaker to Home Assistant via MQTT discovery and handles its commands.

Credentials come from the add-on options, or else from the Supervisor's MQTT service (the
Mosquitto app), which the add-on requests with `services: mqtt:want`.
"""
import asyncio
import json
import logging
import os

import aiohttp
import aiomqtt

import protocol as p

log = logging.getLogger("mqtt")
BASE = "partybox"
BRIDGE_STATUS = f"{BASE}/bridge/status"
BASS_OPTIONS = ["Off", "Boost 1", "Boost 2"]
PATTERN_BY_NAME = {name: pid for pid, name in p.LIGHT_PATTERNS.items()}


def _switch(obj, name, key, icon, action=None, needs_state=True):
    return ("switch", obj, {
        "name": name, "icon": icon,
        "value_template": f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}",
        "payload_on": "ON", "payload_off": "OFF",
    }, action or key, key if needs_state else None)


# (component, object id, config, action, state key required before publishing (or None))
ENTITIES = [
    ("number", "volume", {
        "name": "Volume", "icon": "mdi:volume-high", "min": 0, "max": p.VOLUME_MAX, "step": 1,
        "mode": "slider", "value_template": "{{ value_json.volume }}",
    }, "volume", "volume"),
    _switch("mute", "Mute", "muted", "mdi:volume-off", "mute"),
    ("button", "play", {"name": "Play", "icon": "mdi:play"}, "play", None),
    ("button", "pause", {"name": "Pause", "icon": "mdi:pause"}, "pause", None),
    ("button", "next", {"name": "Next track", "icon": "mdi:skip-next"}, "next", None),
    ("button", "previous", {"name": "Previous track", "icon": "mdi:skip-previous"}, "prev", None),
    ("sensor", "player_status", {
        "name": "Player status", "icon": "mdi:play-pause",
        "value_template": "{{ value_json.player_status | default('unknown') }}",
    }, None, None),
    ("sensor", "song", {"name": "Song", "icon": "mdi:music",
                        "value_template": "{{ value_json.song | default('') }}"}, None, None),
    ("sensor", "artist", {"name": "Artist", "icon": "mdi:account-music",
                          "value_template": "{{ value_json.artist | default('') }}"}, None, None),
    ("sensor", "audio_source", {"name": "Audio source", "icon": "mdi:import",
                                "value_template": "{{ value_json.audio_source }}"}, None, "audio_source"),
    ("sensor", "battery", {
        "name": "Battery", "device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement",
        "value_template": "{{ value_json.battery }}",
    }, None, "battery"),
    ("binary_sensor", "charging", {
        "name": "Charging", "device_class": "battery_charging",
        "value_template": "{{ 'ON' if value_json.charging else 'OFF' }}",
    }, None, "battery"),
    ("select", "bass_boost", {
        "name": "Bass boost", "icon": "mdi:speaker", "options": BASS_OPTIONS,
        "value_template": "{{ " + json.dumps(BASS_OPTIONS) + "[value_json.bass_boost | int(0)] }}",
    }, "bass_boost", "bass_boost"),
    _switch("eq", "Custom EQ", "eq_enabled", "mdi:equalizer", "eq_enabled"),
    *[("number", f"eq_{band}", {
        "name": f"EQ {band}", "icon": "mdi:tune-vertical", "min": -2, "max": 2, "step": 1, "mode": "slider",
        "value_template": f"{{{{ value_json.eq_{band} }}}}", "entity_category": "config",
    }, f"eq_{band}", f"eq_{band}") for band in ("bass", "mid", "treble")],
    _switch("feedback_tone", "Feedback tones", "feedback_tone", "mdi:bell-ring"),
    # The speaker doesn't report these two, so they're always offered and show the last value set
    _switch("volume_boost", "Volume boost", "volume_boost", "mdi:volume-plus", needs_state=False),
    _switch("lock_panel", "Lock top panel", "lock_panel", "mdi:lock", needs_state=False),
    ("light", "light_show", {
        "name": "Light show", "icon": "mdi:led-strip-variant", "schema": "json",
        "supported_color_modes": ["rgb"], "effect": True, "effect_list": list(PATTERN_BY_NAME),
    }, "light", None),
    ("button", "refresh", {"name": "Refresh", "icon": "mdi:refresh", "entity_category": "diagnostic"},
     "refresh", None),
    ("button", "power_off", {"name": "Power off", "icon": "mdi:power"}, "power_off", None),
]


# Entities that depend on a capability of the speaker (protocol.capabilities)
ENTITY_CAPABILITY = {
    "eq": "eq", "eq_bass": "eq", "eq_mid": "eq", "eq_treble": "eq", "feedback_tone": "feedback_tone",
    "volume_boost": "volume_boost", "lock_panel": "lock_panel", "power_off": "power_off",
}


def _speaker_id(address):
    return address.replace(":", "").lower()


class MqttBridge:
    def __init__(self, mgr, options):
        self.mgr = mgr
        self.opts = options
        self.prefix = options.get("discovery_prefix") or "homeassistant"
        self._dirty = asyncio.Event()
        self._published = set()  # (speaker id, object id) with discovery sent
        self._known_ids = set()
        self.status = "starting"
        mgr.listeners.add(self._on_event)

    async def _on_event(self, event):
        if event.get("type") == "state":
            self._dirty.set()

    async def _credentials(self):
        if self.opts.get("mqtt_host"):
            return {
                "host": self.opts["mqtt_host"], "port": self.opts.get("mqtt_port") or 1883,
                "username": self.opts.get("mqtt_username") or None,
                "password": self.opts.get("mqtt_password") or None,
            }
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return None
        async with aiohttp.ClientSession() as http:
            async with http.get("http://supervisor/services/mqtt",
                                headers={"Authorization": f"Bearer {token}"}) as r:
                if r.status != 200:
                    return None
                data = (await r.json()).get("data") or {}
        return {"host": data.get("host"), "port": data.get("port") or 1883,
                "username": data.get("username"), "password": data.get("password")}

    async def run(self):
        while True:
            creds = None
            try:
                creds = await self._credentials()
            except Exception as e:
                log.warning("MQTT service lookup failed: %s", e)
            if not creds or not creds.get("host"):
                self.status = "no MQTT broker (install Mosquitto or set mqtt_host)"
                await asyncio.sleep(60)
                continue
            try:
                will = aiomqtt.Will(BRIDGE_STATUS, "offline", retain=True)
                async with aiomqtt.Client(creds["host"], int(creds["port"]), username=creds["username"],
                                          password=creds["password"], will=will,
                                          identifier="partybox-app") as client:
                    self.status = f"connected to {creds['host']}:{creds['port']}"
                    log.info("MQTT %s", self.status)
                    self._published.clear()
                    await client.publish(BRIDGE_STATUS, "online", retain=True)
                    await client.subscribe(f"{BASE}/+/+/set")
                    await client.subscribe(f"{self.prefix}/status")
                    publisher = asyncio.create_task(self._publisher(client))
                    try:
                        async for msg in client.messages:
                            await self._on_message(msg)
                    finally:
                        publisher.cancel()
            except aiomqtt.MqttError as e:
                self.status = f"MQTT error: {e}"
                log.warning("%s; retrying in 10 s", self.status)
                await asyncio.sleep(10)

    async def _publisher(self, client):
        self._dirty.set()
        while True:
            await self._dirty.wait()
            self._dirty.clear()
            try:
                await self._publish_all(client)
            except aiomqtt.MqttError as e:
                log.warning("publish failed: %s", e)
            await asyncio.sleep(0.2)  # coalesce bursts (e.g. dragging the volume slider)

    async def _publish_all(self, client):
        mgr = self.mgr
        if not mgr.address:
            return
        sid = _speaker_id(mgr.address)
        state = dict(mgr.state)
        connected = mgr.status == "connected"
        # Other speakers we published before are now offline
        for other in self._known_ids - {sid}:
            await client.publish(f"{BASE}/{other}/availability", "offline", retain=True)
            await client.publish(f"{BASE}/{other}/connected", "OFF", retain=True)
        if not connected and mgr.address not in mgr.known and sid not in self._known_ids:
            return  # a speaker being tried for the first time isn't a Home Assistant device yet
        self._known_ids.add(sid)

        device = {
            "identifiers": [f"partybox_{sid}"], "connections": [["bluetooth", mgr.address]],
            "name": state.get("name") or self._device_name(), "manufacturer": "JBL", "model": "PartyBox",
        }
        if state.get("firmware"):
            device["sw_version"] = state["firmware"]
        base = f"{BASE}/{sid}"
        availability = {
            "availability": [{"topic": BRIDGE_STATUS}, {"topic": f"{base}/availability"}],
            "availability_mode": "all",
        }
        connected_cfg = {
            "name": "Connected", "device_class": "connectivity", "unique_id": f"partybox_{sid}_connected",
            "state_topic": f"{base}/connected", "availability_topic": BRIDGE_STATUS, "device": device,
        }
        await self._discover(client, sid, "binary_sensor", "connected", connected_cfg)
        caps = p.capabilities(state)
        for component, obj, cfg, action, needs in ENTITIES:
            if not caps.get(ENTITY_CAPABILITY.get(obj), True):
                # Discovery may already have gone out before the speaker reported its features
                if (sid, obj, None) not in self._published:
                    self._published = {k for k in self._published if k[:2] != (sid, obj)} | {(sid, obj, None)}
                    await client.publish(f"{self.prefix}/{component}/partybox_{sid}/{obj}/config", "", retain=True)
                continue
            if needs and needs not in state:
                continue
            full = {**cfg, **availability, "unique_id": f"partybox_{sid}_{obj}", "device": device}
            if component == "light":
                full.update(state_topic=f"{base}/light", command_topic=f"{base}/{obj}/set")
            else:
                if component != "button":
                    full["state_topic"] = f"{base}/state"
                if action:
                    full["command_topic"] = f"{base}/{obj}/set"
            await self._discover(client, sid, component, obj, full)

        await client.publish(f"{base}/connected", "ON" if connected else "OFF", retain=True)
        await client.publish(f"{base}/availability", "online" if connected else "offline", retain=True)
        await client.publish(f"{base}/state", json.dumps(state), retain=True)
        await client.publish(f"{base}/light", json.dumps(self._light_state(state)), retain=True)

    async def _discover(self, client, sid, component, obj, cfg):
        key = (sid, obj, cfg["device"]["name"])
        if key in self._published:
            return
        self._published = {k for k in self._published if k[:2] != (sid, obj)} | {key}
        await client.publish(f"{self.prefix}/{component}/partybox_{sid}/{obj}/config", json.dumps(cfg), retain=True)

    def _device_name(self):
        name = self.mgr.device_name(self.mgr.address)
        return name if name != self.mgr.address else f"PartyBox {name[-5:]}"

    @staticmethod
    def _light_state(state):
        out = {"state": "ON" if state.get("light_on") else "OFF", "color_mode": "rgb"}
        if state.get("light_pattern") in p.LIGHT_PATTERNS:
            out["effect"] = p.LIGHT_PATTERNS[state["light_pattern"]]
        color = state.get("light_color")
        if color:
            c = color.lstrip("#")
            out["color"] = {"r": int(c[0:2], 16), "g": int(c[2:4], 16), "b": int(c[4:6], 16)}
        return out

    async def _on_message(self, msg):
        topic = str(msg.topic)
        payload = msg.payload.decode() if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload)
        if topic == f"{self.prefix}/status":
            if payload == "online":  # HA restarted: resend discovery
                self._published.clear()
                self._dirty.set()
            return
        _, sid, obj, _ = topic.split("/")
        if not self.mgr.address or sid != _speaker_id(self.mgr.address):
            return
        if msg.retain:
            # A retained command is replayed by the broker on every (re)subscribe, e.g. forcing the
            # volume back each time the app reconnects. Only act on live commands.
            log.info("ignoring retained command %s=%s", topic, payload)
            return
        try:
            await self._command(obj, payload)
        except Exception as e:
            log.warning("command %s=%s failed: %s", obj, payload, e)

    async def _command(self, obj, payload):
        mgr, s = self.mgr, self.mgr.state
        on = payload.upper() == "ON"
        if obj == "volume":
            await mgr.action("volume", int(float(payload)))
        elif obj == "bass_boost":
            await mgr.action("bass_boost", BASS_OPTIONS.index(payload))
        elif obj == "eq" or obj.startswith("eq_"):
            eq = {"enabled": s.get("eq_enabled", True), "bass": s.get("eq_bass", 0),
                  "mid": s.get("eq_mid", 0), "treble": s.get("eq_treble", 0)}
            if obj == "eq":
                eq["enabled"] = on
            else:
                eq[obj[3:]], eq["enabled"] = int(float(payload)), True
            await mgr.action("eq", eq)
        elif obj == "light_show":
            cmd = json.loads(payload)
            if cmd.get("state") == "OFF":
                await mgr.action("light", False)
                return
            if "effect" in cmd and cmd["effect"] in PATTERN_BY_NAME:
                await mgr.action("light_pattern", PATTERN_BY_NAME[cmd["effect"]])
            if "color" in cmd:
                c = cmd["color"]
                await mgr.action("light_color", f"#{int(c['r']):02x}{int(c['g']):02x}{int(c['b']):02x}")
            if "effect" not in cmd and "color" not in cmd:
                await mgr.action("light", True)
        elif obj in ("mute", "feedback_tone", "volume_boost", "lock_panel"):
            await mgr.action(obj, on)
        elif obj in ("play", "pause", "next", "previous", "refresh", "power_off"):
            await mgr.action("prev" if obj == "previous" else obj)

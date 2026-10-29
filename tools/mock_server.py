"""Run the web UI against a simulated PartyBox (no Bluetooth).

    python tools/mock_server.py [--demo]     then open http://localhost:8099

--demo starts already connected, for screenshots.
"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"
sys.path.insert(0, str(APP))

import protocol as p  # noqa: E402
import speaker  # noqa: E402
import server  # noqa: E402

speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device.json"
server.OPTIONS_FILE = Path(tempfile.gettempdir()) / "options.json"


class FakeChar:
    uuid = p.TX_UUID
    properties = ["write-without-response"]


class FakeClient:
    """Simulated speaker: answers queries and ACKs setters."""

    def __init__(self, mgr):
        self.mgr = mgr
        self.is_connected = True
        self.mtu_size = 247
        self.vol, self.mute, self.bass, self.eq = 12, 0, 1, [0, 1, 2, 2, 2]

    async def write_gatt_char(self, _c, data, response=None):
        cmd, payload = data[1], data[3:]
        out = []
        if cmd == p.REQ_PLAYER_INFO:
            body = bytes([0, 0x41, 1, 2, 0x42, 1, self.vol, 0x43, 1, self.mute]) + bytes([0x44, 11]) + b"Weekend Mix" + bytes([0x45, 14]) + b"Party Playlist"
            out.append(p.frame(p.RESP_PLAYER_INFO, body))
        elif cmd == p.REQ_DEV_INFO:
            out.append(p.frame(p.RESP_DEV_INFO, bytes([0, 0x31, 2, 0x20, 0x6B, 0x32, 1, 1, 0x33, 1, 0x80 | 64, 0x36, 1, 1, 0xC1, 11]) + b"Living Room"))
        elif cmd == p.REQ_BASS_BOOST:
            out.append(p.frame(p.RET_BASS_BOOST, [self.bass]))
        elif cmd == p.REQ_SIMPLE_EQ:
            out.append(p.frame(p.RET_SIMPLE_EQ, [0, 0xC1, *self.eq[1:]]))
        elif cmd == p.REQ_FEEDBACK_TONE:
            out.append(p.frame(p.RET_FEEDBACK_TONE, [1]))
        elif cmd == p.SET_PLAYER_INFO:
            if payload[1] == p.TOK_VOLUME:
                self.vol = payload[3]
            if payload[1] == p.TOK_MUTE:
                self.mute = payload[3]
            out.append(p.frame(p.ACK, [cmd, 0]))
        elif cmd == p.HEARTBEAT:
            out.append(p.frame(p.ACK, [cmd, 0]))
        else:
            out.append(p.frame(p.ACK, [cmd, 0]))
        for f in out:
            # split notifications to exercise reassembly
            asyncio.get_event_loop().call_later(0.05, self.mgr._on_notify, None, bytearray(f[:4]))
            asyncio.get_event_loop().call_later(0.06, self.mgr._on_notify, None, bytearray(f[4:]))

    async def disconnect(self):
        self.is_connected = False


async def fake_scan(self):
    self.devices["12:34:56:78:9A:BC"] = {"address": "12:34:56:78:9A:BC", "name": "Living Room", "rssi": -56, "last_seen": time.time()}
    self.devices["AA:BB:CC:DD:EE:FF"] = {"address": "AA:BB:CC:DD:EE:FF", "name": "Garden", "rssi": -80, "last_seen": time.time()}
    await self._emit_state()
    return self.snapshot()["devices"]


async def fake_connect(self, address, ble_fallback=True):
    self.address, self.status, self.state = address, "connecting", {}
    await self._emit_state()
    await asyncio.sleep(0.5)
    self.client = FakeClient(self)
    self._tx, self._tx_response, self.transport = FakeChar(), False, "gatt"
    self._reader = p.FrameReader()
    self.status = "connected"
    await self._log("info", b"", f"connected to {address} (simulated)")
    await self._emit_state()
    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
    await self.refresh()


speaker.SpeakerManager.scan = fake_scan
speaker.SpeakerManager.connect = fake_connect
server.PORT = 8099
if "--demo" in sys.argv:
    # Start already connected (for screenshots) and show MQTT as connected
    import mqtt_bridge

    async def fake_bridge_run(self):
        self.status = "connected to core-mosquitto:1883"
        await asyncio.Event().wait()

    mqtt_bridge.MqttBridge.run = fake_bridge_run
    speaker.STATE_FILE.unlink(missing_ok=True)  # no background auto-reconnect competing with the demo
    _startup = server.on_startup

    async def demo_startup(app):
        await _startup(app)
        mgr = app["mgr"]
        await mgr.scan()
        asyncio.create_task(mgr.connect("12:34:56:78:9A:BC"))

    server.on_startup = demo_startup

server.main()

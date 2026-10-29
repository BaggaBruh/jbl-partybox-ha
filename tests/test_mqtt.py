import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import aiomqtt  # noqa: E402
from amqtt.broker import Broker  # noqa: E402

import mqtt_bridge  # noqa: E402
import speaker  # noqa: E402

PORT = 18830
ADDR = "12:34:56:78:9A:BC"


async def main():
    broker = Broker({
        "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{PORT}"}},
        "sys_interval": 0,
        "auth": {"allow-anonymous": True},
    })
    await broker.start()

    mgr = speaker.SpeakerManager(auto_connect=False)
    mgr.address, mgr.status = ADDR, "connected"
    mgr.state = {"name": "Living Room", "volume": 10, "muted": False, "player_status": "playing",
                 "song": "Not Playing", "bass_boost": 1, "eq_enabled": True, "eq_bass": 2, "eq_mid": 0,
                 "eq_treble": 0, "feedback_tone": True, "audio_source": "bluetooth"}
    actions = []

    async def fake_action(name, value=None):
        actions.append((name, value))
        if name == "volume":
            mgr.state["volume"] = value
        await mgr._emit_state()

    mgr.action = fake_action
    bridge = mqtt_bridge.MqttBridge(mgr, {"mqtt_host": "127.0.0.1", "mqtt_port": PORT})
    task = asyncio.create_task(bridge.run())

    seen = {}
    async with aiomqtt.Client("127.0.0.1", PORT, identifier="ha-test") as ha:
        await ha.subscribe("homeassistant/#")
        await ha.subscribe("partybox/#")

        async def collect(seconds):
            try:
                async with asyncio.timeout(seconds):
                    async for msg in ha.messages:
                        seen[str(msg.topic)] = msg.payload.decode()
            except TimeoutError:
                pass

        await collect(2)
        print("bridge status:", bridge.status)
        configs = sorted(t for t in seen if t.endswith("/config"))
        print(f"{len(configs)} discovery configs:")
        for t in configs:
            print("  ", t.split("/")[1], t.split("/")[3])
        vol_cfg = json.loads(seen["homeassistant/number/partybox_123456789abc/volume/config"])
        print("volume config:", {k: vol_cfg[k] for k in ("command_topic", "state_topic", "min", "max")},
              "| device:", vol_cfg["device"]["name"])
        print("state:", seen["partybox/123456789abc/state"][:80], "...")
        print("connected:", seen["partybox/123456789abc/connected"],
              "| availability:", seen["partybox/123456789abc/availability"],
              "| bridge:", seen["partybox/bridge/status"])
        print("light:", seen["partybox/123456789abc/light"])

        base = "partybox/123456789abc"
        await ha.publish(f"{base}/volume/set", "20")
        await ha.publish(f"{base}/mute/set", "ON")
        await ha.publish(f"{base}/bass_boost/set", "Boost 2")
        await ha.publish(f"{base}/eq_bass/set", "-1")
        await ha.publish(f"{base}/light_show/set", json.dumps({"state": "ON", "effect": "Club"}))
        await ha.publish(f"{base}/light_show/set", json.dumps({"state": "ON", "color": {"r": 255, "g": 0, "b": 16}}))
        await ha.publish(f"{base}/light_show/set", json.dumps({"state": "OFF"}))
        await ha.publish(f"{base}/previous/set", "PRESS")
        await ha.publish("partybox/aabbccddeeff/volume/set", "5")  # other speaker: ignored
        await collect(2)
        print("actions:", actions)
        print("state volume after command:", json.loads(seen[f"{base}/state"])["volume"])

        # speaker disconnects -> entities unavailable, connectivity OFF
        mgr.status = "disconnected"
        await mgr._emit_state()
        await collect(1)
        print("after disconnect: connected", seen[f"{base}/connected"], "availability", seen[f"{base}/availability"])

    task.cancel()
    await broker.shutdown()


asyncio.run(main())

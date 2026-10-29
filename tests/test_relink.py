"""Speaker closes our channel ~4 s in on a link it started; keeps it on a host-started link."""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import bluez  # noqa: E402
import speaker  # noqa: E402

speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_relink.json"
speaker.STATE_FILE.write_text('{"address": "12:34:56:78:9A:BC", "transport": "att"}')
speaker.SETTLE_SECONDS = 0.5
T0 = time.monotonic()
world = {"link_by": "speaker", "audio_at": 0.0}
attempts, relinks = [], []


class FakeLink:
    def __init__(self, mgr):
        self.mgr, self.is_connected = mgr, True

    async def write(self, data):
        pass

    def drop(self):
        if self.is_connected:
            self.is_connected = False
            self.mgr._on_link_closed()

    async def close(self):
        self.is_connected = False


async def fake_open_att(self, address):
    attempts.append((round(time.monotonic() - T0, 1), world["link_by"]))
    link = FakeLink(self)
    self.link, self.transport = link, "att"
    if world["link_by"] == "speaker":
        asyncio.get_event_loop().call_later(4, link.drop)


async def fake_relink(address, **kw):
    relinks.append(round(time.monotonic() - T0, 1))
    world["link_by"] = None
    await asyncio.sleep(1)
    world["link_by"], world["audio_at"] = "host", time.monotonic() + 2
    return None


async def fake_state(address):
    up = world["link_by"] is not None
    return {"connected": up, "audio": up and time.monotonic() >= world["audio_at"], "has_a2dp": True}


async def quick_refresh(self):
    await asyncio.sleep(1.5)


async def fake_open(self, address):
    raise speaker.BleakError("not found")


speaker.SpeakerManager._open_att = fake_open_att
speaker.SpeakerManager._open = fake_open
speaker.SpeakerManager.refresh = quick_refresh
bluez.relink, bluez.link_state = fake_relink, fake_state


async def main():
    m = speaker.SpeakerManager(heartbeat_interval=60, auto_connect=True)
    await m.connect("12:34:56:78:9A:BC")
    await asyncio.sleep(30)
    print(f"status {m.status}, attempts {attempts}, relinks at {relinks}")
    for e in m.log:
        if e["dir"] == "info":
            print("  ", e["note"])
    await m.disconnect()


asyncio.run(main())

"""Speaker drops the link 0.5 s into connect() (while queries are still going out), twice."""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastclock  # noqa: E402,F401
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import bluez  # noqa: E402
import speaker  # noqa: E402

speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_race.json"
speaker.STATE_FILE.write_text('{"address": "12:34:56:78:9A:BC", "transport": "att"}')
speaker.SETTLE_SECONDS = 0.2
T0 = time.monotonic()
attempts = []


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
    attempts.append(round(time.monotonic() - T0, 1))
    link = FakeLink(self)
    self.link, self.transport = link, "att"
    if len(attempts) <= 3:  # initial + two flaky reconnects
        asyncio.get_event_loop().call_later(0.5, link.drop)


async def slow_refresh(self):
    await asyncio.sleep(1.5)  # like the real initial queries


async def fake_open(self, address):
    raise speaker.BleakError("not found")


async def linked(address):
    return {"connected": True, "audio": True, "has_a2dp": True}


speaker.SpeakerManager._open_att = fake_open_att
speaker.SpeakerManager._open = fake_open
speaker.SpeakerManager.refresh = slow_refresh
bluez.link_state = linked
async def _relink_ok(a, **kw): return None
bluez.relink = _relink_ok


async def main():
    m = speaker.SpeakerManager(heartbeat_interval=60, auto_connect=True)
    await m.connect("12:34:56:78:9A:BC")
    t = time.monotonic()
    while time.monotonic() - t < 90:
        await asyncio.sleep(0.5)
        if len(attempts) >= 4 and m.status == "connected":
            break
    await asyncio.sleep(3)
    print(f"status {m.status}, attempts at {attempts}")
    assert m.status == "connected", m.status
    assert len(attempts) == 4, attempts
    await m.disconnect()


fastclock.run(main())

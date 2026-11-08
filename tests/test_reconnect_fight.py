"""A trusted speaker that reconnects itself first: the host loses the re-link race until it blocks."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastclock  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import asyncio  # noqa: E402

import bluez  # noqa: E402
import speaker  # noqa: E402

ADDR = "12:34:56:78:9A:BC"
speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_fight.json"
speaker.STATE_FILE.write_text(f'{{"address": "{ADDR}", "transport": "att"}}')
world = {"link": "speaker"}  # the speaker has already linked itself
relinks, opens, ble = [], [], []


class Link:
    def __init__(self, m):
        self.m, self.is_connected = m, True

    async def write(self, d):
        pass

    def drop(self):
        if self.is_connected:
            self.is_connected = False
            self.m._on_link_closed()

    async def close(self):
        self.is_connected = False


async def relink(address, timeout=30.0, block=False):
    relinks.append(block)
    await asyncio.sleep(1.5)
    if not block:  # the speaker's own reconnect beats our Connect
        world["link"] = "speaker"
        return "Connect: org.bluez.Error.Failed (br-connection-busy)"
    world["link"] = "host"
    return None


async def link_state(address):
    return {"connected": world["link"] is not None, "audio": True, "has_a2dp": True}


async def open_att(self, address):
    opens.append(world["link"])
    link = Link(self)
    self.link, self.transport = link, "att"
    if world["link"] == "speaker":
        asyncio.get_event_loop().call_later(4, link.drop)


async def open_ble(self, address):
    ble.append(time.monotonic())
    raise speaker.NoGattError("no GATT")


async def refresh(self):
    pass


speaker.SpeakerManager._open_att, speaker.SpeakerManager._open = open_att, open_ble
speaker.SpeakerManager.refresh = refresh
bluez.relink, bluez.link_state = relink, link_state


async def main():
    m = speaker.SpeakerManager(heartbeat_interval=60, auto_connect=True)
    t0 = time.monotonic()
    m.auto_reconnect(ADDR)
    while m.status != "connected" and time.monotonic() - t0 < 120:
        await asyncio.sleep(0.5)
    took = time.monotonic() - t0
    await asyncio.sleep(10)  # stays up on the host link
    notes = [e["note"] for e in m.log if e["dir"] == "info"]
    print(f"connected after {took:.1f} s; relinks (blocked?) {relinks}; opened on {opens}")
    assert m.status == "connected", m.status
    assert relinks[:speaker.BLOCK_AFTER] == [False] * speaker.BLOCK_AFTER, relinks
    assert relinks[speaker.BLOCK_AFTER] is True, "didn't block after losing the race"
    assert opens[-1] == "host", opens
    assert not ble, "tried BLE while the speaker was linked"
    assert any("br-connection-busy" in n for n in notes), "BlueZ's reason wasn't logged"
    assert took < 15, took
    await m.disconnect()


fastclock.run(main())

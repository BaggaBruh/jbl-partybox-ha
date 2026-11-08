"""A speaker once connected over BLE ("gatt") still gets a host link and GATT over classic first."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastclock  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import asyncio  # noqa: E402

import bluez  # noqa: E402
import speaker  # noqa: E402

ADDR = "12:34:56:78:9A:BC"
speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_transport.json"
speaker.SETTLE_SECONDS = 0
calls = []


class Link:
    is_connected = True

    async def write(self, d):
        pass

    async def close(self):
        self.is_connected = False


async def relink(address, timeout=30.0, block=False):
    calls.append("relink")


async def link_state(address):  # off before the host links it, like after a power cycle
    return {"connected": "relink" in calls, "audio": True, "has_a2dp": True}


async def open_att(self, address):
    calls.append("att")
    self.link, self.transport = Link(), "att"


async def open_ble(self, address):
    calls.append("ble")
    raise speaker.BleakError("br-connection-unknown")


async def refresh(self):
    pass


speaker.SpeakerManager._open_att, speaker.SpeakerManager._open = open_att, open_ble
speaker.SpeakerManager.refresh = refresh
bluez.relink, bluez.link_state = relink, link_state


async def main():
    speaker.STATE_FILE.write_text(json.dumps({
        "address": ADDR, "transport": "gatt", "autoconnect": "last",
        "known": {ADDR: {"name": "Living Room", "transport": "gatt", "last_connected": 1}},
    }))
    m = speaker.SpeakerManager(heartbeat_interval=60)
    await m.connect_to(ADDR)
    print("calls", calls, "| saved", m.known[ADDR]["transport"])
    assert m.status == "connected", m.status
    assert calls == ["relink", "att"], calls
    assert m.known[ADDR]["transport"] == "att"

    # A later BLE session doesn't take that back
    m._save_last(ADDR, "gatt")
    assert m.known[ADDR]["transport"] == "att", m.known
    await m.disconnect()


fastclock.run(main())

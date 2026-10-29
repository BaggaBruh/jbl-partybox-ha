"""A link that stays open but goes silent: the watchdog probes it, drops it and reconnects."""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import bluez  # noqa: E402
import speaker  # noqa: E402

speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_wd.json"
speaker.STATE_FILE.write_text('{"address": "AA:BB:CC:DD:EE:FF", "transport": "att"}')
speaker.WATCHDOG_SECONDS, speaker.SETTLE_SECONDS = 2, 0.2
opens, sent = [], []
T0 = time.monotonic()


class SilentLink:
    def __init__(self, mgr):
        self.mgr, self.is_connected = mgr, True

    async def write(self, data):
        sent.append(data.hex(" "))  # the speaker never answers

    async def close(self):
        self.is_connected = False


async def fake_open_att(self, address):
    opens.append(round(time.monotonic() - T0, 1))
    self.link, self.transport = SilentLink(self), "att"


async def nothing(self):
    pass


async def link_state(address):
    return {"connected": True, "audio": True, "has_a2dp": True}


async def relink(address, **kw):
    return None


speaker.SpeakerManager._open_att = fake_open_att
speaker.SpeakerManager.refresh = nothing
bluez.link_state, bluez.relink = link_state, relink


async def main():
    m = speaker.SpeakerManager(heartbeat_interval=1, auto_connect=True)
    await m.connect("AA:BB:CC:DD:EE:FF")
    await asyncio.sleep(12)
    print("opens at", opens, "| probes sent:", sum(1 for s in sent if s == "aa 41 00"))
    print([e["note"] for e in m.log if "reconnecting" in e["note"]])
    await m.disconnect()


asyncio.run(main())

"""Power-on to *stable* connection with the host-initiated reconnect strategy."""
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

speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_hostlink.json"


def run(autolink, off_for=20, startup=False):
    speaker.STATE_FILE.write_text('{"address": "12:34:56:78:9A:BC", "transport": "att"}')
    w = {"on": False, "on_at": None, "link": None, "audio_at": None}  # link: None | "speaker" | "host"
    log = []

    def t_on():
        return time.monotonic() - w["on_at"] if w["on_at"] else -1

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

    async def open_att(self, a):
        if not w["on"] or t_on() < 2.5:
            await asyncio.sleep(5)
            raise OSError(112, "Host is down")
        if w["link"] is None:
            w["link"] = "host"  # our L2CAP connect creates the ACL
        await asyncio.sleep(0.3)
        link = Link(self)
        self.link, self.transport = link, "att"
        log.append((round(t_on(), 1), "att on " + w["link"]))
        if w["link"] == "speaker":
            asyncio.get_event_loop().call_later(4, link.drop)

    async def open_ble(self, a):
        await asyncio.sleep(8)
        raise speaker.BleakError("not found")

    async def link_state(a):
        if w["on"] and autolink and w["link"] is None and t_on() >= 4:
            w["link"], w["audio_at"] = "speaker", time.monotonic() + 2
        audio = w["audio_at"] is not None and time.monotonic() >= w["audio_at"]
        return {"connected": w["link"] is not None, "audio": w["link"] is not None and audio, "has_a2dp": True}

    async def relink(a, timeout=30.0):
        log.append((round(t_on(), 1), "relink"))
        w["link"] = None
        if not w["on"] or t_on() < 2.5:
            await asyncio.sleep(min(5, timeout))
            return "Connect: org.bluez.Error.Failed"
        await asyncio.sleep(2)
        w["link"], w["audio_at"] = "host", time.monotonic() + 1
        return None

    async def refresh(self):
        await asyncio.sleep(0.5)

    speaker.SpeakerManager._open_att, speaker.SpeakerManager._open = open_att, open_ble
    speaker.SpeakerManager.refresh = refresh
    bluez.link_state, bluez.relink = link_state, relink

    async def main():
        m = speaker.SpeakerManager(heartbeat_interval=60, auto_connect=True)
        if startup:  # app starts while the speaker is already on and auto-linked
            w["on"], w["on_at"] = True, time.monotonic() - 30
            w["link"], w["audio_at"] = ("speaker", time.monotonic()) if autolink else (None, None)
            m.auto_reconnect("12:34:56:78:9A:BC")
            t0 = time.monotonic()
        else:
            m.address, m.status, m.transport = "12:34:56:78:9A:BC", "connected", "att"
            m.link = Link(m)
            m._connected_at = time.monotonic() - 14
            w["link"] = None
            m.link.drop()  # speaker switched off 14 s after connecting (like the log)
            await asyncio.sleep(off_for)
            w["on"], w["on_at"] = True, time.monotonic()
            t0 = time.monotonic()
        stable_since = None
        while time.monotonic() - t0 < 120:
            await asyncio.sleep(0.1)
            if m.status == "connected":
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 8:
                    break
            else:
                stable_since = None
        took = (stable_since or time.monotonic()) - t0
        await m.disconnect()
        return took, log

    return fastclock.run(main())


for name, kw in [("power-on, speaker auto-links", dict(autolink=True)),
                 ("power-on, no auto-link", dict(autolink=False)),
                 ("app start, speaker already linked", dict(autolink=True, startup=True))]:
    took, log = run(**kw)
    print(f"{name:34s} stable after {took:5.1f} s   {log}")
    assert took < 15, f"{name}: took {took:.1f} s"
    assert log[-1][1] == "att on host", f"{name}: {log}"

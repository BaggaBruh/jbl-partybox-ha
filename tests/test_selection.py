"""Speaker selection: user actions stop auto-connect, the pause expires, the setting is honoured."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastclock  # noqa: E402,F401
import asyncio  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))

import bluez  # noqa: E402
import speaker  # noqa: E402

A, B, OFFLINE = "12:34:56:78:9A:BC", "AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"
speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_selection.json"
speaker.POLL_SECONDS = 0.01
speaker.SETTLE_SECONDS = 0
paging_aborted = []
page_seconds = 0.0


async def relink(_address, timeout=30.0):
    await asyncio.sleep(page_seconds)


async def link_state(_address):
    return {"connected": True, "audio": True, "has_a2dp": True}


async def abort(address):
    paging_aborted.append(address)


pair_results, pairs_cancelled = [], []


async def pair(address, timeout=30.0):
    await asyncio.sleep(page_seconds)
    return pair_results.pop(0) if pair_results else None


async def cancel_pairing(address):
    pairs_cancelled.append(address)


async def no_paired(_uuids):
    return []


bluez.relink, bluez.link_state, bluez.disconnect = relink, link_state, abort
bluez.pair, bluez.cancel_pairing, bluez.paired_devices = pair, cancel_pairing, no_paired


class FakeManager(speaker.SpeakerManager):
    connects = ()

    async def connect(self, address, ble_fallback=True):
        self.connects = (*self.connects, address)
        self.status = "connecting"
        await asyncio.sleep(0.01)
        if address == OFFLINE:
            self.status = "disconnected"
            self._schedule_reconnect()
            raise speaker.BleakError("not found")
        self.address, self.status = address, "connected"
        self._save_last(address, "att")

    async def _discover(self):
        return {}


def fresh(autoconnect="last", last=A):
    speaker.STATE_FILE.write_text(json.dumps({
        "address": last, "transport": "att", "autoconnect": autoconnect,
        "known": {A: {"name": "Living Room", "transport": "att", "last_connected": 2},
                  B: {"name": "Garden", "transport": "att", "last_connected": 1}},
    }))
    return FakeManager()


def check(label, ok):
    print(("ok  " if ok else "FAIL"), label)
    return ok


async def main():
    global page_seconds
    results = []

    # A scan force-stops a reconnect, including the BlueZ connect in flight
    page_seconds = 60
    m = fresh()
    m.auto_reconnect(A)
    await asyncio.sleep(0.05)
    results.append(check("reconnect is paging before the scan", m._paging == A))
    await asyncio.wait_for(m.scan(), 1)
    results.append(check("scan aborts the BlueZ connect", paging_aborted == [A]))
    results.append(check("scan stops the reconnect loop", not m.snapshot()["reconnecting"]))
    results.append(check("auto-connect is paused after the scan", m.snapshot()["auto_paused"] > 0))
    m._schedule_reconnect()
    results.append(check("a drop can't restart it during the pause", not m.snapshot()["reconnecting"]))
    await m.disconnect()

    # The pause expires by itself and auto-connect returns to the target
    page_seconds = 0
    speaker.AUTO_HOLD_SECONDS = 0.05
    m = fresh()
    await m.scan()
    await asyncio.sleep(0.3)
    results.append(check("auto-connect resumes when the pause ends", m.status == "connected" and m.address == A))
    await m.disconnect()
    speaker.AUTO_HOLD_SECONDS = 120

    # A manual connect beats a running reconnect and clears the pause
    page_seconds = 60
    m = fresh()
    m.auto_reconnect(A)
    await asyncio.sleep(0.05)
    page_seconds = 0
    await asyncio.wait_for(m.connect_to(B), 1)
    results.append(check("manual connect wins", m.address == B and m.status == "connected"))
    results.append(check("pause cleared after success", m.snapshot()["auto_paused"] == 0))
    results.append(check("B is now the last speaker", m.autoconnect_target() == B))
    await m.disconnect()

    # A failed manual connect doesn't retry the failed speaker and doesn't replace the last one
    m = fresh()
    try:
        await m.connect_to(OFFLINE)
    except speaker.BleakError:
        pass
    results.append(check("no retries of the failed speaker", not m.snapshot()["reconnecting"]))
    results.append(check("last speaker unchanged", m.autoconnect_target() == A))
    await m.disconnect()

    # A newer connect replaces one in progress
    page_seconds = 60
    m = fresh()
    first = asyncio.create_task(m.connect_to(A))
    await asyncio.sleep(0.05)
    page_seconds = 0
    await asyncio.wait_for(m.connect_to(B), 1)
    results.append(check("first connect reports it was superseded", await first is False))
    results.append(check("second connect stands", m.address == B and m.status == "connected"))
    await m.disconnect()

    # The setting: pinned speaker, off, and forgetting the pinned speaker
    m = fresh(autoconnect=B)
    results.append(check("pinned target", m.autoconnect_target() == B))
    await m.set_autoconnect("off")
    m.auto_reconnect(A)
    results.append(check("off starts nothing", not m.snapshot()["reconnecting"]))
    await m.set_autoconnect(B)
    await m.forget(B)
    results.append(check("forgetting the pinned speaker falls back to last", m.autoconnect == "last"))
    results.append(check("setting survives a restart", FakeManager().autoconnect == "last"))
    try:
        await m.set_autoconnect(B)
        results.append(check("forgotten speaker can't be pinned", False))
    except ValueError:
        results.append(check("forgotten speaker can't be pinned", True))
    await m.disconnect()

    # Pairing: connects when it works, stays paused when it doesn't, can be interrupted
    page_seconds = 0
    m = fresh()
    await m.connect_to(B, pair=True)
    results.append(check("pair then connect", m.status == "connected" and m.address == B))
    await m.disconnect()
    m = fresh()
    pair_results.append("Failed")
    try:
        await m.connect_to(OFFLINE, pair=True)
        results.append(check("failed pairing raises", False))
    except speaker.BleakError:
        results.append(check("failed pairing raises", True))
    results.append(check("failed pairing doesn't connect", OFFLINE not in m.connects and m.status == "disconnected"))
    results.append(check("failed pairing pauses auto-connect", m.snapshot()["auto_paused"] > 0))
    await m.disconnect()
    page_seconds = 60
    m = fresh()
    first = asyncio.create_task(m.connect_to(OFFLINE, pair=True))
    await asyncio.sleep(0.05)
    await m.scan()
    results.append(check("scan cancels pairing", await first is False and pairs_cancelled == [OFFLINE]))
    page_seconds = 0
    await m.disconnect()

    # Adapter selection and health
    async def health():
        return {"error": None, "adapters": [
            {"id": "hci0", "address": "00:00:00:00:00:01", "name": "a", "powered": False},
            {"id": "hci1", "address": "00:00:00:00:00:02", "name": "b", "powered": True},
        ]}

    bluez.health = health
    m = fresh()
    await m.refresh_health()
    results.append(check("switched-off adapter is reported", m.snapshot()["bluetooth"]["fix"] == "hci0"))
    await m.set_adapter("hci1")
    results.append(check("adapter selected", bluez.adapter == "hci1" and m.snapshot()["bluetooth"]["problem"] is None))
    results.append(check("L2CAP binds to the selected adapter", speaker.att.source_address == "00:00:00:00:00:02"))
    results.append(check("adapter survives a restart", FakeManager().snapshot()["bluetooth"]["selected"] == "hci1"))
    try:
        await m.set_adapter("hci9")
        results.append(check("unknown adapter refused", False))
    except ValueError:
        results.append(check("unknown adapter refused", True))
    await m.set_adapter(None)
    await m.disconnect()

    # Files from before this feature keep working
    speaker.STATE_FILE.write_text(json.dumps({"address": A, "transport": "att"}))
    m = FakeManager()
    results.append(check("old state file migrates", m.autoconnect_target() == A and A in m.known))

    assert all(results), "some checks failed"


fastclock.run(main())

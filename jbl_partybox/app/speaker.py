"""BLE connection to one PartyBox speaker at a time."""
import asyncio
import collections
import json
import logging
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

import att
import bluez
import protocol as p

log = logging.getLogger("partybox")
STATE_FILE = Path("/data/last_device.json")
SETTLE_SECONDS = 1  # after the host link and its audio are up, before opening our channel
POLL_SECONDS = 1  # how often the reconnect loop checks BlueZ for the speaker's link
AUDIO_WAIT_SECONDS = 10  # how long to wait for the speaker's audio link after re-linking it
PAGE_RETRY_SECONDS = 1  # gap between host connect attempts while the speaker is off (each takes ~5 s)
RELINK_TIMEOUT_SECONDS = 12  # a host connect to a speaker that's off otherwise hangs ~30 s
FALLBACK_EVERY = 6  # every Nth failed attempt is a plain connect (incl. BLE) instead
WATCHDOG_SECONDS = 30  # silence before probing the speaker; twice this and the link is dropped

INITIAL_QUERIES = [
    p.req_device_info,
    p.req_player_info,
    p.req_battery,
    p.req_bass_boost,
    p.req_simple_eq,
    p.req_feedback_tone,
]


class SpeakerManager:
    def __init__(self, heartbeat_interval=5, scan_timeout=8, auto_connect=True):
        self.heartbeat_interval = heartbeat_interval
        self.scan_timeout = scan_timeout
        self.auto_connect = auto_connect
        self.client = None  # BleakClient when connected over GATT
        self.link = None  # att.AttLink when connected over classic Bluetooth
        self.transport = None  # "gatt" (BLE) | "att" (GATT over classic Bluetooth)
        self.address = None
        self.status = "disconnected"  # disconnected | connecting | connected
        self.state = {}
        self.devices = {}  # address -> {name, rssi, last_seen}
        self.log = collections.deque(maxlen=300)
        self.listeners = set()  # async callables(event: dict)
        self._reader = p.FrameReader()
        self._tx = None
        self._tx_response = True
        self._write_lock = asyncio.Lock()
        self._heartbeat_task = None
        self._reconnect_task = None
        self._user_disconnect = False
        self._connected_at = None
        self._last_rx = 0.0
        self._dropped_at = None

    # ---- Events ----

    def snapshot(self):
        return {
            "status": self.status,
            "address": self.address,
            "state": self.state,
            "devices": sorted(self.devices.values(), key=lambda d: -(d["rssi"] or -999)),
        }

    async def _emit(self, event):
        for cb in list(self.listeners):
            try:
                await cb(event)
            except Exception:
                self.listeners.discard(cb)

    async def _emit_state(self):
        await self._emit({"type": "state", **self.snapshot()})

    async def _log(self, direction, data, note=""):
        entry = {"t": time.time(), "dir": direction, "hex": data.hex(" ") if data else "", "note": note}
        self.log.append(entry)
        await self._emit({"type": "log", "entry": entry})

    # ---- Scanning ----

    async def scan(self):
        found = await BleakScanner.discover(timeout=self.scan_timeout, return_adv=True)
        for dev, adv in found.values():
            uuids = {u.lower() for u in adv.service_uuids} | {u.lower() for u in adv.service_data}
            name = dev.name or adv.local_name or ""
            if uuids & p.ADV_SERVICE_UUIDS or "partybox" in name.lower() or p.SERVICE_UUID in uuids:
                self.devices[dev.address] = {
                    "address": dev.address, "name": name or dev.address,
                    "rssi": adv.rssi, "last_seen": time.time(),
                }
        await self._emit_state()
        return self.snapshot()["devices"]

    # ---- Connection ----

    def _link_up(self):
        if self.transport == "att":
            return bool(self.link and self.link.is_connected)
        return bool(self.client and self.client.is_connected)

    async def connect(self, address, ble_fallback=True):
        if self._link_up():
            if self.address == address:
                return
            await self.disconnect()
        self._user_disconnect = False
        self.address = address
        self.status = "connecting"
        self.state = {}
        await self._emit_state()
        try:
            opened = False
            if self._load_last().get("transport") == "att":
                try:
                    await self._open_att(address)
                    opened = True
                except Exception as e:
                    if not ble_fallback:
                        raise
                    await self._log("info", b"", f"GATT over classic Bluetooth failed ({e}); trying BLE")
            if not opened:
                try:
                    await self._open(address)
                except NoGattError:
                    # BlueZ is connected over classic Bluetooth (e.g. A2DP), so bleak never makes a
                    # BLE link. The speaker serves the same GATT database over BR/EDR, so use that.
                    await self._log("info", b"", f"BlueZ device: {await _safe(bluez.device_info(address))}")
                    await self._log("info", b"", f"BlueZ objects: {await _safe(bluez.dump_device(address))}")
                    self.client = None  # leave the classic link up; the ATT channel rides on it
                    await self._log("info", b"", "no BLE GATT; trying GATT over classic Bluetooth (L2CAP PSM 31)")
                    await self._open_att(address)
        except Exception as e:
            self.status = "disconnected"
            await self._log("err", b"", f"connect failed: {e}")
            await self._emit_state()
            self._schedule_reconnect()
            raise
        self.status = "connected"
        self._connected_at = self._last_rx = time.monotonic()
        self._save_last(address, self.transport)
        mtu = f", mtu {self.client.mtu_size}" if self.transport == "gatt" else ""
        took = f" (reconnected {time.monotonic() - self._dropped_at:.0f} s after the drop)" if self._dropped_at else ""
        self._dropped_at = None
        await self._log("info", b"", f"connected to {address} over {self.transport}{mtu}{took}")
        await self._emit_state()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self.refresh()

    async def _open(self, address):
        device = await BleakScanner.find_device_by_address(address, timeout=self.scan_timeout)
        if device is None:
            raise BleakError(f"{address} not found; is it on and not connected to the JBL app?")
        client = BleakClient(device, disconnected_callback=self._on_disconnect, timeout=30.0)
        await client.connect()
        self.client = client
        self.transport = "gatt"
        await self._setup(client)

    async def _open_att(self, address):
        self._reader = p.FrameReader()
        link = att.AttLink(self._on_link_data, self._on_link_closed, on_event=self._on_link_event)
        try:
            await link.open(address, p.RX_UUID, p.TX_UUID)
        except BaseException:
            if link.characteristics:
                await self._log("info", b"", f"BR/EDR GATT characteristics: {link.describe()}")
            await link.close()
            raise
        await self._log("info", b"", f"BR/EDR GATT characteristics: {link.describe()}")
        for note in link.notes:
            await self._log("info", b"", note)
        self.link = link
        self.transport = "att"

    async def _close_link(self):
        client, self.client = self.client, None
        link, self.link = self.link, None
        for closer in (client and client.disconnect, link and link.close):
            if closer:
                try:
                    await closer()
                except Exception:
                    pass

    async def _setup(self, client):
        # The app tries the PartyBox service, then a per-device service UUID, then 0x1801;
        # the RX/TX characteristic UUIDs are the same in all cases, so look for them anywhere.
        services = sorted(client.services, key=lambda s: s.uuid.lower() != p.SERVICE_UUID)
        svc = next((s for s in services if s.get_characteristic(p.TX_UUID)), None)
        if svc is None:
            layout = "; ".join(
                f"{s.uuid} [{', '.join(c.uuid for c in s.characteristics)}]" for s in client.services
            )
            await self._log("err", b"", f"GATT layout: {layout}")
            raise NoGattError("PartyBox TX characteristic not found (see GATT layout in the debug log)")
        rx, tx = svc.get_characteristic(p.RX_UUID), svc.get_characteristic(p.TX_UUID)
        await self._log("info", b"", f"using service {svc.uuid}")
        # Pro/Ultimate models notify on TX instead of RX
        notify = rx if rx and {"notify", "indicate"} & set(rx.properties) else tx
        self._tx = tx
        # Android picks write-without-response when the characteristic only supports that
        self._tx_response = "write" in tx.properties
        self._reader = p.FrameReader()
        await client.start_notify(notify, self._on_notify)
        props = ", ".join(f"{c.uuid[-4:]}:{'/'.join(c.properties)}" for c in svc.characteristics)
        await self._log("info", b"", f"characteristics {props}")

    async def disconnect(self):
        self._user_disconnect = True
        self._cancel_tasks()
        await self._close_link()
        self.status = "disconnected"
        self._dropped_at = None
        await self._emit_state()

    def _on_disconnect(self, _client):
        # connect() handles failures that happen while it's still running
        if self.status != "connected":
            return
        self.status = "disconnected"
        self._dropped_at = time.monotonic()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        asyncio.get_event_loop().create_task(self._after_disconnect())

    def _on_link_closed(self):
        self._on_disconnect(None)

    def _on_link_event(self, msg):
        asyncio.get_event_loop().create_task(self._log("info", b"", msg))

    async def _after_disconnect(self):
        await self._close_link()
        await self._log("info", b"", "disconnected")
        await self._emit_state()
        self._schedule_reconnect()

    def auto_reconnect(self, address):
        """Connect to `address` in the background the same way drop-outs are recovered."""
        self.address = address
        self._user_disconnect = False
        self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self._user_disconnect or not self.auto_connect or not self.address:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self):
        # The speaker only keeps our control channel open on a link the host set up
        # (Device1.Connect). On a link it set up itself (its auto-reconnect after power-on) it
        # closes the channel after ~5 s. So every reconnect (re)creates the link from the host:
        # immediately when the speaker links itself, otherwise by paging it every few seconds.
        # Every Nth failure also tries a plain connect, in case BlueZ can't be driven at all.
        next_page = time.monotonic()
        failures = 0
        while not self._user_disconnect and self.status != "connected":
            await asyncio.sleep(POLL_SECONDS)
            if self.status == "connecting":
                continue
            st = await _safe(bluez.link_state(self.address))
            linked = isinstance(st, dict) and st["connected"]
            if not linked and time.monotonic() < next_page:
                continue
            if failures % FALLBACK_EVERY == FALLBACK_EVERY - 1:
                await self._log("info", b"", "host re-link keeps failing; trying a direct connect")
            else:
                err = await _safe(bluez.relink(self.address, timeout=RELINK_TIMEOUT_SECONDS))
                if err:
                    failures += 1
                    next_page = time.monotonic() + PAGE_RETRY_SECONDS
                    if linked or failures == 1:
                        await self._log("info", b"", f"re-linking speaker from the host: {err}")
                    continue
                await self._log("info", b"", "re-linked speaker from the host")
                await self._wait_for_audio(AUDIO_WAIT_SECONDS)
                await asyncio.sleep(SETTLE_SECONDS)
            try:
                await self.connect(self.address, ble_fallback=failures % FALLBACK_EVERY == FALLBACK_EVERY - 1)
                if self.status == "connected":
                    return
                # Dropped while connect() was still sending its initial queries; the drop handler
                # left the retry to this loop.
            except Exception:
                pass
            failures += 1
            next_page = time.monotonic() + PAGE_RETRY_SECONDS

    async def _wait_for_audio(self, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = await _safe(bluez.link_state(self.address))
            if isinstance(st, dict) and st["connected"] and (st["audio"] or not st["has_a2dp"]):
                return True
            await asyncio.sleep(0.25)
        return False

    def _cancel_tasks(self):
        for t in (self._heartbeat_task, self._reconnect_task):
            if t and not t.done() and t is not asyncio.current_task():
                t.cancel()

    async def _heartbeat_loop(self):
        # Keep-alive plus watchdog: a link can die without the socket noticing. When the speaker
        # has been silent for WATCHDOG_SECONDS, probe it with a player-info request (it always
        # answers that); if it stays silent, treat the link as dead so the reconnect loop takes over.
        while self._link_up():
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self.send(p.heartbeat(), quiet=True)
                silent = time.monotonic() - self._last_rx
                if silent > 2 * WATCHDOG_SECONDS:
                    await self._drop_link(f"no data from the speaker for {silent:.0f} s")
                    return
                if silent > WATCHDOG_SECONDS:
                    await self.send(p.req_player_info(), quiet=True)
            except Exception as e:
                await self._drop_link(f"keep-alive failed: {e}")
                return

    async def _drop_link(self, reason):
        await self._log("info", b"", f"{reason}; reconnecting")
        await self._close_link()
        self._on_disconnect(None)

    # ---- I/O ----

    async def send(self, data, quiet=False):
        if not self._link_up():
            raise BleakError("not connected")
        async with self._write_lock:
            if not quiet:
                await self._log("tx", data)
            if self.transport == "att":
                await self.link.write(data)
            else:
                await self.client.write_gatt_char(self._tx, data, response=self._tx_response)
            await asyncio.sleep(0.08)  # the app sends one command at a time

    async def refresh(self):
        for q in INITIAL_QUERIES:
            if not self._link_up():
                break
            try:
                await self.send(q())
            except Exception as e:
                await self._log("err", b"", f"query failed: {e}")
            await asyncio.sleep(0.15)

    def _on_notify(self, _char, data):
        asyncio.get_event_loop().create_task(self._handle(bytes(data)))

    def _on_link_data(self, data):
        asyncio.get_event_loop().create_task(self._handle(bytes(data)))

    async def _handle(self, data):
        self._last_rx = time.monotonic()
        frames, junk = self._reader.feed(data)
        if junk:
            await self._log("rx", junk, "unframed")
        changed = False
        for cmd, payload in frames:
            raw = p.frame(cmd, payload)
            if cmd == p.HEARTBEAT or (cmd == p.ACK and payload[:1] == bytes([p.HEARTBEAT])):
                continue
            upd = p.parse_frame(cmd, payload)
            ack = upd.pop("_ack", None)
            hb = upd.pop("_heartbeat_s", None)
            if hb:
                self.heartbeat_interval = max(1, hb)
            note = f"cmd 0x{cmd:02x}"
            if ack:
                note = f"ack 0x{ack[0]:02x} status {ack[1]}"
                if ack[0] == p.SET_PLAYER_INFO:
                    asyncio.get_event_loop().create_task(self._delayed(p.req_player_info()))
            elif upd:
                note += " " + json.dumps(upd, ensure_ascii=False)
            await self._log("rx", raw, note)
            if upd:
                self.state.update(upd)
                changed = True
        if changed:
            await self._emit_state()

    async def _delayed(self, data, delay=0.3):
        await asyncio.sleep(delay)
        try:
            await self.send(data, quiet=True)
        except Exception:
            pass

    # ---- Actions ----

    async def action(self, name, value=None):
        s = self.state
        builders = {
            "volume": lambda: (p.set_volume(value), {"volume": int(value)}),
            "primary_volume": lambda: (p.set_primary_volume(value), {"primary_volume": int(value)}),
            "secondary_volume": lambda: (p.set_secondary_volume(value), {"secondary_volume": int(value)}),
            "mute": lambda: (p.set_mute(bool(value)), {"muted": bool(value)}),
            "play": lambda: (p.set_play(p.PLAY_PLAY), {"player_status": "playing"}),
            "pause": lambda: (p.set_play(p.PLAY_PAUSE), {"player_status": "paused"}),
            "next": lambda: (p.set_play(p.PLAY_NEXT), {}),
            "prev": lambda: (p.set_play(p.PLAY_PREV), {}),
            "bass_boost": lambda: (p.set_bass_boost(int(value)), {"bass_boost": int(value)}),
            "eq": lambda: (
                p.set_simple_eq(value.get("enabled", True), value.get("bass", 0), value.get("mid", 0), value.get("treble", 0)),
                {"eq_enabled": bool(value.get("enabled", True)), "eq_bass": int(value.get("bass", 0)),
                 "eq_mid": int(value.get("mid", 0)), "eq_treble": int(value.get("treble", 0))},
            ),
            "feedback_tone": lambda: (p.set_feedback_tone(bool(value)), {"feedback_tone": bool(value)}),
            "volume_boost": lambda: (p.set_volume_boost(bool(value)), {"volume_boost": bool(value)}),
            "lock_panel": lambda: (p.set_lock_panel(bool(value)), {"lock_panel": bool(value)}),
            "rename": lambda: (p.set_device_name(str(value)), {"name": str(value)[:16]}),
            # Newer models use the main switch; older ones turn lights off by selecting pattern OFF
            # (LightShowControlViewModel), so send both.
            "light": lambda: (
                [p.set_light_switch(bool(value)),
                 p.set_light_pattern(s.get("light_pattern") or 1 if value else 0)],
                {"light_on": bool(value)},
            ),
            "light_pattern": lambda: (p.set_light_pattern(int(value)), {"light_pattern": int(value), "light_on": True}),
            "light_color": lambda: (p.set_light_color(*_hex_rgb(value)), {"light_color": value}),
            "power_off": lambda: (p.power_off(), {}),
            "raw": lambda: (bytes.fromhex(str(value).replace(" ", "")), {}),
        }
        if name == "refresh":
            await self.refresh()
            return
        if name not in builders:
            raise ValueError(f"unknown action {name}")
        data, optimistic = builders[name]()
        for frame in data if isinstance(data, list) else [data]:
            await self.send(frame)
        if optimistic:
            s.update(optimistic)
            await self._emit_state()

    # ---- Persistence ----

    def _save_last(self, address, transport):
        try:
            STATE_FILE.write_text(json.dumps({"address": address, "transport": transport}))
        except OSError:
            pass

    def _load_last(self):
        last = self.load_last_info()
        return last if last.get("address") == self.address else {}

    @staticmethod
    def load_last_info():
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return {}

    @staticmethod
    def load_last():
        return SpeakerManager.load_last_info().get("address")


class NoGattError(BleakError):
    pass


async def _safe(coro):
    try:
        return await coro
    except Exception as e:
        return f"error: {e}"


def _hex_rgb(value):
    v = str(value).lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)

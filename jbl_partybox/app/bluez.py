"""BlueZ D-Bus helpers (Linux only) for dual-mode speakers.

A PartyBox is both a classic (BR/EDR) and a BLE device on the same address. When BlueZ is
connected over BR/EDR, no BLE GATT services show up; these helpers report what BlueZ sees
and let us re-create the classic link from the host side.
"""
import asyncio

try:
    from dbus_fast import BusType, Message, MessageType, Variant
    from dbus_fast.aio import MessageBus
except ImportError:  # not on Linux
    MessageBus = None

BLUEZ = "org.bluez"
adapter = None  # adapter id ("hci0") to use; None means BlueZ's first one
_INTERESTING = ("Paired", "Bonded", "Trusted", "Connected", "ServicesResolved", "AddressType", "Class", "Name")


def _on_adapter(path):
    return adapter is None or path.startswith(f"/org/bluez/{adapter}/")


async def _objects(bus):
    reply = await bus.call(Message(
        destination=BLUEZ, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects",
    ))
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(reply.error_name)
    return reply.body[0]


def _adapters(objects):
    found = []
    for path, ifaces in sorted(objects.items()):
        a = ifaces.get("org.bluez.Adapter1")
        if a:
            found.append({
                "id": path.rsplit("/", 1)[-1], "address": a["Address"].value,
                "name": (a["Alias"].value if "Alias" in a else "") or path.rsplit("/", 1)[-1],
                "powered": bool(a["Powered"].value),
            })
    return found


async def health():
    """{"error": why BlueZ can't be used or None, "adapters": [{id, address, name, powered}]}."""
    if MessageBus is None:
        return {"error": "BlueZ isn't available on this system", "adapters": []}
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except Exception as e:
        return {"error": f"can't reach D-Bus ({e})", "adapters": []}
    try:
        return {"error": None, "adapters": _adapters(await _objects(bus))}
    except Exception as e:
        return {"error": f"can't reach BlueZ ({e})", "adapters": []}
    finally:
        bus.disconnect()


async def set_powered(adapter_id, on=True):
    """Returns None or an error string, e.g. when rfkill blocks the adapter."""
    if MessageBus is None:
        return "BlueZ not available"
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(
            destination=BLUEZ, path=f"/org/bluez/{adapter_id}", interface="org.freedesktop.DBus.Properties",
            member="Set", signature="ssv", body=["org.bluez.Adapter1", "Powered", Variant("b", on)],
        ))
        return reply.error_name if reply.message_type == MessageType.ERROR else None
    finally:
        bus.disconnect()


async def _adapter_path(bus):
    if adapter:
        return f"/org/bluez/{adapter}"
    found = _adapters(await _objects(bus))
    if not found:
        raise RuntimeError("no Bluetooth adapter")
    return f"/org/bluez/{found[0]['id']}"


async def _discovery(bus, on):
    path = await _adapter_path(bus)
    if on:
        # BlueZ merges this with bleak's LE-only filter, so classic-only speakers show up too
        await bus.call(Message(
            destination=BLUEZ, path=path, interface="org.bluez.Adapter1", member="SetDiscoveryFilter",
            signature="a{sv}", body=[{"Transport": Variant("s", "auto")}],
        ))
    await bus.call(Message(
        destination=BLUEZ, path=path, interface="org.bluez.Adapter1", member="StartDiscovery" if on else "StopDiscovery",
    ))


def _looks_like_speaker(props, speaker_uuids):
    name = props.get("Alias") or props.get("Name") or props["Address"]
    uuids = {u.lower() for u in [*props.get("UUIDs", []), *props.get("ServiceData", {})]}
    partybox = bool(uuids & speaker_uuids) or "partybox" in name.lower()
    # A renamed speaker often doesn't show its PartyBox service once it's connected for audio, so
    # also accept everything of the audio/video major class (not phones, keyboards).
    return name, partybox, partybox or (props.get("Class", 0) >> 8) & 0x1F == 4


async def discover_nearby(seconds, speaker_uuids):
    """Unpaired speaker-like devices in range, including classic-only ones (speakers in pairing mode)."""
    if MessageBus is None:
        return []
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        await _discovery(bus, True)
        await asyncio.sleep(seconds)
        found = []
        for path, ifaces in (await _objects(bus)).items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev or not _on_adapter(path):
                continue
            props = {k: v.value for k, v in dev.items()}
            if props.get("Paired") or props.get("Bonded") or "RSSI" not in props:
                continue
            name, partybox, speaker = _looks_like_speaker(props, speaker_uuids)
            if speaker:
                found.append({"address": props["Address"], "name": name, "partybox": partybox, "rssi": props["RSSI"]})
        return found
    finally:
        bus.disconnect()  # also ends this client's discovery session


async def pair(address, timeout=30.0):
    """Pair and trust the speaker. Returns None or an error string.

    No agent is registered: BlueZ then pairs as NoInputNoOutput ("just works"), which suits a
    speaker without a display. Trusting it lets the speaker reconnect its audio by itself.
    """
    if MessageBus is None:
        return "BlueZ not available"
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        path, props = await _find_device(bus, address)
        if path is None:  # BlueZ forgets unpaired devices about 30 s after the scan
            await _discovery(bus, True)
            for _ in range(40):
                await asyncio.sleep(0.25)
                path, props = await _find_device(bus, address)
                if path:
                    break
            await _discovery(bus, False)
            if path is None:
                return "speaker not found; is it in pairing mode?"
        if not props.get("Paired"):
            reply = await asyncio.wait_for(bus.call(Message(
                destination=BLUEZ, path=path, interface="org.bluez.Device1", member="Pair",
            )), timeout)
            if reply.message_type == MessageType.ERROR and "AlreadyExists" not in reply.error_name:
                return f"{reply.error_name}: {reply.body[0] if reply.body else ''}".rstrip(": ")
        await bus.call(Message(
            destination=BLUEZ, path=path, interface="org.freedesktop.DBus.Properties", member="Set",
            signature="ssv", body=["org.bluez.Device1", "Trusted", Variant("b", True)],
        ))
        return None
    except asyncio.TimeoutError:
        return "timed out; is the speaker in pairing mode?"
    finally:
        bus.disconnect()


async def cancel_pairing(address):
    if MessageBus is None:
        return
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        path, _ = await _find_device(bus, address)
        if path:
            await bus.call(Message(destination=BLUEZ, path=path, interface="org.bluez.Device1", member="CancelPairing"))
    finally:
        bus.disconnect()


async def _find_device(bus, address):
    reply = await bus.call(Message(
        destination=BLUEZ, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects",
    ))
    if reply.message_type == MessageType.ERROR:
        return None, None
    for path, ifaces in reply.body[0].items():
        dev = ifaces.get("org.bluez.Device1")
        if dev and _on_adapter(path) and dev["Address"].value.upper() == address.upper():
            return path, {k: v.value for k, v in dev.items()}
    return None, None


async def device_info(address):
    """Returns a short dict of the BlueZ Device1 state, or None."""
    if MessageBus is None:
        return None
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        _, props = await _find_device(bus, address)
        if props is None:
            return None
        info = {k: props[k] for k in _INTERESTING if k in props}
        if "Class" in info:
            info["Class"] = hex(info["Class"])  # present only when BlueZ has seen it over BR/EDR
        info["UUIDs"] = len(props.get("UUIDs", []))
        return info
    finally:
        bus.disconnect()


async def dump_device(address):
    """Device1 UUIDs plus any GATT objects BlueZ created under the device, for diagnostics."""
    if MessageBus is None:
        return None
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(
            destination=BLUEZ, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects",
        ))
        objects = reply.body[0]
        path, props = await _find_device(bus, address)
        if path is None:
            return None
        children = sorted(
            f"{p[len(path):]}:{','.join(i.split('.')[-1] for i in ifaces if i.startswith('org.bluez'))}"
            for p, ifaces in objects.items() if p.startswith(path + "/")
        )
        uuids = [u[4:8] if u.endswith("-0000-1000-8000-00805f9b34fb") else u for u in props.get("UUIDs", [])]
        return {"uuids": uuids, "objects": children}
    finally:
        bus.disconnect()



async def link_state(address):
    """{"connected": ACL up, "audio": an A2DP transport exists, "has_a2dp": speaker offers A2DP}."""
    if MessageBus is None:
        return None
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(
            destination=BLUEZ, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects",
        ))
        for path, ifaces in reply.body[0].items():
            dev = ifaces.get("org.bluez.Device1")
            if dev and _on_adapter(path) and dev["Address"].value.upper() == address.upper():
                audio = any(p.startswith(path + "/") and "org.bluez.MediaTransport1" in i
                            for p, i in reply.body[0].items())
                uuids = dev["UUIDs"].value if "UUIDs" in dev else []
                return {
                    "connected": bool(dev["Connected"].value),
                    "audio": audio,
                    "has_a2dp": any(u.startswith("0000110b") for u in uuids),
                }
        return None
    finally:
        bus.disconnect()


async def paired_devices(speaker_uuids):
    """Paired audio devices: [{"address", "name", "partybox": advertises a PartyBox service/name}]."""
    if MessageBus is None:
        return []
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(
            destination=BLUEZ, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects",
        ))
        if reply.message_type == MessageType.ERROR:
            return []
        found = []
        for path, ifaces in reply.body[0].items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev or not _on_adapter(path):
                continue
            props = {k: v.value for k, v in dev.items()}
            if not (props.get("Paired") or props.get("Bonded")):
                continue
            name, partybox, speaker = _looks_like_speaker(props, speaker_uuids)
            if speaker:
                found.append({"address": props["Address"], "name": name, "partybox": partybox})
        return found
    finally:
        bus.disconnect()


async def disconnect(address):
    """Device1.Disconnect, which also aborts a host connect still in progress."""
    if MessageBus is None:
        return
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        path, _ = await _find_device(bus, address)
        if path:
            await bus.call(Message(destination=BLUEZ, path=path, interface="org.bluez.Device1", member="Disconnect"))
    finally:
        bus.disconnect()


async def relink(address, timeout=30.0):
    """Drop the speaker's link and re-create it from the host side (Device1.Connect).

    Sessions on a link the speaker set up itself (auto-reconnect of audio) get our control
    channel closed after a few seconds; on a host-initiated link it stays up. Returns None on
    success or an error string.
    """
    if MessageBus is None:
        return "BlueZ not available"
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        path, props = await _find_device(bus, address)
        if path is None:
            return "device unknown to BlueZ"

        async def call(member):
            reply = await asyncio.wait_for(bus.call(Message(
                destination=BLUEZ, path=path, interface="org.bluez.Device1", member=member,
            )), timeout)
            return None if reply.message_type != MessageType.ERROR else f"{member}: {reply.error_name}"

        if props.get("Connected"):
            await call("Disconnect")
            for _ in range(20):
                await asyncio.sleep(0.25)
                _, props = await _find_device(bus, address)
                if not props or not props.get("Connected"):
                    break
            await asyncio.sleep(1)
        err = await call("Connect")
        return None if err is None or "AlreadyConnected" in err else err
    except asyncio.TimeoutError:
        return "timed out"
    finally:
        bus.disconnect()

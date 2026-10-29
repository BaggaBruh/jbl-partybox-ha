"""BlueZ D-Bus helpers (Linux only) for dual-mode speakers.

A PartyBox is both a classic (BR/EDR) and a BLE device on the same address. When BlueZ is
connected over BR/EDR, no BLE GATT services show up; these helpers report what BlueZ sees
and let us re-create the classic link from the host side.
"""
import asyncio

try:
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus
except ImportError:  # not on Linux
    MessageBus = None

BLUEZ = "org.bluez"
_INTERESTING = ("Paired", "Bonded", "Trusted", "Connected", "ServicesResolved", "AddressType", "Class", "Name")


async def _find_device(bus, address):
    reply = await bus.call(Message(
        destination=BLUEZ, path="/", interface="org.freedesktop.DBus.ObjectManager", member="GetManagedObjects",
    ))
    if reply.message_type == MessageType.ERROR:
        return None, None
    for path, ifaces in reply.body[0].items():
        dev = ifaces.get("org.bluez.Device1")
        if dev and dev["Address"].value.upper() == address.upper():
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
            if dev and dev["Address"].value.upper() == address.upper():
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

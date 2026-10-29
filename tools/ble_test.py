"""JBL PartyBox BLE test: scan, read player info, set volume/mute.

    pip install bleak
    python tools/ble_test.py scan
    python tools/ble_test.py info   [--addr XX:XX:...]
    python tools/ble_test.py volume 16 [--addr XX:XX:...]
    python tools/ble_test.py mute on|off [--addr XX:XX:...]
    python tools/ble_test.py raw "AA 41 00" [--addr XX:XX:...]
"""
import argparse
import asyncio

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

SERVICE = "65786365-6c70-6f69-6e74-2e636f6d0000"
RX = "65786365-6c70-6f69-6e74-2e636f6d0001"  # notify
TX = "65786365-6c70-6f69-6e74-2e636f6d0002"  # write
ADV_UUIDS = {
    "0000fddf-0000-1000-8000-00805f9b34fb",
    "0000dffd-0000-1000-8000-00805f9b34fb",
    "0000fc69-0000-1000-8000-00805f9b34fb",
}

SET_PLAYER_INFO = 0x43
REQ_PLAYER_INFO = 0x41
RESP_PLAYER_INFO = 0x42
TOKENS = {0x41: "status", 0x42: "volume", 0x43: "mute", 0x44: "song",
          0x45: "artist", 0x46: "primary_volume", 0x47: "secondary_volume"}


def frame(cmd, payload=b""):
    return bytes([0xAA, cmd, len(payload)]) + bytes(payload)


def set_player(token, value, dev_idx=0):
    return frame(SET_PLAYER_INFO, [dev_idx, token, 1, value])


def parse_player_info(payload):
    out, i = {}, 1  # payload[0] is device index
    while i + 2 <= len(payload):
        tok, ln = payload[i], payload[i + 1]
        val = payload[i + 2:i + 2 + ln]
        i += 2 + ln
        name = TOKENS.get(tok, f"0x{tok:02x}")
        out[name] = val.decode(errors="replace") if tok in (0x44, 0x45) else int.from_bytes(val, "big")
    return out


def on_notify(_, data: bytearray):
    print("<-", data.hex(" "))
    if len(data) >= 3 and data[0] == 0xAA:
        cmd, ln = data[1], data[2]
        payload = data[3:3 + ln]
        if cmd == RESP_PLAYER_INFO:
            print("   player info:", parse_player_info(payload))
        elif cmd == 0x00 and len(payload) >= 2:
            print(f"   ack for cmd 0x{payload[0]:02x}, status {payload[1]}")


async def scan(timeout=8.0):
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    hits = []
    for dev, adv in found.values():
        uuids = set(adv.service_uuids) | set(adv.service_data)
        if uuids & ADV_UUIDS or "partybox" in (dev.name or "").lower():
            hits.append(dev)
            print(f"{dev.address}  {dev.name}  rssi={adv.rssi}")
    return hits


async def run(addr, packets, listen):
    if addr:
        # Connecting via a freshly scanned BLEDevice is more reliable on Windows than a bare address
        device = await BleakScanner.find_device_by_address(addr, timeout=10.0)
        if device is None:
            raise SystemExit(f"{addr} not advertising (disconnect it from the phone/JBL app?)")
    else:
        hits = await scan()
        if not hits:
            raise SystemExit("No PartyBox found")
        device = hits[0]
    print("Connecting to", device.address)
    # Only discover the PartyBox service: full discovery is what times out on WinRT
    client = BleakClient(device, timeout=30.0, services=[SERVICE])
    for attempt in range(1, 4):
        try:
            await client.connect()
            break
        except (TimeoutError, asyncio.TimeoutError, BleakError) as e:
            print(f"Connect attempt {attempt} failed ({e!r})")
            if attempt == 3:
                raise SystemExit("Could not connect")
            await asyncio.sleep(2)
    try:
        await talk(client, packets, listen)
    finally:
        await client.disconnect()


async def talk(client, packets, listen):
    print("MTU:", client.mtu_size)
    svc = client.services.get_service(SERVICE)
    if svc is None:
        raise SystemExit("PartyBox service not found; services: "
                         + ", ".join(s.uuid for s in client.services))
    for c in svc.characteristics:
        print(f"  char {c.uuid}  {','.join(c.properties)}")
    rx, tx = svc.get_characteristic(RX), svc.get_characteristic(TX)
    if tx is None:
        raise SystemExit("TX characteristic not found")
    # Pro models notify on the TX characteristic instead of RX
    notify_char = rx if rx and ({"notify", "indicate"} & set(rx.properties)) else tx
    try:
        await client.start_notify(notify_char, on_notify)
        print("Notifications on", notify_char.uuid)
    except Exception as e:
        print("start_notify failed:", e)
    # Android uses write-without-response when that's all the char supports
    with_response = "write" in tx.properties
    print("Write type:", "with response" if with_response else "without response")
    for p in packets:
        print("->", p.hex(" "))
        await client.write_gatt_char(tx, p, response=with_response)
        await asyncio.sleep(0.5)
    await asyncio.sleep(listen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["scan", "info", "volume", "mute", "raw"])
    ap.add_argument("value", nargs="?")
    ap.add_argument("--addr")
    ap.add_argument("--listen", type=float, default=3.0, help="seconds to wait for notifications")
    a = ap.parse_args()

    if a.action == "scan":
        asyncio.run(scan())
        return
    info = frame(REQ_PLAYER_INFO)
    if a.action == "info":
        packets = [info]
    elif a.action == "volume":
        vol = int(a.value)
        if not 0 <= vol <= 32:
            raise SystemExit("volume must be 0-32")
        packets = [set_player(0x42, vol), info]
    elif a.action == "mute":
        packets = [set_player(0x43, 1 if a.value == "on" else 0), info]
    else:
        packets = [bytes.fromhex(a.value)]
    asyncio.run(run(a.addr, packets, a.listen))


if __name__ == "__main__":
    main()

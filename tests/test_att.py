import asyncio
import socket
import struct
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastclock  # noqa: E402,F401
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import att  # noqa: E402
import bluez  # noqa: E402
import protocol as p  # noqa: E402
import speaker  # noqa: E402


def le128(u):
    return bytes(reversed(uuid.UUID(u).bytes))


# Fake GATT database: (decl_handle, props, value_handle, uuid_bytes)
# Mirrors the real speaker: TX 0x000e (write+wnr), RX 0x0010 (read+notify), no discoverable CCCD
CHARS = [
    (0x0002, 0x20, 0x0003, struct.pack("<H", 0x2A05)),
    (0x0006, 0x02, 0x0007, struct.pack("<H", 0x2A00)),
    (0x0008, 0x02, 0x0009, struct.pack("<H", 0x2A01)),
    (0x000B, 0x0C, 0x000C, le128("65786365-6c70-6f69-6e74-2e636f6d0af1")),
    (0x000D, 0x0C, 0x000E, le128(p.TX_UUID)),
    (0x000F, 0x12, 0x0010, le128(p.RX_UUID)),
]
CCCD = 0x0011
import os
CCCD_WRITABLE = os.environ.get("CCCD_WRITABLE", "1") == "1"
log = []


async def fake_server(sock):
    loop = asyncio.get_event_loop()
    notifying = False
    vol = 10
    while True:
        pdu = await loop.sock_recv(sock, 1024)
        if not pdu:
            return
        op = pdu[0]
        log.append(pdu.hex(" "))
        if op == att.READ_BY_TYPE_REQ:
            start, end, typ = struct.unpack("<HHH", pdu[1:7])
            items = [c for c in CHARS if c[0] >= start]
            if not items:
                await loop.sock_sendall(sock, bytes([att.ERROR_RSP, op]) + struct.pack("<H", start) + b"\x0a")
                continue
            size = len(items[0][3])
            items = [c for c in items if len(c[3]) == size]  # one UUID size per response
            body = b"".join(struct.pack("<HBH", c[0], c[1], c[2]) + c[3] for c in items[:1 if size == 16 else 5])
            await loop.sock_sendall(sock, bytes([att.READ_BY_TYPE_RSP, 5 + size]) + body)
        elif op == att.FIND_INFO_REQ:
            start, end = struct.unpack("<HH", pdu[1:5])
            if False:
                pass
            else:
                await loop.sock_sendall(sock, bytes([att.ERROR_RSP, op]) + struct.pack("<H", start) + b"\x0a")
        elif op == att.WRITE_REQ:
            handle = struct.unpack("<H", pdu[1:3])[0]
            if handle == CCCD and not CCCD_WRITABLE:
                await loop.sock_sendall(sock, bytes([att.ERROR_RSP, op]) + struct.pack("<H", handle) + b"\x03")
                continue
            if handle == CCCD:
                notifying = pdu[3:5] == b"\x01\x00"
            await loop.sock_sendall(sock, bytes([att.WRITE_RSP]))
        elif op == att.WRITE_CMD:
            handle, data = struct.unpack("<H", pdu[1:3])[0], pdu[3:]
            assert handle == 0x000E, handle
            cmd, payload = data[1], data[3:]
            if cmd == p.REQ_PLAYER_INFO:
                out = p.frame(p.RESP_PLAYER_INFO, [0, 0x42, 1, vol, 0x43, 1, 0])
            else:
                if cmd == p.SET_PLAYER_INFO and payload[1] == p.TOK_VOLUME:
                    vol = payload[3]
                out = p.frame(p.ACK, [cmd, 0])
            if notifying:
                await asyncio.sleep(0.01)
                await loop.sock_sendall(sock, bytes([att.NOTIFY]) + struct.pack("<H", 0x0011) + out)


speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_att.json"
speaker.STATE_FILE.unlink(missing_ok=True)


class FakeBleak:
    def __init__(self, dev, disconnected_callback=None, timeout=30):
        self.is_connected, self.services = False, []

    async def connect(self):
        self.is_connected = True  # already connected over classic: no GATT

    async def disconnect(self):
        self.is_connected = False


class FakeScanner:
    @staticmethod
    async def find_device_by_address(a, timeout=10):
        return object()


async def no_diag(a):
    return {"diag": "skipped"}


async def main():
    server_sock = server_task = None

    def factory():
        nonlocal server_sock
        a, b = socket.socketpair()
        a.setblocking(False)
        b.setblocking(False)
        server_sock = b
        nonlocal server_task
        server_task = asyncio.get_event_loop().create_task(fake_server(b))
        return a

    orig_open = att.AttLink.open

    async def open_without_connect(self, address, rx, tx, timeout=15.0):
        return await orig_open(self, None, rx, tx, timeout)  # socketpair is already connected

    att.AttLink.open = open_without_connect
    orig_init = att.AttLink.__init__
    att.AttLink.__init__ = lambda self, d, c, on_event=None: orig_init(self, d, c, sock_factory=factory, on_event=on_event)
    speaker.BleakClient, speaker.BleakScanner = FakeBleak, FakeScanner
    bluez.device_info = bluez.dump_device = no_diag

    m = speaker.SpeakerManager(heartbeat_interval=60, auto_connect=False)
    await m.connect("12:34:56:78:9A:BC")
    await asyncio.sleep(1.5)
    print("1) status", m.status, "transport", m.transport, "volume", m.state.get("volume"))
    assert m.status == "connected" and m.transport == "att", (m.status, m.transport)
    await m.action("volume", 26)
    await asyncio.sleep(0.6)
    print("2) volume after set", m.state.get("volume"), "| saved", speaker.STATE_FILE.read_text())
    assert m.state.get("volume") == 26, m.state
    assert m.known["12:34:56:78:9A:BC"]["transport"] == "att", m.known
    server_task.cancel()
    await asyncio.sleep(0)  # let the fake speaker stop reading before its socket closes
    server_sock.close()  # speaker drops the link
    await asyncio.sleep(0.3)
    print("3) after drop", m.status)
    assert m.status == "disconnected", m.status
    await m.disconnect()
    for e in list(m.log)[:14]:
        if e["dir"] != "tx" and e["dir"] != "rx":
            print("  ", e["dir"], e["hex"], e["note"])


fastclock.run(main())

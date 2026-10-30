"""ATT client vs. a speaker that acts as GATT client too and drops us if its requests go unanswered."""
import asyncio
import socket
import struct
import sys
from pathlib import Path
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastclock  # noqa: E402,F401
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import att  # noqa: E402
import protocol as p  # noqa: E402


def le128(u):
    return bytes(reversed(uuid.UUID(u).bytes))


CHARS = [
    (0x000D, 0x0C, 0x000E, le128(p.TX_UUID)),
    (0x000F, 0x12, 0x0010, le128(p.RX_UUID)),
]


class Pipe:
    """One end of a message-preserving duplex channel (like an L2CAP SEQPACKET socket)."""
    def __init__(self):
        self.inbox, self.peer, self.closed = asyncio.Queue(), None, False

    def close(self):
        if not self.closed:
            self.closed = True
            self.inbox.put_nowait(b"")
            self.peer.closed or self.peer.inbox.put_nowait(b"")


class MsgLoop:
    """Proxy for the event loop that routes sock_* calls on Pipe objects."""
    def __init__(self, real):
        self.real = real

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def sock_recv(self, sock, n):
        return await sock.inbox.get()

    async def sock_sendall(self, sock, data):
        if sock.closed:
            raise OSError("closed")
        sock.peer.inbox.put_nowait(bytes(data))

    def time(self):
        return self.real.time()


def pipe_pair():
    a, b = Pipe(), Pipe()
    a.peer, b.peer = b, a
    return a, b


async def speaker(sock, answered):
    loop = MsgLoop(asyncio.get_event_loop())
    # Speaker immediately asks us for MTU and our primary services, like a phone would answer
    await loop.sock_sendall(sock, bytes([att.MTU_REQ]) + struct.pack("<H", 672))
    await asyncio.sleep(0.05)
    await loop.sock_sendall(sock, struct.pack("<BHHH", att.READ_BY_GROUP_REQ, 1, 0xFFFF, 0x2800))
    deadline = loop.time() + 1.0
    while True:
        try:
            pdu = await asyncio.wait_for(loop.sock_recv(sock, 1024), max(0.01, deadline - loop.time()))
        except asyncio.TimeoutError:
            if len(answered) < 2:
                print("speaker: requests unanswered after 1s -> dropping link")
                sock.close()
                return
            deadline = loop.time() + 3600
            continue
        if not pdu:  # we closed the link
            return
        op = pdu[0]
        if op in (att.MTU_RSP, att.ERROR_RSP) and len(answered) < 2:
            answered.append(pdu.hex(" "))
            continue
        if op == att.READ_BY_TYPE_REQ:
            start = struct.unpack("<H", pdu[1:3])[0]
            items = [c for c in CHARS if c[0] >= start]
            if not items:
                await loop.sock_sendall(sock, bytes([att.ERROR_RSP, op]) + pdu[1:3] + b"\x0a")
            else:
                body = b"".join(struct.pack("<HBH", c[0], c[1], c[2]) + c[3] for c in items[:1])
                await loop.sock_sendall(sock, bytes([att.READ_BY_TYPE_RSP, 21]) + body)
        elif op == att.FIND_INFO_REQ:
            await loop.sock_sendall(sock, bytes([att.ERROR_RSP, op]) + pdu[1:3] + b"\x0a")
        elif op == att.WRITE_REQ:
            await loop.sock_sendall(sock, bytes([att.ERROR_RSP, op]) + pdu[1:3] + b"\x01")
        elif op == att.WRITE_CMD:
            await loop.sock_sendall(sock, bytes([att.NOTIFY]) + struct.pack("<H", 0x10) + p.frame(p.ACK, [pdu[4], 0]))


async def main():
    answered, got, events, closed = [], [], [], []

    def factory():
        a, b = pipe_pair()
        asyncio.get_event_loop().create_task(speaker(b, answered))
        return a

    link = att.AttLink(got.append, lambda: closed.append(1), sock_factory=factory, on_event=events.append)
    link._loop = MsgLoop(asyncio.get_event_loop())
    await link.open(None, p.RX_UUID, p.TX_UUID)
    await asyncio.sleep(1.5)
    await link.write(p.req_player_info())
    await asyncio.sleep(0.3)
    print("connected:", link.is_connected, "| closed:", bool(closed))
    print("speaker got answers:", answered)
    print("notifications:", [g.hex(" ") for g in got])
    for e in events:
        print("event:", e)
    assert link.is_connected and not closed, "link dropped"
    assert len(answered) == 2, answered
    assert got, "no notification"
    await link.close()


fastclock.run(main())

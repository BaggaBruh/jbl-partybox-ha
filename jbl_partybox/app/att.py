"""Minimal GATT client over classic Bluetooth (ATT on L2CAP PSM 31).

When the speaker is connected to the host over BR/EDR (e.g. for A2DP audio), BlueZ reports it as
connected but doesn't browse its GATT database, so bleak sees no services. The speaker still
serves GATT over BR/EDR (it lists the GAP service 0x1800 in SDP), so we open the ATT channel
ourselves, find the PartyBox RX/TX characteristics, enable notifications and write commands.
"""
import asyncio
import socket
import struct
import uuid

ATT_PSM = 31

ERROR_RSP = 0x01
MTU_REQ, MTU_RSP = 0x02, 0x03
FIND_INFO_REQ, FIND_INFO_RSP = 0x04, 0x05
FIND_BY_TYPE_REQ = 0x06
READ_BY_GROUP_REQ = 0x10
READ_BY_TYPE_REQ, READ_BY_TYPE_RSP = 0x08, 0x09
WRITE_REQ, WRITE_RSP = 0x12, 0x13
WRITE_CMD = 0x52
NOTIFY, INDICATE, CONFIRM = 0x1B, 0x1D, 0x1E

RESPONSE_OPCODES = {0x01, 0x03, 0x05, 0x07, 0x09, 0x0B, 0x0D, 0x0F, 0x11, 0x13, 0x17, 0x19, 0x21}
NO_REPLY_OPCODES = {CONFIRM, WRITE_CMD, 0xD2}  # peer confirmations / commands need no answer
ERR_REQ_NOT_SUPPORTED, ERR_ATTR_NOT_FOUND = 0x06, 0x0A
OUR_MTU = 512

UUID_CHAR_DECL = 0x2803
UUID_CCCD = 0x2902
PROP_WRITE_NO_RSP, PROP_WRITE, PROP_NOTIFY, PROP_INDICATE = 0x04, 0x08, 0x10, 0x20


class AttError(Exception):
    pass


def _uuid_from_le(b):
    if len(b) == 2:
        return f"0000{struct.unpack('<H', b)[0]:04x}-0000-1000-8000-00805f9b34fb"
    return str(uuid.UUID(bytes=bytes(reversed(b))))


source_address = None  # local adapter to connect from; None lets the kernel choose


def open_l2cap_socket():
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    except (AttributeError, OSError) as e:
        raise AttError(f"L2CAP sockets unavailable ({e}); the app needs host_network") from e
    sock.setblocking(False)
    if source_address:
        sock.bind((source_address, 0))
    return sock


class AttLink:
    def __init__(self, on_data, on_closed, sock_factory=open_l2cap_socket, on_event=None):
        self._on_data = on_data
        self._on_closed = on_closed
        self._on_event = on_event or (lambda msg: None)
        self._sock_factory = sock_factory
        self._sock = None
        self._loop = asyncio.get_event_loop()
        self._reader = None
        self._pending = None  # Future for the single outstanding request
        self._req_lock = asyncio.Lock()
        self.characteristics = []  # (decl_handle, props, value_handle, uuid)
        self.notes = []  # setup diagnostics for the debug log
        self.last_error = None
        self._tx = None

    @property
    def is_connected(self):
        return self._sock is not None

    async def open(self, address, rx_uuid, tx_uuid, timeout=15.0):
        sock = self._sock_factory()
        try:
            if address is not None:
                await asyncio.wait_for(self._loop.sock_connect(sock, (address, ATT_PSM)), timeout)
        except BaseException:
            sock.close()
            raise
        self._sock = sock
        self._reader = asyncio.create_task(self._read_loop())
        await self._discover()
        chars = {c[3]: c for c in self.characteristics}
        tx, rx = chars.get(tx_uuid), chars.get(rx_uuid)
        if tx is None:
            raise AttError("PartyBox TX characteristic not in the BR/EDR GATT database")
        self._tx = tx
        notify = rx if rx and rx[1] & (PROP_NOTIFY | PROP_INDICATE) else tx
        value = b"\x01\x00" if notify[1] & PROP_NOTIFY else b"\x02\x00"
        cccd = await self._find_cccd(notify)
        # The CCCD normally sits right after the value; try there if discovery found nothing
        for handle in dict.fromkeys(h for h in (cccd, notify[2] + 1) if h is not None):
            rsp = await self._request(
                bytes([WRITE_REQ]) + struct.pack("<H", handle) + value, WRITE_RSP, allow_error=True)
            if rsp is not None:
                self.notes.append(f"notifications enabled via CCCD {handle:#06x}")
                break
            self.notes.append(f"enabling notifications via {handle:#06x} failed: {self.last_error}")
        else:
            self.notes.append("no writable CCCD; relying on the speaker sending notifications by default")

    def describe(self):
        return ", ".join(f"{c[2]:#06x} {c[3][4:8] if c[3].endswith('00805f9b34fb') else c[3]} props {c[1]:#04x}"
                         for c in self.characteristics)

    async def _discover(self):
        start = 1
        while start <= 0xFFFF:
            rsp = await self._request(
                struct.pack("<BHHH", READ_BY_TYPE_REQ, start, 0xFFFF, UUID_CHAR_DECL), READ_BY_TYPE_RSP, allow_error=True)
            if rsp is None:
                break
            item_len, data = rsp[1], rsp[2:]
            last = start
            for i in range(0, len(data) - item_len + 1, item_len):
                item = data[i:i + item_len]
                decl, props, value_handle = struct.unpack("<HBH", item[:5])
                self.characteristics.append((decl, props, value_handle, _uuid_from_le(item[5:])))
                last = decl
            if last == 0xFFFF or last < start:
                break
            start = last + 1

    async def _find_cccd(self, char):
        later = [c[0] for c in self.characteristics if c[0] > char[2]]
        end = min(later) - 1 if later else 0xFFFF
        start = char[2] + 1
        while start <= end:
            rsp = await self._request(struct.pack("<BHH", FIND_INFO_REQ, start, end), FIND_INFO_RSP, allow_error=True)
            if rsp is None:
                self.notes.append(f"find descriptors {start:#06x}-{end:#06x}: {self.last_error}")
                return None
            fmt, data = rsp[1], rsp[2:]
            step = 4 if fmt == 1 else 18
            found = []
            for i in range(0, len(data) - step + 1, step):
                handle = struct.unpack("<H", data[i:i + 2])[0]
                found.append(f"{handle:#06x}:{_uuid_from_le(data[i + 2:i + step])[4:8]}")
                if fmt == 1 and struct.unpack("<H", data[i + 2:i + 4])[0] == UUID_CCCD:
                    return handle
            self.notes.append(f"descriptors after {char[2]:#06x}: {', '.join(found) or 'none'}")
            if not found:
                return None
            start = int(found[-1].split(":")[0], 16) + 1
        return None

    async def _request(self, pdu, expect, allow_error=False, timeout=5.0):
        async with self._req_lock:
            self._pending = self._loop.create_future()
            await self._loop.sock_sendall(self._sock, pdu)
            rsp = await asyncio.wait_for(self._pending, timeout)
        if rsp[0] == ERROR_RSP:
            self.last_error = f"ATT error {rsp[4]:#04x}" if len(rsp) >= 5 else "ATT error"
            if allow_error:
                return None
            raise AttError(f"ATT error {rsp[4]:#04x} for request {pdu[0]:#04x}")
        if rsp[0] != expect:
            raise AttError(f"unexpected ATT response {rsp[0]:#04x}")
        return rsp

    async def _read_loop(self):
        try:
            while True:
                pdu = await self._loop.sock_recv(self._sock, 1024)
                if not pdu:
                    self._on_event("ATT channel closed by the speaker")
                    break
                op = pdu[0]
                if op in (NOTIFY, INDICATE):
                    if op == INDICATE:
                        await self._loop.sock_sendall(self._sock, bytes([CONFIRM]))
                    self._on_data(pdu[3:])
                elif op in RESPONSE_OPCODES:
                    if self._pending and not self._pending.done():
                        self._pending.set_result(pdu)
                    else:
                        self._on_event(f"unexpected ATT response {pdu.hex(' ')}")
                elif op not in NO_REPLY_OPCODES:
                    await self._answer_peer_request(pdu)
        except OSError as e:
            if self._sock is not None:
                self._on_event(f"ATT channel error: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            if self._sock is not None:
                self._sock.close()
                self._sock = None
                self._on_closed()

    async def _answer_peer_request(self, pdu):
        """The speaker is also a GATT client on this channel; an unanswered request makes it drop us.
        We expose no services, so: answer MTU, and say "not found"/"not supported" to the rest."""
        op = pdu[0]
        if op == MTU_REQ:
            reply = bytes([MTU_RSP]) + struct.pack("<H", OUR_MTU)
        else:
            handle = pdu[1:3] if len(pdu) >= 3 else b"\x00\x00"
            code = ERR_ATTR_NOT_FOUND if op in (FIND_INFO_REQ, FIND_BY_TYPE_REQ, READ_BY_TYPE_REQ,
                                                 READ_BY_GROUP_REQ) else ERR_REQ_NOT_SUPPORTED
            reply = bytes([ERROR_RSP, op]) + handle + bytes([code])
        self._on_event(f"speaker ATT request {pdu.hex(' ')} -> {reply.hex(' ')}")
        await self._loop.sock_sendall(self._sock, reply)

    async def write(self, data):
        if self._sock is None:
            raise AttError("ATT link closed")
        handle = struct.pack("<H", self._tx[2])
        if self._tx[1] & PROP_WRITE_NO_RSP:
            await self._loop.sock_sendall(self._sock, bytes([WRITE_CMD]) + handle + data)
        else:
            await self._request(bytes([WRITE_REQ]) + handle + data, WRITE_RSP)

    async def close(self):
        sock, self._sock = self._sock, None
        if self._reader:
            self._reader.cancel()
        if sock:
            sock.close()

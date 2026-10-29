"""JBL PartyBox BLE protocol, reconstructed from the JBL PartyBox Android app (3.14.20).

Frames are `AA <cmd> <len> <payload>`. Writes go to TX, replies arrive as notifications on RX.
"""

SERVICE_UUID = "65786365-6c70-6f69-6e74-2e636f6d0000"
RX_UUID = "65786365-6c70-6f69-6e74-2e636f6d0001"
TX_UUID = "65786365-6c70-6f69-6e74-2e636f6d0002"
ADV_SERVICE_UUIDS = {
    "0000fddf-0000-1000-8000-00805f9b34fb",  # PartyBox
    "0000dffd-0000-1000-8000-00805f9b34fb",  # Pro / Ultimate
    "0000fc69-0000-1000-8000-00805f9b34fb",  # Pro (new)
}

IDENT = 0xAA

# Command bytes (com.harman.sdk.utils.PacketFormat)
ACK = 0x00
REQ_POWER_OFF = 0x03
REQ_DEV_INFO = 0x11
RESP_DEV_INFO = 0x12
SET_DEV_INFO = 0x13
SET_LIGHT_INFO = 0x33
REQ_PLAYER_INFO = 0x41
RESP_PLAYER_INFO = 0x42
SET_PLAYER_INFO = 0x43
REQ_BASS_BOOST = 0x61
RET_BASS_BOOST = 0x62
SET_BASS_BOOST = 0x63
REQ_SIMPLE_EQ = 0x91
RET_SIMPLE_EQ = 0x92
SET_SIMPLE_EQ = 0x93
HEARTBEAT = 0xEA
RES_DEVICE_DELAY_DURATION = 0xEB
REQ_FEEDBACK_TONE = 0xF1
RET_FEEDBACK_TONE = 0xF2
SET_FEEDBACK_TONE = 0xF3

# Player tokens (SET_PLAYER_INFO / RESP_PLAYER_INFO)
TOK_PLAY = 0x41
TOK_VOLUME = 0x42
TOK_MUTE = 0x43
TOK_SONG = 0x44
TOK_ARTIST = 0x45
TOK_PRIMARY_VOLUME = 0x46
TOK_SECONDARY_VOLUME = 0x47

# Device info tokens (SET_DEV_INFO / RESP_DEV_INFO)
TOK_PRODUCT_ID = 0x31
TOK_MODEL_ID = 0x32
TOK_BATTERY = 0x33
TOK_LINKED_COUNT = 0x34
TOK_CHANNEL = 0x35
TOK_AUDIO_SOURCE = 0x36
TOK_MAC = 0x37
TOK_ROLE = 0x38
TOK_PARTY_MODE = 0x39
TOK_SERIAL = 0x40
TOK_FIRMWARE = 0x41
TOK_VOLUME_BOOST = 0x54
TOK_LOCK_PANEL = 0x55
TOK_DEVICE_NAME = 0xC1

# Light tokens (SET_LIGHT_INFO)
LIGHT_PATTERN = 0x31
LIGHT_COLOR = 0x32
LIGHT_LOOP = 0x36
LIGHT_MAIN_SWITCH = 0x99

VOLUME_MAX = 32
PLAY_PAUSE, PLAY_PLAY, PLAY_PREV, PLAY_NEXT = 1, 2, 3, 4
PLAYER_STATUS = {0: "off", 1: "paused", 2: "playing", 3: "prev", 4: "next"}
AUDIO_SOURCES = {0: "none", 1: "bluetooth", 2: "aux", 3: "usb", 4: "optical", 7: "usb_audio"}
BASS_BOOST_LEVELS = {0: "off", 1: "boost_1", 2: "boost_2"}
EQ_CUSTOM_CATEGORY = 0xC1
LIGHT_PATTERNS = {
    1: "Rock", 2: "Neon", 3: "Club", 4: "Flow", 5: "Ripple", 6: "Cross", 7: "Flash",
    9: "Loop", 10: "Bounce", 11: "Trim", 12: "Switch", 13: "Freeze",
}

# The app sends EQ band levels -2..+2 as bytes 4..0 (EQFragment.mapCustomLevelToBandValues)
_EQ_LEVEL_TO_BYTE = {-2: 4, -1: 3, 0: 2, 1: 1, 2: 0}
_EQ_BYTE_TO_LEVEL = {v: k for k, v in _EQ_LEVEL_TO_BYTE.items()}


def frame(cmd, payload=b""):
    payload = bytes(payload)
    return bytes([IDENT, cmd, len(payload)]) + payload


def _token_cmd(cmd, token, value, dev_idx=0):
    return frame(cmd, [dev_idx, token, 1, value & 0xFF])


# ---- Requests ----

def req_device_info():
    return frame(REQ_DEV_INFO)


def req_player_info():
    return frame(REQ_PLAYER_INFO)


def req_battery(dev_idx=0):
    return frame(SET_DEV_INFO, [dev_idx, TOK_BATTERY])


def req_bass_boost():
    return frame(REQ_BASS_BOOST)


def req_simple_eq():
    return frame(REQ_SIMPLE_EQ, [0])


def req_feedback_tone():
    return frame(REQ_FEEDBACK_TONE)


def heartbeat():
    return frame(HEARTBEAT)


# ---- Setters ----

def set_volume(value, dev_idx=0):
    return _token_cmd(SET_PLAYER_INFO, TOK_VOLUME, _clamp(value, 0, VOLUME_MAX), dev_idx)


def set_primary_volume(value, dev_idx=0):
    return _token_cmd(SET_PLAYER_INFO, TOK_PRIMARY_VOLUME, _clamp(value, 0, VOLUME_MAX), dev_idx)


def set_secondary_volume(value, dev_idx=0):
    return _token_cmd(SET_PLAYER_INFO, TOK_SECONDARY_VOLUME, _clamp(value, 0, VOLUME_MAX), dev_idx)


def set_mute(on, dev_idx=0):
    return _token_cmd(SET_PLAYER_INFO, TOK_MUTE, 1 if on else 0, dev_idx)


def set_play(action, dev_idx=0):
    return _token_cmd(SET_PLAYER_INFO, TOK_PLAY, action, dev_idx)


def set_bass_boost(level):
    return frame(SET_BASS_BOOST, [_clamp(level, 0, 2)])


def set_simple_eq(enabled, bass, mid, treble, dev_idx=0):
    bands = [_EQ_LEVEL_TO_BYTE[_clamp(v, -2, 2)] for v in (bass, mid, treble)]
    return frame(SET_SIMPLE_EQ, [dev_idx, EQ_CUSTOM_CATEGORY, 1 if enabled else 0, *bands])


def set_feedback_tone(on):
    return frame(SET_FEEDBACK_TONE, [1 if on else 0])


def set_volume_boost(on, dev_idx=0):
    return _token_cmd(SET_DEV_INFO, TOK_VOLUME_BOOST, 1 if on else 0, dev_idx)


def set_lock_panel(on, dev_idx=0):
    return _token_cmd(SET_DEV_INFO, TOK_LOCK_PANEL, 1 if on else 0, dev_idx)


def set_device_name(name, dev_idx=0):
    raw = name.encode("utf-8")[:16]
    return frame(SET_DEV_INFO, [dev_idx, TOK_DEVICE_NAME, len(raw), *raw])


def set_light_switch(on, dev_idx=0):
    return _token_cmd(SET_LIGHT_INFO, LIGHT_MAIN_SWITCH, 1 if on else 0, dev_idx)


def set_light_pattern(pattern_id, dev_idx=0):
    return _token_cmd(SET_LIGHT_INFO, LIGHT_PATTERN, pattern_id, dev_idx)


def set_light_color(r, g, b, dev_idx=0):
    return frame(SET_LIGHT_INFO, [dev_idx, LIGHT_LOOP, 1, 1, LIGHT_COLOR, 3, r & 0xFF, g & 0xFF, b & 0xFF])


def power_off():
    return frame(REQ_POWER_OFF, [4])


def _clamp(v, lo, hi):
    return max(lo, min(hi, int(v)))


# ---- Parsing ----

class FrameReader:
    """Reassembles frames from notification chunks."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data):
        """Returns (frames, junk): complete (cmd, payload) tuples and bytes that weren't a frame."""
        self._buf += data
        frames, junk = [], bytearray()
        while self._buf:
            if self._buf[0] != IDENT:
                idx = self._buf.find(IDENT)
                cut = len(self._buf) if idx < 0 else idx
                junk += self._buf[:cut]
                del self._buf[:cut]
                continue
            if len(self._buf) < 3:
                break
            total = 3 + self._buf[2]
            if len(self._buf) < total:
                break
            frames.append((self._buf[1], bytes(self._buf[3:total])))
            del self._buf[:total]
        return frames, bytes(junk)


def parse_battery(b):
    if b == 0xF0:
        return {"battery": None, "charging": False, "ac_powered": True}
    return {"battery": b & 0x7F, "charging": bool(b & 0x80), "ac_powered": False}


def parse_player_info(payload):
    """payload = <dev_idx> then <token> <len> <value...> repeated."""
    out = {}
    i = 1
    while i + 2 <= len(payload):
        tok, ln = payload[i], payload[i + 1]
        val = payload[i + 2:i + 2 + ln]
        i += 2 + ln
        num = int.from_bytes(val, "big") if val else 0
        if tok == TOK_PLAY:
            out["player_status"] = PLAYER_STATUS.get(num, num)
        elif tok == TOK_VOLUME:
            out["volume"] = num
        elif tok == TOK_MUTE:
            out["muted"] = bool(num)
        elif tok == TOK_SONG:
            out["song"] = val.decode("utf-8", "replace").strip()
        elif tok == TOK_ARTIST:
            out["artist"] = val.decode("utf-8", "replace").strip()
        elif tok == TOK_PRIMARY_VOLUME:
            out["primary_volume"] = num
        elif tok == TOK_SECONDARY_VOLUME:
            out["secondary_volume"] = num
    return out


def parse_device_info(payload):
    """payload = <dev_idx> then <token> <len> <value...> repeated (as seen from a PartyBox over GATT)."""
    out = {}
    i = 1
    while i + 1 < len(payload):
        tok, ln = payload[i], payload[i + 1]
        val = payload[i + 2:i + 2 + ln]
        i += 2 + ln
        if not val:
            continue
        if tok == TOK_PRODUCT_ID:
            out["product_id"] = val.hex().upper()
        elif tok == TOK_MODEL_ID:
            out["model_id"] = val.hex().upper()
        elif tok == TOK_BATTERY:
            out.update(parse_battery(val[0]))
        elif tok == TOK_AUDIO_SOURCE:
            out["audio_source"] = AUDIO_SOURCES.get(val[0], val[0])
        elif tok == TOK_MAC:
            out["mac"] = ":".join(f"{b:02X}" for b in val)
        elif tok == TOK_SERIAL:
            out["serial"] = val.decode("ascii", "replace")
        elif tok == TOK_FIRMWARE:
            out["firmware"] = ".".join(str(b) for b in val)
        elif tok == TOK_DEVICE_NAME:
            out["name"] = val.decode("utf-8", "replace")
        elif tok == TOK_VOLUME_BOOST:
            out["volume_boost"] = bool(val[0])
        elif tok == TOK_LOCK_PANEL:
            out["lock_panel"] = bool(val[0])
    return out


def parse_simple_eq(payload):
    """payload = <dev_idx> <category> <status> <bass> <mid> <treble>"""
    if len(payload) < 6:
        return {}
    return {
        "eq_category": payload[1],
        "eq_enabled": bool(payload[2]),
        "eq_bass": _EQ_BYTE_TO_LEVEL.get(payload[3], 0),
        "eq_mid": _EQ_BYTE_TO_LEVEL.get(payload[4], 0),
        "eq_treble": _EQ_BYTE_TO_LEVEL.get(payload[5], 0),
    }


def parse_frame(cmd, payload):
    """Returns a dict of state updates (possibly empty) plus '_ack' / '_heartbeat_s' meta keys."""
    if cmd == RESP_PLAYER_INFO:
        return parse_player_info(payload)
    if cmd == RESP_DEV_INFO:
        return parse_device_info(payload)
    if cmd == RET_BASS_BOOST and payload:
        return {"bass_boost": payload[0]}
    if cmd == RET_SIMPLE_EQ:
        return parse_simple_eq(payload)
    if cmd == RET_FEEDBACK_TONE and payload:
        return {"feedback_tone": bool(payload[0])}
    if cmd == RES_DEVICE_DELAY_DURATION and payload:
        return {"_heartbeat_s": int.from_bytes(payload, "big")}
    if cmd == ACK and len(payload) >= 2:
        return {"_ack": (payload[0], payload[1])}
    return {}

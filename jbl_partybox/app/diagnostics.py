"""A report for bug reports. People post these publicly, so personal details are removed."""
import json
import platform
import re
from pathlib import Path

import protocol as p

_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:_-][0-9A-Fa-f]{2}){5}\b")
_NOTE_FIELDS = re.compile(r'"(name|song|artist|serial|mac)": "(?:[^"\\]|\\.)*"')
# <cmd>: tokens with personal values, in frames shaped <dev_idx> then <token> <len> <value>
_TLV_TOKENS = {
    p.RESP_DEV_INFO: {p.TOK_MAC, p.TOK_SERIAL, p.TOK_DEVICE_NAME},
    p.RESP_PLAYER_INFO: {0x44, 0x45},  # song, artist
}
_ALWAYS_BLANK = {0x85}  # the address of a linked speaker
_SAFE_STATE = ("product_id", "model_id", "firmware", "audio_source", "player_status", "battery", "charging",
               "ac_powered", "features", "bass_boost", "eq_enabled", "light_on", "light_pattern")


def scrub_hex(hex_str):
    """Blank the personal values in one logged frame ("aa 12 05 ..."), keeping its structure."""
    try:
        data = bytearray.fromhex(hex_str)
    except ValueError:
        return "(unparsed frame removed)"
    if len(data) < 3 or data[0] != p.IDENT:
        return "(unframed data removed)" if data else ""
    cmd = data[1]
    keep = [f"{b:02x}" for b in data]
    if cmd in _ALWAYS_BLANK or (cmd == 0x13 and p.TOK_DEVICE_NAME in data[3:5]):
        keep[3:] = ["xx"] * (len(keep) - 3)
    elif cmd in _TLV_TOKENS:
        i = 4
        while i + 1 < len(data):
            tok, ln = data[i], data[i + 1]
            if tok in _TLV_TOKENS[cmd]:
                keep[i + 2:i + 2 + ln] = ["xx"] * len(keep[i + 2:i + 2 + ln])
            i += 2 + ln
    return " ".join(keep)


def scrub_text(text, secrets=()):
    text = _NOTE_FIELDS.sub(lambda m: f'"{m.group(1)}": "(removed)"', text)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "(name)")
    return _MAC.sub("(address)", text)


def _version():
    for path in (Path(__file__).parent / "config.yaml", Path(__file__).parent.parent / "config.yaml"):
        try:
            match = re.search(r'^version:\s*"?([^"\n]+)', path.read_text(), re.M)
            if match:
                return match.group(1)
        except OSError:
            pass
    return "unknown"


def report(mgr, mqtt_status):
    secrets = {mgr.state.get("name"), mgr.state.get("song"), mgr.state.get("artist"), mgr.state.get("serial")}
    secrets |= {d.get("name") for d in [*mgr.known.values(), *mgr.paired.values(), *mgr.devices.values()]}
    secrets -= {"Not Playing"}
    bluetooth = mgr.snapshot()["bluetooth"]
    return {
        "app_version": _version(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "status": mgr.status,
        "transport": mgr.transport,
        "reconnecting": mgr.snapshot()["reconnecting"],
        "autoconnect": mgr.autoconnect if mgr.autoconnect in ("last", "off") else "a specific speaker",
        "mqtt": scrub_text(mqtt_status, secrets),
        "bluetooth": json.loads(scrub_text(json.dumps(bluetooth), secrets)),
        "capabilities": p.capabilities(mgr.state),
        "state": {k: mgr.state[k] for k in _SAFE_STATE if k in mgr.state},
        "speakers": [
            {k: d[k] for k in ("known", "paired", "partybox", "ble", "rssi")}
            for d in mgr.snapshot()["devices"]
        ],
        "log": [
            {"t": round(e["t"], 2), "dir": e["dir"], "hex": scrub_hex(e["hex"]), "note": scrub_text(e["note"], secrets)}
            for e in mgr.log
        ],
    }

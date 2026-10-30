"""Model capabilities and the diagnostics scrubber."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jbl_partybox" / "app"))
import diagnostics  # noqa: E402
import protocol as p  # noqa: E402
import speaker  # noqa: E402

speaker.STATE_FILE = Path(tempfile.gettempdir()) / "last_device_features.json"
speaker.STATE_FILE.unlink(missing_ok=True)

# ---- Capabilities ----
caps = p.capabilities({})
assert all(caps[k] for k in ("eq", "feedback_tone", "volume_boost", "lock_panel", "rename", "power_off")), caps
assert caps["model"] is None

upd = p.parse_frame(p.RET_FEATURE_INFO, bytes([0xA1, 1, 2, 0xA3, 1, 0, 0xA5, 1, 1, 0xAE, 1, 0, 0xB0, 1, 1]))
assert upd == {"features": {"eq": False, "feedback_tone": True, "lock_panel": False}}, upd
caps = p.capabilities(upd)
assert not caps["eq"] and not caps["lock_panel"] and caps["feedback_tone"]
assert caps["volume_boost"], "a feature that isn't mentioned stays available"

assert p.capabilities({"product_id": "1F5D"})["model"] == "PartyBox 310"
assert not p.capabilities({"product_id": "1F5D"})["power_off"]
assert not p.capabilities({"product_id": "6220"})["rename"], "the other byte order is recognised too"
assert p.capabilities({"product_id": "ABCD"})["model"] is None
assert p.parse_feature_info(bytes([0xA3])) == {"features": {}}, "truncated frames are harmless"
assert p.parse_feature_info(bytes([0xA3, 5])) == {"features": {}}

# ---- Scrubbing ----
MAC = "54:AA:BB:CC:DD:EE"
NAME, SONG, SERIAL = "Secret Speaker", "Secret Song", "SERIAL123"
dev_info = p.frame(p.RESP_DEV_INFO, bytes([0, 0x31, 2, 0x1F, 0x61, 0x37, 6, 0x54, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE,
                                           0x40, len(SERIAL)]) + SERIAL.encode()
                   + bytes([0xC1, len(NAME)]) + NAME.encode())
scrubbed = diagnostics.scrub_hex(dev_info.hex(" "))
assert "1f 61" in scrubbed, "non-personal values stay"
assert "54 aa" not in scrubbed and NAME.encode().hex(" ") not in scrubbed and SERIAL.encode().hex(" ") not in scrubbed

player = p.frame(p.RESP_PLAYER_INFO, bytes([0, 0x42, 1, 9, 0x44, len(SONG)]) + SONG.encode())
scrubbed = diagnostics.scrub_hex(player.hex(" "))
assert "42 01 09" in scrubbed and SONG.encode().hex(" ") not in scrubbed

partner = p.frame(0x85, bytes.fromhex("54AABBCCDDEE"))
assert "54" not in diagnostics.scrub_hex(partner.hex(" ")).split()[3:]
rename = p.set_device_name(NAME)
assert NAME.encode().hex(" ") not in diagnostics.scrub_hex(rename.hex(" "))
assert diagnostics.scrub_hex("zz") == "(unparsed frame removed)"

m = speaker.SpeakerManager()
m.state = {"name": NAME, "song": SONG, "serial": SERIAL, "mac": MAC, "product_id": "1F61", "firmware": "1.2"}
m.known = {MAC: {"name": NAME, "transport": "att", "last_connected": 1}}
m.address = MAC
m.health = {"error": None, "adapters": [{"id": "hci0", "address": "00:11:22:33:44:55", "name": NAME, "powered": True}]}
m.log.extend([
    {"t": 1.0, "dir": "info", "hex": "", "note": f"connected to {MAC} over att"},
    {"t": 2.0, "dir": "info", "hex": "", "note": f"BlueZ device: {{'Name': '{NAME}', 'Address': '{MAC}'}}"},
    {"t": 3.0, "dir": "rx", "hex": dev_info.hex(" "), "note": json.dumps({"name": NAME, "serial": SERIAL, "mac": MAC})},
    {"t": 4.0, "dir": "rx", "hex": player.hex(" "), "note": json.dumps({"song": "An Older Song", "volume": 9})},
])
text = json.dumps(diagnostics.report(m, f"connected to {NAME}"))
for secret in (MAC, NAME, SONG, SERIAL, "An Older Song", "00:11:22:33:44:55", "54 aa bb"):
    assert secret not in text, secret
report = json.loads(text)
assert report["capabilities"]["model"] == "PartyBox 710"
assert report["state"]["firmware"] == "1.2"
assert report["app_version"] != "unknown"
assert '"volume": 9' in report["log"][3]["note"], "harmless values stay"

print("ok")

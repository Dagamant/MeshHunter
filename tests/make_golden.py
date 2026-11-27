"""Write a synthetic sniffer capture, decoded by the desktop decoder, in the
same JSON-lines layout the sniffer logs to sniffer_packets.jsonl.

CI feeds it to the CardputerZero `mesh_tests`, which re-decodes every packet
with the C++ port and checks it agrees with the Python output:

    python tests/make_golden.py golden.jsonl
    mesh_tests golden.jsonl
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meshhunter.core.channels import make_channel, subscribed_channels  # noqa: E402
from meshhunter.core.sniffer_decode import (  # noqa: E402
    decode_meshcore,
    decode_meshtastic,
    default_meshtastic_channels,
)
from meshhunter.core.sniffer_worker import _jsonable  # noqa: E402

import packets as P  # noqa: E402

# Must match the channels test_golden() in cardputer-zero/tests/mesh_tests.cpp
# decodes with: Public only, and every preset's default channel.
MC_CHANNELS = subscribed_channels({"meshcore_channels": []})
MT_CHANNELS = default_meshtastic_channels(
    ["LongFast", "MediumFast", "MediumSlow", "ShortFast", "ShortSlow", "ShortTurbo", "LongMod", "LongSlow"])


def meshcore_packets():
    rptr = P.ed25519_key("golden-repeater")
    chat = P.ed25519_key("golden-chat")
    yield P.mc_advert(rptr, adv_type=P.ADV_TYPE_REPEATER, lat=38.154288, lon=-81.11461, name="Gauley Rptr")
    yield P.mc_advert(rptr, adv_type=P.ADV_TYPE_REPEATER, lat=38.154288, lon=-81.11461, name="Gauley Rptr",
                      path=bytes.fromhex("a1b2"))
    yield P.mc_advert(chat, adv_type=P.ADV_TYPE_CHAT, name="phone")
    yield P.mc_advert(chat, adv_type=P.ADV_TYPE_ROOM, lat=-33.8688, lon=151.2093, name="Sydney Room")
    yield P.mc_advert(chat, lat=1.0, lon=2.0, name="forged", corrupt_signature=True)
    yield P.mc_grp_txt("Alice: hello mesh")
    yield P.mc_grp_txt("Bob: a longer public message that spans several AES blocks", path=b"\x42")
    yield P.mc_grp_txt("Carol: not subscribed", key=make_channel("#golden")["key"])
    yield P.mc_packet(P.ROUTE_DIRECT, 0x02, b"\xaa\xbb" + bytes(18), path=bytes.fromhex("0102"))
    yield P.mc_packet(P.ROUTE_FLOOD, 0x07, b"\xcc" + bytes(range(32)) + bytes(16))
    yield P.mc_packet(P.ROUTE_FLOOD, 0x03, bytes(4))


def meshtastic_packets():
    yield P.mt_text("hello from the mesh")
    yield P.mt_text("medium fast", channel="MediumFast", sender=0x0A0B0C0D, hop_limit=1)
    yield P.mt_text("to you", dest=0x01020304, channel="ShortTurbo")
    yield P.mt_position(38.1542881, -81.1146102, alt=612)
    yield P.mt_nodeinfo("!1234abcd", "Base Camp", "BC", hw_model=43, role=2, public_key=bytes(range(32)))
    yield P.mt_text("private", key=bytes(range(16)), channel="Private")
    yield P.mt_text("dm", dest=0x55555555, chan_hash=0, key=bytes(range(16)))


def records():
    for raw in meshcore_packets():
        yield {"hex": raw.hex(), "sw": "12", "crc": True, "decoded": _jsonable(decode_meshcore(raw, MC_CHANNELS))}
    for raw in meshtastic_packets():
        yield {"hex": raw.hex(), "sw": "2b", "crc": True, "decoded": _jsonable(decode_meshtastic(raw, MT_CHANNELS))}


def main(argv):
    out = open(argv[1], "w", encoding="utf-8") if len(argv) > 1 else sys.stdout
    with out:
        for rec in records():
            out.write(json.dumps(rec, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main(sys.argv)

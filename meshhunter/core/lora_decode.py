"""Over-the-air packet decoding for raw LoRa frames heard by the HAT's
SX1262: MeshCore and Meshtastic, plus the radio presets/frequency maths
each network uses.

decode_meshcore()/decode_meshtastic() never raise on malformed input --
the radio hands us whatever it heard, CRC-valid or not -- they return a
DecodedPacket with whatever could be made sense of.
"""

import hashlib
import hmac
import struct
from dataclasses import dataclass, field

from Crypto.Cipher import AES

from .constants import ADV_TYPE_NAMES

# ---- radio presets -----------------------------------------------------------

MESHCORE_SYNC_WORD = 0x12
MESHTASTIC_SYNC_WORD = 0x2B
LORAWAN_SYNC_WORD = 0x34
PREAMBLE_LEN = 16

# name -> (freq MHz, bw kHz, sf, cr) -- MeshCore's own app presets.
MESHCORE_PRESETS = {
    "USA/Canada": (910.525, 62.5, 7, 5),
    "EU/UK (Narrow)": (869.618, 62.5, 8, 8),
}
MESHCORE_BANDWIDTHS = (62.5, 125.0, 250.0, 500.0)

# name -> (bw kHz, sf, cr). The preset's display name doubles as the
# default channel name, which is what the frequency slot is hashed from.
MESHTASTIC_PRESETS = {
    "LongFast": (250.0, 11, 5),
    "MediumFast": (250.0, 9, 5),
}

# region -> (start MHz, end MHz), from Meshtastic's RadioInterface.cpp.
# Only regions inside the HAT's 860-960 MHz SX1262 front end.
MESHTASTIC_REGIONS = {
    "US": (902.0, 928.0),
    "EU_868": (869.4, 869.65),
    "ANZ": (915.0, 928.0),
    "IN": (865.0, 867.0),
    "NZ_865": (864.0, 868.0),
    "KR": (920.0, 923.0),
    "TW": (920.0, 925.0),
}


def meshtastic_frequency_mhz(region, preset):
    """Centre frequency Meshtastic firmware picks for the default
    (unnamed, channel_num 0) primary channel: djb2 hash of the preset name
    modulo the number of bw-wide slots in the region."""
    start, end = MESHTASTIC_REGIONS[region]
    bw_mhz = MESHTASTIC_PRESETS[preset][0] / 1000.0
    num_channels = max(1, int((end - start) // bw_mhz))
    h = 5381
    for ch in preset.encode():
        h = (h * 33 + ch) & 0xFFFFFFFF
    slot = h % num_channels
    return start + bw_mhz / 2 + slot * bw_mhz


# ---- shared -------------------------------------------------------------------


@dataclass
class DecodedPacket:
    network: str
    kind: str  # short type label for the messages table
    sender: str = ""
    text: str = ""
    hops: str = ""
    # Present when the packet advertises a node position/identity; consumed
    # by the node logger (MeshCore adverts) and the packet CSV.
    node: dict = field(default_factory=dict)
    lat: float | None = None
    lon: float | None = None


# ---- raw capture ---------------------------------------------------------------

CAPTURE_SHOW_BYTES = 24  # on-screen preview; the CSV keeps the full frame


def decode_capture(raw, crc_ok=True):
    """No protocol decoding at all -- just the frame as spaced hex (spaces
    let long frames word-wrap), plus its ASCII if it's mostly printable."""
    shown = raw[:CAPTURE_SHOW_BYTES].hex(" ")
    if len(raw) > CAPTURE_SHOW_BYTES:
        shown += " …"
    printable = sum(32 <= b < 127 for b in raw)
    if raw and printable / len(raw) > 0.8:
        shown += f'  "{raw.decode("ascii", "replace")}"'
    return DecodedPacket("Capture", "RAW" if crc_ok else "RAW (CRC ERR)", sender=f"{len(raw)} B", text=shown)


# ---- MeshCore -----------------------------------------------------------------

_MC_ROUTES = ["TC_FLOOD", "FLOOD", "DIRECT", "TC_DIRECT"]
_MC_PAYLOADS = [
    "REQ", "RESPONSE", "TXT_MSG", "ACK", "ADVERT", "GRP_TXT", "GRP_DATA",
    "ANON_REQ", "PATH", "TRACE", "MULTIPART", "CONTROL",
]
_MC_PAYLOADS += [f"TYPE_{i}" for i in range(len(_MC_PAYLOADS), 15)] + ["RAW_CUSTOM"]

MESHCORE_PUBLIC_SECRET = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")


class MeshCoreChannel:
    def __init__(self, name, secret):
        self.name = name
        self.secret = secret
        self.hash = hashlib.sha256(secret).digest()[0]


def meshcore_channels(hashtag_names=()):
    """Public plus any '#name' hashtag channels, whose secret is the first
    16 bytes of sha256 of the channel name (including the '#')."""
    channels = [MeshCoreChannel("Public", MESHCORE_PUBLIC_SECRET)]
    for name in hashtag_names:
        name = name.strip()
        if not name:
            continue
        if not name.startswith("#"):
            name = "#" + name
        channels.append(MeshCoreChannel(name, hashlib.sha256(name.encode()).digest()[:16]))
    return channels


def decode_meshcore(raw, channels):
    try:
        return _decode_meshcore(raw, channels)
    except (IndexError, ValueError, struct.error):
        return DecodedPacket("MeshCore", "MALFORMED", text=raw.hex())


def _decode_meshcore(raw, channels):
    header = raw[0]
    route = header & 0x03
    ptype = (header >> 2) & 0x0F
    pos = 1
    if route in (0, 3):
        pos += 4  # transport codes
    path_byte = raw[pos]
    pos += 1
    hash_size = (path_byte >> 6) + 1
    hop_count = path_byte & 0x3F
    path = raw[pos:pos + hop_count * hash_size]
    pos += hop_count * hash_size
    body = raw[pos:]
    if len(path) != hop_count * hash_size:
        raise ValueError("truncated path")

    route_name = _MC_ROUTES[route]
    pkt = DecodedPacket("MeshCore", _MC_PAYLOADS[ptype], hops=f"{route_name} {hop_count}")
    if hop_count:
        pkt.hops += " via " + ",".join(path[i:i + hash_size].hex() for i in range(0, len(path), hash_size))

    if ptype == 4:
        _decode_mc_advert(body, pkt)
    elif ptype == 5:
        _decode_mc_grp_txt(body, pkt, channels)
    elif ptype in (0, 1, 2, 8):
        # dest hash, src hash, MAC, ciphertext -- end-to-end encrypted with
        # the pair's shared secret, so only the routing hashes are visible.
        pkt.sender = body[1:2].hex()
        pkt.text = f"to {body[0:1].hex()} (encrypted, {len(body) - 4} B)"
    elif ptype == 7:
        pkt.sender = body[1:33].hex()[:8]
        pkt.text = f"to {body[0:1].hex()} (encrypted)"
    elif ptype == 3:
        pkt.text = f"ack {body[:4].hex()}"
    elif ptype == 9:
        pkt.text = f"trace tag {body[:4].hex()}"
    else:
        pkt.text = f"{len(body)} B"
    return pkt


def _decode_mc_advert(body, pkt):
    pubkey = body[0:32]
    flags = body[100]
    adv_type = flags & 0x0F
    pos = 101
    lat = lon = None
    if flags & 0x10:
        lat_i, lon_i = struct.unpack_from("<ii", body, pos)
        lat, lon = lat_i / 1e6, lon_i / 1e6
        pos += 8
    if flags & 0x20:
        pos += 2
    if flags & 0x40:
        pos += 2
    name = body[pos:].decode("utf-8", "replace").strip("\x00") if flags & 0x80 else ""

    pkt.sender = name or pubkey.hex()[:8]
    type_label = ADV_TYPE_NAMES.get(adv_type, f"type {adv_type}")
    pkt.text = type_label
    if lat is not None and (lat or lon):
        pkt.lat, pkt.lon = lat, lon
        pkt.text += f" @ {lat:.5f}, {lon:.5f}"
    # Same shape as a meshcore-library contact dict, so it can go straight
    # into MeshCoreWorker's node recording.
    pkt.node = {
        "public_key": pubkey.hex(),
        "adv_name": name,
        "type": adv_type,
        "adv_lat": lat if lat is not None else 0.0,
        "adv_lon": lon if lon is not None else 0.0,
        "last_advert": struct.unpack_from("<I", body, 32)[0],
        "out_path": "",
        "lastmod": "",
    }


def _decode_mc_grp_txt(body, pkt, channels):
    chan_hash, mac, ciphertext = body[0], body[1:3], body[3:]
    for ch in channels:
        if ch.hash != chan_hash:
            continue
        if hmac.new(ch.secret, ciphertext, hashlib.sha256).digest()[:2] != mac:
            continue
        if len(ciphertext) % 16:
            break
        plain = AES.new(ch.secret, AES.MODE_ECB).decrypt(ciphertext)
        message = plain[5:].split(b"\x00", 1)[0].decode("utf-8", "replace")
        sender, sep, text = message.partition(": ")
        pkt.kind = f"CHAN {ch.name}"
        pkt.sender, pkt.text = (sender, text) if sep else ("", message)
        return
    pkt.text = f"channel {chan_hash:02x} (unknown key)"


# ---- Meshtastic ---------------------------------------------------------------

MESHTASTIC_DEFAULT_KEY = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")
_BROADCAST = 0xFFFFFFFF

_PORTNUMS = {
    1: "TEXT", 2: "REMOTE_HW", 3: "POSITION", 4: "NODEINFO", 5: "ROUTING",
    6: "ADMIN", 7: "TEXT_COMPRESSED", 8: "WAYPOINT", 10: "DETECTION_SENSOR",
    32: "REPLY", 34: "PAXCOUNTER", 64: "SERIAL", 65: "STORE_FORWARD",
    66: "RANGE_TEST", 67: "TELEMETRY", 70: "TRACEROUTE", 71: "NEIGHBORINFO",
    73: "MAP_REPORT",
}


def meshtastic_channel_hash(name, key=MESHTASTIC_DEFAULT_KEY):
    h = 0
    for b in name.encode() + key:
        h ^= b
    return h


def _node_id(num):
    return f"!{num:08x}"


def _pb_fields(buf):
    """Minimal protobuf wire-format reader: yields (field_no, wire_type,
    value) with varints as int, fixed32/64 as raw bytes, and
    length-delimited fields as bytes."""
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = _varint(buf, pos)
        field_no, wire = key >> 3, key & 7
        if wire == 0:
            val, pos = _varint(buf, pos)
        elif wire == 1:
            val, pos = buf[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = _varint(buf, pos)
            val, pos = buf[pos:pos + length], pos + length
        elif wire == 5:
            val, pos = buf[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        if pos > n:
            raise ValueError("truncated protobuf")
        yield field_no, wire, val


def _varint(buf, pos):
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _pb_dict(buf):
    out = {}
    for f, _, v in _pb_fields(buf):
        out.setdefault(f, v)
    return out


def _f32(b):
    return struct.unpack("<f", b)[0] if isinstance(b, (bytes, bytearray)) and len(b) == 4 else None


def decode_meshtastic(raw, channel_name, key=MESHTASTIC_DEFAULT_KEY):
    if len(raw) < 16:
        return DecodedPacket("Meshtastic", "MALFORMED", text=raw.hex())
    dest, sender, pkt_id, flags, chan_hash = struct.unpack_from("<IIIBB", raw, 0)
    hop_limit, hop_start = flags & 0x07, flags >> 5
    pkt = DecodedPacket("Meshtastic", "?", sender=_node_id(sender))
    hops_taken = hop_start - hop_limit if hop_start else None
    pkt.hops = f"{hops_taken} hop(s)" if hops_taken is not None else f"limit {hop_limit}"
    to = "all" if dest == _BROADCAST else _node_id(dest)
    encrypted = raw[16:]

    if chan_hash != meshtastic_channel_hash(channel_name, key):
        pkt.kind = "ENCRYPTED"
        pkt.text = f"to {to}, channel hash {chan_hash:02x}" + (" (PKI DM)" if chan_hash == 0 else "")
        return pkt

    nonce = struct.pack("<QI", pkt_id, sender) + b"\x00" * 4
    plain = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=nonce).decrypt(encrypted)
    try:
        data = _pb_dict(plain)
        portnum = data.get(1, 0)
        payload = data.get(2, b"")
        pkt.kind = _PORTNUMS.get(portnum, f"PORT_{portnum}")
        _decode_mt_payload(portnum, payload, pkt)
    except (IndexError, ValueError, struct.error, TypeError):
        pkt.kind = "UNDECODABLE"
        pkt.text = f"to {to} (decrypt failed / not default key)"
        return pkt
    if dest != _BROADCAST:
        pkt.text = f"[to {to}] {pkt.text}"
    return pkt


def _decode_mt_payload(portnum, payload, pkt):
    if portnum == 1:
        pkt.text = payload.decode("utf-8", "replace")
    elif portnum == 3:
        pos = _pb_dict(payload)
        lat_raw, lon_raw = pos.get(1), pos.get(2)
        if isinstance(lat_raw, (bytes, bytearray)) and isinstance(lon_raw, (bytes, bytearray)):
            lat = struct.unpack("<i", lat_raw)[0] / 1e7
            lon = struct.unpack("<i", lon_raw)[0] / 1e7
            pkt.lat, pkt.lon = lat, lon
            alt = pos.get(3)
            pkt.text = f"{lat:.5f}, {lon:.5f}" + (f" alt {alt} m" if isinstance(alt, int) else "")
        else:
            pkt.text = "(no position)"
    elif portnum == 4:
        user = _pb_dict(payload)
        long_name = user.get(2, b"").decode("utf-8", "replace")
        short_name = user.get(3, b"").decode("utf-8", "replace")
        pkt.text = f"{long_name} ({short_name})" if short_name else long_name
        pkt.node = {"id": user.get(1, b"").decode("utf-8", "replace"), "long_name": long_name, "short_name": short_name}
    elif portnum == 67:
        tele = _pb_dict(payload)
        parts = []
        if isinstance(tele.get(2), (bytes, bytearray)):
            dm = _pb_dict(tele[2])
            if isinstance(dm.get(1), int):
                parts.append(f"batt {dm[1]}%")
            if (v := _f32(dm.get(2))) is not None:
                parts.append(f"{v:.2f} V")
            if (v := _f32(dm.get(3))) is not None:
                parts.append(f"ch util {v:.1f}%")
        if isinstance(tele.get(3), (bytes, bytearray)):
            em = _pb_dict(tele[3])
            if (v := _f32(em.get(1))) is not None:
                parts.append(f"{v:.1f} °C")
            if (v := _f32(em.get(2))) is not None:
                parts.append(f"{v:.0f}% RH")
        pkt.text = ", ".join(parts) or "telemetry"
    else:
        pkt.text = f"{len(payload)} B"

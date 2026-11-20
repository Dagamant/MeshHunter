"""Decoders for raw LoRa packets captured by the MeshHunter sniffer.

Everything here is pure (bytes in, dict out) so it can be unit-tested and
re-run over the raw packet log later with better keys or newer decoders.

MeshCore
    Header, route, path and payload type are always cleartext. ADVERTs are
    signed but not encrypted -- public key, name, type and location are all
    readable (and the signature is checked, so a corrupted packet that
    slipped past the LoRa CRC can't create a bogus node). Group text on a
    channel whose key we know (the default Public channel, hashtag
    channels subscribed in meshcore_channels; see core/channels.py) is decrypted.
    Direct messages only reveal 1-byte source/destination hashes.

Meshtastic
    The 16-byte header (to, from, packet id, hop info, channel hash, relay
    node) is cleartext. The payload is AES-CTR encrypted with the channel
    key; every modem preset's default channel uses the well-known default
    key, so on default channels we get NodeInfo (names, hardware, role,
    public key), Position, NeighborInfo, MapReport and public text.
    Private channels and PKI direct messages only give the header.
"""

import base64
import hashlib
import struct

from Crypto.Cipher import AES

from .channels import PUBLIC_CHANNEL_KEY, decrypt_group_text, make_channel, subscribed_channels

# --------------------------------------------------------------------------
# MeshCore
# --------------------------------------------------------------------------

MC_ROUTE_NAMES = ["TC_FLOOD", "FLOOD", "DIRECT", "TC_DIRECT"]
MC_PAYLOAD_NAMES = {
    0x00: "REQ", 0x01: "RESPONSE", 0x02: "TEXT_MSG", 0x03: "ACK", 0x04: "ADVERT",
    0x05: "GRP_TXT", 0x06: "GRP_DATA", 0x07: "ANON_REQ", 0x08: "PATH", 0x09: "TRACE",
    0x0A: "MULTIPART", 0x0B: "CONTROL", 0x0F: "RAW_CUSTOM",
}
MC_PUBLIC_CHANNEL_KEY = PUBLIC_CHANNEL_KEY


def meshcore_channel(name, key=None):
    """A MeshCore group channel (see core/channels.py)."""
    return make_channel(name, key)


def default_meshcore_channels(config=None):
    """Public plus the subscribed channels in config's meshcore_channels."""
    return subscribed_channels(config)


def _verify_ed25519(pubkey, message, signature):
    try:
        from Crypto.Signature import eddsa

        key = eddsa.import_public_key(pubkey)
        eddsa.new(key, "rfc8032").verify(message, signature)
        return True
    except (ValueError, TypeError):
        return False


def decode_meshcore(raw, channels=()):
    """Decode one MeshCore over-the-air packet. Never raises; anything
    unparseable comes back with "error" set."""
    d = {"proto": "meshcore"}
    try:
        buf = memoryview(raw)
        header = buf[0]
        route_type = header & 0x03
        payload_type = (header >> 2) & 0x0F
        d["route_type"] = route_type
        d["route_typename"] = MC_ROUTE_NAMES[route_type]
        d["payload_type"] = payload_type
        d["payload_typename"] = MC_PAYLOAD_NAMES.get(payload_type, f"UNK{payload_type}")
        d["payload_ver"] = header >> 6
        pos = 1
        if route_type in (0, 3):
            d["transport_codes"] = bytes(buf[pos:pos + 4]).hex()
            pos += 4
        path_byte = buf[pos]
        pos += 1
        hash_size = (path_byte >> 6) + 1
        hops = path_byte & 0x3F
        path = bytes(buf[pos:pos + hops * hash_size])
        if len(path) != hops * hash_size:
            raise ValueError("truncated path")
        pos += len(path)
        d["path_len"] = hops
        d["path_hash_size"] = hash_size
        d["path"] = path.hex()
        d["path_hops"] = [path[i:i + hash_size].hex() for i in range(0, len(path), hash_size)]
        payload = bytes(buf[pos:])
        # Same hash the meshcore library puts on RX_LOG_DATA: identical for
        # every relayed copy of a packet, so repeats can be grouped.
        d["pkt_hash"] = int.from_bytes(hashlib.sha256(payload).digest()[:4], "little")

        if payload_type == 0x04:
            _decode_mc_advert(payload, d)
        elif payload_type == 0x05:
            _decode_mc_grp_txt(payload, channels, d)
        elif payload_type in (0x00, 0x01, 0x02, 0x08) and len(payload) >= 2:
            d["dest_hash"] = payload[0:1].hex()
            d["src_hash"] = payload[1:2].hex()
        elif payload_type == 0x07 and len(payload) >= 33:
            d["dest_hash"] = payload[0:1].hex()
            d["sender_key"] = payload[1:33].hex()
    except (IndexError, ValueError) as exc:
        d["error"] = str(exc) or type(exc).__name__
    return d


def _decode_mc_advert(payload, d):
    if len(payload) < 101:
        raise ValueError("short advert")
    pubkey, ts, sig, app = payload[:32], payload[32:36], payload[36:100], payload[100:]
    d["adv_key"] = pubkey.hex()
    d["adv_timestamp"] = struct.unpack("<I", ts)[0]
    d["sig_ok"] = _verify_ed25519(pubkey, pubkey + ts + app, sig)
    flags = app[0]
    d["adv_flags"] = flags
    d["adv_type"] = flags & 0x0F
    pos = 1
    if flags & 0x10:
        lat, lon = struct.unpack("<ii", app[pos:pos + 8])
        d["adv_lat"] = lat / 1e6
        d["adv_lon"] = lon / 1e6
        pos += 8
    if flags & 0x20:
        pos += 2
    if flags & 0x40:
        pos += 2
    if flags & 0x80:
        d["adv_name"] = app[pos:].decode("utf-8", "replace").strip("\x00")


def _decode_mc_grp_txt(payload, channels, d):
    if len(payload) < 3:
        raise ValueError("short group packet")
    d["chan_hash"] = f"{payload[0]:02x}"
    found = decrypt_group_text(payload, channels)
    if found:
        d.update(found)
    else:
        # Keep the encrypted group payload (hash + MAC + ciphertext) so a
        # later dictionary attack (core/channel_crack.py) can try candidate
        # channel names against this exact packet without re-reading the log.
        d["grp_cipher"] = payload.hex()


# --------------------------------------------------------------------------
# Meshtastic
# --------------------------------------------------------------------------

MT_DEFAULT_PSK = bytes([0xD4, 0xF1, 0xBB, 0x3A, 0x20, 0x29, 0x07, 0x59,
                        0xF0, 0xBC, 0xFF, 0xAB, 0xCF, 0x4E, 0x69, 0x01])
MT_BROADCAST = 0xFFFFFFFF

MT_PORTS = {
    0: "UNKNOWN", 1: "TEXT", 2: "REMOTE_HW", 3: "POSITION", 4: "NODEINFO",
    5: "ROUTING", 6: "ADMIN", 7: "TEXT_COMPRESSED", 8: "WAYPOINT", 9: "AUDIO",
    10: "DETECTION_SENSOR", 32: "REPLY", 33: "IP_TUNNEL", 34: "PAXCOUNTER",
    64: "SERIAL", 65: "STORE_FORWARD", 66: "RANGE_TEST", 67: "TELEMETRY",
    68: "ZPS", 69: "SIMULATOR", 70: "TRACEROUTE", 71: "NEIGHBORINFO",
    72: "ATAK_PLUGIN", 73: "MAP_REPORT", 74: "POWERSTRESS",
}
MT_ROLES = {
    0: "CLIENT", 1: "CLIENT_MUTE", 2: "ROUTER", 3: "ROUTER_CLIENT", 4: "REPEATER",
    5: "TRACKER", 6: "SENSOR", 7: "TAK", 8: "CLIENT_HIDDEN", 9: "LOST_AND_FOUND",
    10: "TAK_TRACKER", 11: "ROUTER_LATE", 12: "CLIENT_BASE",
}


def expand_meshtastic_psk(psk):
    """Channel PSK bytes as stored in a Meshtastic channel config -> AES key
    (or b"" for an unencrypted channel). A 1-byte PSK is an index into the
    well-known default key: 1 = default, 2..10 = default with last byte+n-1."""
    if len(psk) == 0:
        return b""
    if len(psk) == 1:
        idx = psk[0]
        if idx == 0:
            return b""
        key = bytearray(MT_DEFAULT_PSK)
        key[-1] = (key[-1] + idx - 1) & 0xFF
        return bytes(key)
    if len(psk) < 16:
        return psk.ljust(16, b"\x00")
    if 16 < len(psk) < 32:
        return psk.ljust(32, b"\x00")
    return psk


def _xor_hash(data):
    h = 0
    for b in data:
        h ^= b
    return h


def meshtastic_channel(name, psk_b64="AQ=="):
    key = expand_meshtastic_psk(base64.b64decode(psk_b64))
    return {"name": name, "key": key, "hash": _xor_hash(name.encode()) ^ _xor_hash(key)}


def default_meshtastic_channels(preset_names, config=None):
    """Every modem preset's default primary channel (named after the
    preset, default key), plus any from config's sniffer_meshtastic_channels
    ([{"name": "...", "psk": "<base64>"}])."""
    channels = [meshtastic_channel(n) for n in preset_names]
    for entry in (config or {}).get("sniffer_meshtastic_channels") or []:
        try:
            channels.append(meshtastic_channel(entry["name"], entry.get("psk", "AQ==")))
        except (KeyError, TypeError, ValueError):
            continue
    return channels


def mt_node_id(num):
    return f"!{num:08x}"


# -- minimal protobuf wire-format reader -----------------------------------

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


def pb_fields(buf):
    """Parse a protobuf message into {field_number: [values...]}. LEN
    fields come back as bytes, fixed32/64 as raw little-endian ints.
    Raises ValueError on anything malformed -- which is also how a wrong
    decryption key is detected."""
    fields = {}
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = _varint(buf, pos)
        field, wire = key >> 3, key & 7
        if field == 0:
            raise ValueError("field 0")
        if wire == 0:
            val, pos = _varint(buf, pos)
        elif wire == 1:
            if pos + 8 > n:
                raise ValueError("truncated fixed64")
            val = struct.unpack_from("<Q", buf, pos)[0]
            pos += 8
        elif wire == 2:
            ln, pos = _varint(buf, pos)
            if pos + ln > n:
                raise ValueError("truncated bytes")
            val = bytes(buf[pos:pos + ln])
            pos += ln
        elif wire == 5:
            if pos + 4 > n:
                raise ValueError("truncated fixed32")
            val = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
        else:
            raise ValueError(f"wire type {wire}")
        fields.setdefault(field, []).append(val)
    return fields


def _first(fields, num, default=None):
    vals = fields.get(num)
    return vals[0] if vals else default


def _str(fields, num):
    v = _first(fields, num)
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else None


def _sfixed32(v):
    return struct.unpack("<i", struct.pack("<I", v))[0] if v is not None else None


def _int32(v):
    if v is None:
        return None
    v &= 0xFFFFFFFFFFFFFFFF
    return v - (1 << 64) if v >= 1 << 63 else v


def _coords(fields, lat_field, lon_field):
    lat = _sfixed32(_first(fields, lat_field))
    lon = _sfixed32(_first(fields, lon_field))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    return lat / 1e7, lon / 1e7


def decode_meshtastic(raw, channels=()):
    """Decode one Meshtastic over-the-air packet. Never raises."""
    d = {"proto": "meshtastic"}
    if len(raw) < 16:
        d["error"] = "short packet"
        return d
    dest, sender, pkt_id, flags, chan_hash, next_hop, relay = struct.unpack_from("<IIIBBBB", raw, 0)
    d.update({
        "to": mt_node_id(dest) if dest != MT_BROADCAST else "^all",
        "from": mt_node_id(sender),
        "from_num": sender,
        "id": pkt_id,
        "hop_limit": flags & 0x07,
        "want_ack": bool(flags & 0x08),
        "via_mqtt": bool(flags & 0x10),
        "hop_start": flags >> 5,
        "chan_hash": chan_hash,
        "next_hop": next_hop,
        "relay_node": relay,
    })
    if d["hop_start"]:
        d["hops_away"] = d["hop_start"] - d["hop_limit"]
    payload = raw[16:]
    nonce = struct.pack("<QI", pkt_id, sender) + b"\x00" * 4

    for chan in channels:
        if chan["hash"] != chan_hash:
            continue
        if chan["key"]:
            plain = AES.new(chan["key"], AES.MODE_CTR, nonce=b"", initial_value=nonce).decrypt(payload)
        else:
            plain = payload
        try:
            data = pb_fields(plain)
            port = _first(data, 1)
            if port is None or port not in MT_PORTS or not isinstance(_first(data, 2, b""), bytes):
                raise ValueError("not a Data message")
        except (ValueError, IndexError):
            continue
        d["channel"] = chan["name"]
        d["port"] = port
        d["portname"] = MT_PORTS[port]
        try:
            _decode_mt_app(port, _first(data, 2, b""), d)
        except (ValueError, IndexError, struct.error) as exc:
            d["app_error"] = str(exc)
        return d

    d["encrypted"] = True
    if chan_hash == 0 and dest != MT_BROADCAST:
        d["pki"] = True  # public-key encrypted direct message
    return d


def _decode_mt_app(port, payload, d):
    if port == 1:
        d["text"] = payload.decode("utf-8", "replace")
    elif port == 3:
        f = pb_fields(payload)
        pos = _coords(f, 1, 2)
        if pos:
            d["lat"], d["lon"] = pos
        alt = _int32(_first(f, 3))
        if alt is not None:
            d["alt"] = alt
    elif port == 4:
        f = pb_fields(payload)
        d["user_id"] = _str(f, 1)
        d["long_name"] = _str(f, 2)
        d["short_name"] = _str(f, 3)
        d["hw_model"] = _first(f, 5, 0)
        d["role"] = MT_ROLES.get(_first(f, 7, 0), str(_first(f, 7, 0)))
        pk = _first(f, 8)
        if pk:
            d["public_key"] = pk.hex()
    elif port == 71:
        f = pb_fields(payload)
        neighbors = []
        for raw_n in f.get(4, []):
            nf = pb_fields(raw_n)
            snr_raw = _first(nf, 2)
            snr = struct.unpack("<f", struct.pack("<I", snr_raw))[0] if snr_raw is not None else None
            neighbors.append({"id": mt_node_id(_first(nf, 1, 0)), "snr": snr})
        d["neighbors"] = neighbors
    elif port == 73:
        f = pb_fields(payload)
        d["long_name"] = _str(f, 1)
        d["short_name"] = _str(f, 2)
        d["role"] = MT_ROLES.get(_first(f, 3, 0), str(_first(f, 3, 0)))
        d["hw_model"] = _first(f, 4, 0)
        d["firmware_version"] = _str(f, 5)
        pos = _coords(f, 9, 10)
        if pos:
            d["lat"], d["lon"] = pos
    elif port == 70:
        f = pb_fields(payload)
        route = []
        for v in f.get(1, []):
            if isinstance(v, bytes):  # packed repeated fixed32
                route.extend(struct.unpack(f"<{len(v) // 4}I", v[: len(v) // 4 * 4]))
            else:
                route.append(v)
        d["route"] = [mt_node_id(n) for n in route]


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------

def format_meshtastic(d):
    parts = [f"from={d.get('from')}", f"to={d.get('to')}"]
    if "hops_away" in d:
        parts.append(f"hops={d['hops_away']}")
    if d.get("relay_node"):
        parts.append(f"relay=..{d['relay_node']:02x}")
    if d.get("encrypted"):
        parts.append("(pki-dm)" if d.get("pki") else f"chan_hash={d['chan_hash']} (encrypted)")
        return " ".join(parts)
    parts.append(f"chan={d.get('channel')!r} port={d.get('portname')}")
    if d.get("long_name"):
        parts.append(f"name={d['long_name']!r}/{d.get('short_name')!r} role={d.get('role')} hw={d.get('hw_model')}")
    if d.get("lat") is not None:
        parts.append(f"loc=({d['lat']:.5f},{d['lon']:.5f})")
    if d.get("text") is not None:
        parts.append(f"msg={d['text']!r}")
    if d.get("neighbors") is not None:
        parts.append("neighbors=" + ",".join(n["id"] for n in d["neighbors"]))
    if d.get("route") is not None:
        parts.append("route=" + ",".join(d["route"]))
    return " ".join(parts)

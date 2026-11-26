"""Builders for synthetic over-the-air packets, used by the decoder tests
and by make_golden.py.

Everything is deterministic (fixed keys, timestamps and packet ids) so a
golden capture built from these is the same on every run.
"""

import hashlib
import struct

from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA256
from Crypto.Signature import eddsa

from meshhunter.core.channels import PUBLIC_CHANNEL_KEY
from meshhunter.core.sniffer_decode import MT_DEFAULT_PSK, _xor_hash

# --------------------------------------------------------------------------
# MeshCore
# --------------------------------------------------------------------------

ROUTE_FLOOD = 1
ROUTE_DIRECT = 2
ROUTE_TC_FLOOD = 0

ADV_TYPE_CHAT, ADV_TYPE_REPEATER, ADV_TYPE_ROOM, ADV_TYPE_SENSOR = 1, 2, 3, 4


def ed25519_key(seed_text):
    """A throwaway Ed25519 key, derived from text so tests are repeatable."""
    return eddsa.import_private_key(hashlib.sha256(seed_text.encode()).digest())


def mc_header(route_type, payload_type, version=0):
    return bytes([(route_type & 0x03) | ((payload_type & 0x0F) << 2) | ((version & 0x03) << 6)])


def mc_packet(route_type, payload_type, payload, path=b"", hash_size=1, transport=b"\x00\x00\x00\x00"):
    out = bytearray(mc_header(route_type, payload_type))
    if route_type in (0, 3):
        out += transport
    hops = len(path) // hash_size
    out.append(((hash_size - 1) << 6) | hops)
    out += path
    out += payload
    return bytes(out)


def mc_advert_payload(key, adv_type=ADV_TYPE_REPEATER, lat=None, lon=None, name=None, timestamp=1794200000,
                      corrupt_signature=False):
    pub = key.public_key().export_key(format="raw")
    flags = adv_type & 0x0F
    app = bytearray()
    if lat is not None:
        flags |= 0x10
        app += struct.pack("<ii", round(lat * 1e6), round(lon * 1e6))
    if name is not None:
        flags |= 0x80
        app += name.encode("utf-8")
    app = bytes([flags]) + bytes(app)
    ts = struct.pack("<I", timestamp)
    sig = eddsa.new(key, "rfc8032").sign(pub + ts + app)
    if corrupt_signature:
        sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
    return pub + ts + sig + app


def mc_advert(key, path=b"", route_type=ROUTE_FLOOD, **kwargs):
    return mc_packet(route_type, 0x04, mc_advert_payload(key, **kwargs), path=path)


def mc_grp_txt_payload(text, key=PUBLIC_CHANNEL_KEY, timestamp=1794200000):
    plain = struct.pack("<I", timestamp) + b"\x00" + text.encode("utf-8")
    plain += b"\x00" * (-len(plain) % 16)
    cipher = AES.new(key, AES.MODE_ECB).encrypt(plain)
    mac = HMAC.new(key + bytes(16), digestmod=SHA256).update(cipher).digest()[:2]
    chan_hash = hashlib.sha256(key).digest()[0]
    return bytes([chan_hash]) + mac + cipher


def mc_grp_txt(text, key=PUBLIC_CHANNEL_KEY, path=b"", **kwargs):
    return mc_packet(ROUTE_FLOOD, 0x05, mc_grp_txt_payload(text, key, **kwargs), path=path)


# --------------------------------------------------------------------------
# Meshtastic
# --------------------------------------------------------------------------

MT_BROADCAST = 0xFFFFFFFF


def pb_varint(value):
    value &= 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb_field(num, value):
    """Encode one field: int -> varint, bytes/str -> length-delimited."""
    if isinstance(value, str):
        value = value.encode("utf-8")
    if isinstance(value, (bytes, bytearray)):
        return pb_varint(num << 3 | 2) + pb_varint(len(value)) + bytes(value)
    return pb_varint(num << 3 | 0) + pb_varint(value)


def pb_sfixed32(num, value):
    return pb_varint(num << 3 | 5) + struct.pack("<i", value)


def mt_channel_hash(name, key=MT_DEFAULT_PSK):
    return _xor_hash(name.encode()) ^ _xor_hash(key)


def mt_packet(port, app_payload, *, sender=0x1234ABCD, dest=MT_BROADCAST, pkt_id=0x0BADF00D,
              hop_limit=3, hop_start=3, channel="LongFast", key=MT_DEFAULT_PSK, chan_hash=None, relay=0):
    data = pb_field(1, port) + pb_field(2, app_payload)
    nonce = struct.pack("<QI", pkt_id, sender) + b"\x00" * 4
    cipher = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=nonce).encrypt(data) if key else data
    if chan_hash is None:
        chan_hash = mt_channel_hash(channel, key)
    flags = (hop_limit & 0x07) | ((hop_start & 0x07) << 5)
    header = struct.pack("<IIIBBBB", dest, sender, pkt_id, flags, chan_hash, 0, relay)
    return header + cipher


def mt_text(text, **kwargs):
    return mt_packet(1, text.encode("utf-8"), **kwargs)


def mt_position(lat, lon, alt=None, **kwargs):
    body = pb_sfixed32(1, round(lat * 1e7)) + pb_sfixed32(2, round(lon * 1e7))
    if alt is not None:
        body += pb_field(3, alt)
    return mt_packet(3, body, **kwargs)


def mt_nodeinfo(user_id, long_name, short_name, hw_model=9, role=2, public_key=None, **kwargs):
    body = pb_field(1, user_id) + pb_field(2, long_name) + pb_field(3, short_name) + pb_field(5, hw_model)
    body += pb_field(7, role)
    if public_key:
        body += pb_field(8, public_key)
    return mt_packet(4, body, **kwargs)

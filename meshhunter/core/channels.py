"""MeshCore channel subscriptions: which group channels the app listens to.

A MeshCore group message (GRP_TXT) is AES-encrypted with its channel's
16-byte key, and carries a 1-byte hash of that key plus a 2-byte MAC so a
receiver can tell which key fits. "#hashtag" channels derive their key
from the name itself (sha256("#name")[:16]), so subscribing to one only
needs its name; any other channel needs its key.

The app decrypts subscribed channels itself, for both the LoRa sniffer and
a companion's RX log, so listening never uses up (or changes) a companion
device's own channel slots.

Config: "meshcore_channels" is a list of
    {"name": "#test"}                         hashtag channel, key from name
    {"name": "Club", "key": "<32 hex chars or base64>"}
    ... optionally with "enabled": false to keep it listed but not decrypt.
The Public channel (well-known key) is always on and isn't listed.
"""

import base64
import hashlib
import struct

from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA256

PUBLIC_CHANNEL_NAME = "Public"
PUBLIC_CHANNEL_KEY = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")

# Widely used hashtag channels, subscribed to out of the box.
DEFAULT_CHANNEL_NAMES = ("#test", "#testing", "#bot", "#hamradio", "#emergency")


def default_channel_entries():
    return [{"name": n, "enabled": True} for n in DEFAULT_CHANNEL_NAMES]


def parse_key(key):
    """Hex (32 chars) or base64 text -> 16 key bytes. Raises ValueError."""
    if isinstance(key, (bytes, bytearray)):
        raw = bytes(key)
    else:
        text = str(key).strip().replace(" ", "")
        if len(text) == 32:
            try:
                raw = bytes.fromhex(text)
            except ValueError:
                raw = None
        else:
            raw = None
        if raw is None:
            try:
                raw = base64.b64decode(text, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("key must be 32 hex characters or base64") from exc
    if len(raw) != 16:
        raise ValueError("key must be 16 bytes (32 hex characters)")
    return raw


def normalize_name(name):
    return (name or "").strip()


def make_channel(name, key=None):
    """A decryptable channel: {"name", "key": 16 bytes, "hash": int}.
    Raises ValueError for a non-hashtag channel without a key, or a bad key."""
    name = normalize_name(name)
    if not name:
        raise ValueError("channel needs a name")
    if key:
        key = parse_key(key)
    else:
        if not name.startswith("#") or len(name) < 2:
            raise ValueError(f"{name!r} isn't a #hashtag channel, so it needs a key")
        key = hashlib.sha256(name.encode("utf-8")).digest()[:16]
    return {"name": name, "key": key, "hash": hashlib.sha256(key).digest()[0]}


def parse_entry_text(text):
    """User input "#name" or "Name <key>" -> a config entry dict.
    Raises ValueError with a message fit for the UI."""
    parts = (text or "").split()
    if not parts:
        raise ValueError("Enter a channel name, e.g. #hamradio")
    name = parts[0]
    key = "".join(parts[1:]) or None
    if not key and not name.startswith("#"):
        name = "#" + name  # "hamradio" means the hashtag channel
    make_channel(name, key)  # validate
    entry = {"name": name, "enabled": True}
    if key:
        entry["key"] = key
    return entry


def clean_entries(entries):
    """Validated, de-duplicated config entries (bad ones dropped)."""
    out, seen = [], set()
    for entry in entries or []:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            continue
        name = normalize_name(entry.get("name"))
        key = entry.get("key") or None
        try:
            make_channel(name, key)
        except (ValueError, TypeError):
            continue
        if name.lower() in seen or name.lower() == PUBLIC_CHANNEL_NAME.lower():
            continue
        seen.add(name.lower())
        clean = {"name": name, "enabled": bool(entry.get("enabled", True))}
        if key:
            clean["key"] = key
        out.append(clean)
    return out


def config_channels(data):
    """The meshcore_channels value for a loaded config. A config file from
    before channel subscriptions existed gets the defaults plus anything
    in the older sniffer_meshcore_channels list."""
    if isinstance(data.get("meshcore_channels"), list):
        return clean_entries(data["meshcore_channels"])
    return clean_entries(default_channel_entries() + list(data.get("sniffer_meshcore_channels") or []))


def subscribed_channels(config=None):
    """Public plus every enabled subscription, ready for decrypt_group_text."""
    channels = [make_channel(PUBLIC_CHANNEL_NAME, PUBLIC_CHANNEL_KEY)]
    config = config or {}
    entries = config.get("meshcore_channels")
    if entries is None:
        entries = config_channels(config)
    for entry in clean_entries(entries):
        if entry["enabled"]:
            channels.append(make_channel(entry["name"], entry.get("key")))
    return channels


def decrypt_group_text(payload, channels):
    """Try to decrypt a GRP_TXT packet payload (chan hash, MAC, ciphertext).
    Returns {"chan_name", "sender_timestamp", "message"} or None."""
    if len(payload) < 3:
        return None
    chan_hash, mac, cipher = payload[0], payload[1:3], payload[3:]
    if not cipher or len(cipher) % 16:
        return None
    for chan in channels:
        if chan["hash"] != chan_hash:
            continue
        h = HMAC.new(chan["key"] + bytes(16), digestmod=SHA256)
        h.update(cipher)
        if h.digest()[:2] != mac:
            continue
        plain = AES.new(chan["key"], AES.MODE_ECB).decrypt(cipher)
        return {
            "chan_name": chan["name"],
            "sender_timestamp": struct.unpack("<I", plain[:4])[0],
            "message": plain[5:].rstrip(b"\x00").decode("utf-8", "replace"),
        }
    return None

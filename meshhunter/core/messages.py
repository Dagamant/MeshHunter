"""Turn decoded packets into rows for the Messages panel.

Every packet the app decodes, from either the sniffer or a MeshCore
companion's RX log, becomes one flat dict, whether or not it's something
the node logger records (no GPS, bad signature, encrypted, chat text...):

    mesh      "meshcore" | "meshtastic"
    kind      "text" | "dm" | "advert" | "position" | "node" | "encrypted" | "other"
    channel   channel name, or a hint like "hash 3f" when it's unknown
    sender    best display name we have
    to        recipient for direct packets ("" for broadcasts)
    text      the message body, or a one-line summary for non-text packets
    rssi/snr/hops, source ("sniffer" | "companion"), profile (sniffer only)
    key       identifies one transmission, so relayed copies can be grouped

and, for the packet inspector, where known: t_ms (time with milliseconds),
raw_hex, route, ptype, path (hex) / path_hops, chan_hash, payload_len,
decrypted, profile_label. MessageLog adds heard + copies (one entry per
relayed copy folded into the row).

Framework-agnostic: the Qt panel only renders what's here.
"""

import time
from datetime import datetime

from .constants import ADV_TYPE_NAMES

TEXT_KINDS = ("text", "dm")
MESH_LABELS = {"meshcore": "MeshCore", "meshtastic": "Meshtastic"}


def _base(mesh, source, rssi=None, snr=None, profile=None):
    return {
        "time": time.strftime("%H:%M:%S"),
        "t_ms": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "mesh": mesh,
        "source": source,
        "profile": profile or "",
        "rssi": rssi,
        "snr": snr,
        "hops": None,
        "kind": "other",
        "channel": "",
        "sender": "",
        "to": "",
        "text": "",
        "key": None,
        "raw_hex": "",
        "route": "",
        "ptype": "",
        "path": "",
        "path_hops": [],
        "chan_hash": None,
        "payload_len": None,
        "decrypted": False,
        "profile_label": "",
    }


def meshcore_message(d, source, rssi=None, snr=None, profile=None, raw=None):
    """d: decode_meshcore() output or a companion RX_LOG_DATA payload
    (same keys). raw: the over-the-air bytes, when the caller has them
    (a companion payload carries its own raw_hex)."""
    m = _base("meshcore", source, d.get("rssi", rssi), d.get("snr", snr), profile)
    if d.get("pkt_hash") is not None:
        m["key"] = ("mc", d["pkt_hash"])
    m["hops"] = d.get("path_len")
    ptype = d.get("payload_typename", "?")
    m["raw_hex"] = raw.hex() if isinstance(raw, (bytes, bytearray)) else (d.get("raw_hex") or "")
    m["route"] = d.get("route_typename") or ""
    m["ptype"] = ptype
    m["path"] = d.get("path") or ""
    hops = d.get("path_hops")
    if hops is None and m["path"]:
        size = max(1, int(d.get("path_hash_size") or 1)) * 2
        hops = [m["path"][i:i + size] for i in range(0, len(m["path"]), size)]
    m["path_hops"] = list(hops or [])
    m["chan_hash"] = d.get("chan_hash")
    payload = d.get("pkt_payload")
    if isinstance(payload, (bytes, bytearray)):
        m["payload_len"] = len(payload)
    elif d.get("grp_cipher"):
        m["payload_len"] = len(d["grp_cipher"]) // 2

    if ptype == "ADVERT":
        m["kind"] = "advert"
        key = d.get("adv_key") or ""
        m["sender"] = d.get("adv_name") or (key[:8] if key else "?")
        parts = [f"Advert · {ADV_TYPE_NAMES.get(d.get('adv_type'), d.get('adv_type', '?'))}"]
        if d.get("adv_lat") or d.get("adv_lon"):
            parts.append(f"{d['adv_lat']:.5f}, {d['adv_lon']:.5f}")
        else:
            parts.append("no location")
        if d.get("sig_ok") is False:
            parts.append("BAD SIGNATURE")
        m["text"] = " · ".join(parts)
    elif ptype == "GRP_TXT":
        if d.get("message") is not None:
            m["kind"] = "text"
            m["decrypted"] = True
            m["channel"] = d.get("chan_name") or "?"
            # Group text carries the sender inside the body: "name: message".
            name, sep, body = d["message"].partition(": ")
            if sep and name and len(name) <= 32:
                m["sender"], m["text"] = name, body
            else:
                m["text"] = d["message"]
        else:
            m["kind"] = "encrypted"
            m["channel"] = d.get("chan_name") or f"hash {d.get('chan_hash', '?')}"
            m["text"] = "Group message (no key for this channel)"
            # The raw encrypted group payload, kept so "Find channels" can
            # dictionary-attack it (core/channel_crack.py). From the sniffer
            # decoder (grp_cipher) or a companion RX log (pkt_payload bytes).
            cipher = d.get("grp_cipher")
            if cipher is None and isinstance(d.get("pkt_payload"), (bytes, bytearray)):
                cipher = bytes(d["pkt_payload"]).hex()
            m["grp_cipher"] = cipher
    elif ptype == "TEXT_MSG":
        m["kind"] = "encrypted"
        m["sender"] = d.get("src_hash", "")
        m["to"] = d.get("dest_hash", "")
        m["text"] = "Direct message (end-to-end encrypted)"
    else:
        m["sender"] = d.get("src_hash", "")
        m["to"] = d.get("dest_hash", "")
        m["text"] = f"{ptype} · {d.get('route_typename', '?').lower()}"
    if d.get("error"):
        m["text"] += f" · decode error: {d['error']}"
    return m


def companion_dm_message(payload, sender_name):
    """A CONTACT_MSG_RECV from a companion: a direct message to us, already
    decrypted by the device."""
    m = _base("meshcore", "companion", snr=payload.get("SNR") or payload.get("snr"))
    m["kind"] = "dm"
    m["sender"] = sender_name
    m["to"] = "me"
    m["text"] = payload.get("text") or ""
    m["hops"] = payload.get("path_len") if payload.get("path_len") not in (None, 255) else None
    return m


def meshtastic_message(d, rssi=None, snr=None, profile=None, names=None):
    """d: decode_meshtastic() output. names: optional {"!abcd1234": long_name}
    for showing who sent a text instead of a bare node id."""
    names = dict(names or {})
    m = _base("meshtastic", "sniffer", rssi, snr, profile)
    sender = d.get("from", "?")
    if d.get("long_name"):  # node info / map report name themselves
        names.setdefault(sender, d["long_name"])
    m["key"] = ("mt", d.get("from_num"), d.get("id"))
    m["hops"] = d.get("hops_away")
    m["ptype"] = d.get("portname") or ("ENCRYPTED" if d.get("encrypted") else "")
    m["chan_hash"] = d.get("chan_hash")
    m["decrypted"] = not d.get("encrypted")
    m["sender"] = _mt_name(sender, names)
    to = d.get("to", "")
    m["to"] = "" if to == "^all" else _mt_name(to, names)

    if d.get("encrypted"):
        m["kind"] = "encrypted"
        if d.get("pki"):
            m["channel"] = "PKI"
            m["text"] = "Direct message (public-key encrypted)"
        else:
            m["channel"] = f"hash {d.get('chan_hash')}"
            m["text"] = "Encrypted (no key for this channel)"
        return m

    m["channel"] = d.get("channel") or ""
    port = d.get("portname", "?")
    if d.get("text") is not None:
        m["kind"] = "dm" if m["to"] else "text"
        m["text"] = d["text"]
    elif port == "POSITION":
        m["kind"] = "position"
        if d.get("lat") is not None:
            alt = f" · {d['alt']} m" if d.get("alt") is not None else ""
            m["text"] = f"Position · {d['lat']:.5f}, {d['lon']:.5f}{alt}"
        else:
            m["text"] = "Position · no fix"
    elif port in ("NODEINFO", "MAP_REPORT"):
        m["kind"] = "node"
        label = "Node info" if port == "NODEINFO" else "Map report"
        bits = [label]
        if d.get("long_name"):
            bits.append(f"{d['long_name']} ({d.get('short_name') or '?'})")
        if d.get("role"):
            bits.append(d["role"].lower())
        if d.get("lat") is not None:
            bits.append(f"{d['lat']:.5f}, {d['lon']:.5f}")
        m["text"] = " · ".join(bits)
    elif port == "NEIGHBORINFO":
        neighbors = d.get("neighbors") or []
        m["text"] = f"Neighbors · {', '.join(_mt_name(n['id'], names) for n in neighbors) or 'none'}"
    elif port == "TRACEROUTE":
        route = d.get("route") or []
        m["text"] = f"Traceroute · {' → '.join(_mt_name(n, names) for n in route) or 'direct'}"
    else:
        m["text"] = port.replace("_", " ").title()
    if d.get("app_error"):
        m["text"] += f" · decode error: {d['app_error']}"
    return m


def _mt_name(node_id, names):
    name = names.get(node_id)
    return f"{name} ({node_id})" if name else node_id


def signal_text(m):
    parts = []
    if m.get("rssi") is not None:
        parts.append(f"{m['rssi']:.0f} dBm")
    if m.get("snr") is not None:
        parts.append(f"{m['snr']:.1f} dB")
    if m.get("hops") is not None:
        parts.append(f"{m['hops']} hop" + ("" if m["hops"] == 1 else "s"))
    if m.get("heard", 1) > 1:
        parts.append(f"×{m['heard']}")
    return "  ".join(parts)


def from_text(m):
    return f"{m['sender']} → {m['to']}" if m["to"] else m["sender"]


def channel_text(m):
    return "DM" if m["kind"] == "dm" else m["channel"]


def _copy(m):
    """One heard copy of a transmission, for the inspector's "Copies heard"."""
    return {"t": m.get("t_ms") or m.get("time", ""), "rssi": m.get("rssi"), "snr": m.get("snr"),
            "path_len": m.get("hops")}


class MessageLog:
    """Newest-first list of message rows. A flooded packet is usually heard
    several times (once per repeater in range); copies of the same
    transmission are folded into one row with a heard count, keeping the
    strongest signal and the fewest hops seen."""

    MAX_ROWS = 2000
    GROUP_WINDOW = 300  # how far back (rows) a relayed copy is matched to its original

    def __init__(self):
        self.rows = []

    def find(self, m):
        key = m.get("key")
        if key is None:
            return None
        for i, old in enumerate(self.rows[:self.GROUP_WINDOW]):
            if old.get("key") == key:
                return i
        return None

    def merge(self, index, new):
        old = self.rows[index]
        old["heard"] = old.get("heard", 1) + 1
        old.setdefault("copies", []).append(_copy(new))
        if new.get("rssi") is not None and (old.get("rssi") is None or new["rssi"] > old["rssi"]):
            old["rssi"], old["snr"] = new["rssi"], new.get("snr")
        if new.get("hops") is not None and (old.get("hops") is None or new["hops"] < old["hops"]):
            old["hops"] = new["hops"]

    def insert(self, m):
        m = dict(m)
        m["heard"] = 1
        m["copies"] = [_copy(m)]
        self.rows.insert(0, m)

    def overflow(self):
        return max(0, len(self.rows) - self.MAX_ROWS)

    def trim(self):
        del self.rows[self.MAX_ROWS:]

    def add(self, m):
        """Insert or fold in m. Returns True if it became a new row."""
        index = self.find(m)
        if index is not None:
            self.merge(index, m)
            return False
        self.insert(m)
        self.trim()
        return True

    def clear(self):
        self.rows = []


def matches(m, mesh=None, text_only=False, channel=None, text=None):
    """channel: only rows on that channel (as channel_text shows it, so
    "DM" picks direct messages), compared case-insensitively. text: only
    rows whose From or Message contains it (case-insensitive)."""
    if mesh and m["mesh"] != mesh:
        return False
    if channel and channel_text(m).lower() != channel.lower():
        return False
    if text_only and m["kind"] not in TEXT_KINDS:
        return False
    if text:
        needle = text.lower()
        if needle not in from_text(m).lower() and needle not in (m.get("text") or "").lower():
            return False
    return True


def channel_choices(rows, subscribed=()):
    """Channel filter options: Public, the subscribed channel names, then
    any other named channel seen in rows (hash hints left out)."""
    names, seen = [], set()
    for name in ["Public", *subscribed, *(channel_text(m) for m in rows)]:
        if not name or name == "?" or name.startswith("hash ") or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
    return names

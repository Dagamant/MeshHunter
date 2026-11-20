"""The TUI's Settings screen contents: the same settings the Qt dialog
edits, as a flat list of typed items the curses screen can draw and edit.

Item kinds:
  heading                       a section title (not selectable)
  bool    key                   toggled with Space/Enter
  choice  key, options          cycled with Left/Right/Enter
  text    key, secret           edited on the prompt line
  int     key, minimum          edited on the prompt line
  hop     profile key           one checkbox of sniffer_hop_profiles
  chan    channel name          one meshcore_channels subscription (Enter
                                pauses/resumes, x removes it)
  addchan                       prompt for a new channel to subscribe to
  defchan                       re-add any missing default channels

Channel items change the item list itself, so the screen rebuilds it
(build_items) after acting on one.
"""

from ..core.channels import default_channel_entries, parse_entry_text
from ..core.sniffer_profiles import DEFAULT_HOP_PROFILES, all_profiles


def build_items(config):
    profiles = all_profiles(config)
    profile_options = [(p.key, p.label()) for p in profiles]
    items = [
        {"kind": "heading", "label": "Uploads"},
        {"kind": "text", "key": "api_key", "label": "WDGWars API key", "secret": True},
        {"kind": "bool", "key": "auto_upload", "label": "Auto-upload new nodes to WDGWars"},
        {"kind": "bool", "key": "batch_uploads", "label": "Batch uploads (send pending with u)"},
        {"kind": "text", "key": "ingest_url", "label": "Ingest API URL"},
        {"kind": "text", "key": "ingest_api_key", "label": "Ingest API key", "secret": True},
        {"kind": "bool", "key": "ingest_auto_send", "label": "Auto-send new nodes to Ingest API"},
        {"kind": "heading", "label": "Companion"},
        {"kind": "bool", "key": "auto_clear_contacts", "label": "Auto-clear logged contacts from device"},
        {"kind": "heading", "label": "MeshCore channels (Public is always on)"},
    ]
    for entry in config.get("meshcore_channels") or []:
        label = entry["name"] + ("  (private key)" if entry.get("key") else "")
        items.append({"kind": "chan", "key": entry["name"], "label": label})
    items += [
        {"kind": "addchan", "label": "Add channel... (#name or Name <key>)"},
        {"kind": "defchan", "label": "Add default channels"},
        {"kind": "heading", "label": "GPS"},
        {"kind": "text", "key": "gps_port", "label": "GPS serial port"},
        {"kind": "bool", "key": "gps_enabled", "label": "GPS enabled"},
        {"kind": "heading", "label": "LoRa sniffer"},
        {"kind": "choice", "key": "sniffer_mode", "label": "Mode",
         "options": [("hop", "Hop between profiles"), ("fixed", "Fixed on one profile")]},
        {"kind": "choice", "key": "sniffer_fixed_profile", "label": "Fixed profile", "options": profile_options},
        {"kind": "heading", "label": "Hop profiles (split across radios when using All LoRa sniffers)"},
    ]
    for p in profiles:
        items.append({"kind": "hop", "key": p.key, "label": p.label()})
    items += [
        {"kind": "heading", "label": "Hop timing (ms)"},
        {"kind": "int", "key": "sniffer_dwell_ms", "label": "Dwell per profile (0 = auto)", "minimum": 0},
        {"kind": "int", "key": "sniffer_linger_ms", "label": "Linger after a packet", "minimum": 0},
        {"kind": "int", "key": "sniffer_partial_linger_ms", "label": "Linger after a partial", "minimum": 0},
        {"kind": "int", "key": "sniffer_linger_max_ms", "label": "Max linger", "minimum": 0},
    ]
    return items


def value_text(item, draft):
    kind = item["kind"]
    if kind == "bool":
        return "[x]" if draft.get(item["key"]) else "[ ]"
    if kind == "hop":
        return "[x]" if item["key"] in draft.get("sniffer_hop_profiles", []) else "[ ]"
    if kind == "chan":
        entry = _find_channel(draft, item["key"])
        return "[x]" if entry and entry.get("enabled", True) else "[ ]"
    if kind == "choice":
        value = draft.get(item["key"])
        for key, label in item["options"]:
            if key == value:
                return f"< {label} >"
        return f"< {value} >"
    if kind == "text":
        value = draft.get(item["key"], "") or ""
        if item.get("secret") and value:
            return "••••" + (value[-4:] if len(value) > 8 else "")
        return value or "(empty)"
    if kind == "int":
        value = draft.get(item["key"], 0)
        return "auto" if item["key"] == "sniffer_dwell_ms" and not value else str(value)
    return ""


def activate(item, draft, step=1):
    """Toggle/cycle item in draft. Returns False if it needs a text prompt."""
    kind, key = item["kind"], item.get("key")
    if kind == "bool":
        draft[key] = not draft.get(key)
    elif kind == "hop":
        hops = list(draft.get("sniffer_hop_profiles") or [])
        if key in hops:
            hops.remove(key)
        else:
            hops.append(key)
        draft["sniffer_hop_profiles"] = hops
    elif kind == "chan":
        entry = _find_channel(draft, key)
        if entry:
            entry["enabled"] = not entry.get("enabled", True)
    elif kind == "defchan":
        channels = draft.setdefault("meshcore_channels", [])
        names = {e["name"].lower() for e in channels}
        channels += [e for e in default_channel_entries() if e["name"].lower() not in names]
    elif kind == "choice":
        keys = [k for k, _ in item["options"]]
        current = keys.index(draft.get(key)) if draft.get(key) in keys else 0
        draft[key] = keys[(current + step) % len(keys)]
    else:
        return False
    return True


def _find_channel(draft, name):
    for entry in draft.get("meshcore_channels") or []:
        if entry["name"] == name:
            return entry
    return None


def remove_channel(item, draft):
    """Drop a chan item's subscription. Returns True if item was one."""
    if item["kind"] != "chan":
        return False
    draft["meshcore_channels"] = [e for e in draft.get("meshcore_channels") or [] if e["name"] != item["key"]]
    return True


def set_text(item, draft, text):
    """Apply prompt input. Returns an error string or None."""
    if item["kind"] == "addchan":
        if not text.strip():
            return None
        try:
            entry = parse_entry_text(text)
        except ValueError as exc:
            return str(exc)
        channels = draft.setdefault("meshcore_channels", [])
        if entry["name"].lower() in {e["name"].lower() for e in channels} | {"public"}:
            return f"{entry['name']} is already listed"
        channels.append(entry)
        return None
    if item["kind"] == "int":
        try:
            value = int(text.strip() or 0)
        except ValueError:
            return "Enter a whole number"
        draft[item["key"]] = max(item.get("minimum", 0), value)
    else:
        draft[item["key"]] = text.strip()
    return None


def finalize(draft):
    """Fix up a draft before saving (same rule as the Qt dialog: an empty
    hop list falls back to the defaults)."""
    if not draft.get("sniffer_hop_profiles"):
        draft["sniffer_hop_profiles"] = list(DEFAULT_HOP_PROFILES)
    return draft

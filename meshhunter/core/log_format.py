"""Formatting/classification for the terminal-style log view.

Framework-agnostic: classify_log_line() returns [(text, tag_or_None), ...]
segments, consumed by whichever GUI renders them (tk.Text tags today,
QTextCharFormat later).
"""

import re

from .constants import ADV_TYPE_NAMES


def format_rx_log(d):
    """Render a decoded RX_LOG_DATA payload as a compact human-readable line."""
    parts = []
    snr, rssi = d.get("snr"), d.get("rssi")
    if snr is not None or rssi is not None:
        parts.append(f"snr={snr} rssi={rssi}")

    route = d.get("route_typename", "?")
    ptype = d.get("payload_typename", "?")
    parts.append(f"route={route} type={ptype}")

    if d.get("path_len"):
        parts.append(f"path_len={d['path_len']} path={d.get('path', '')}")

    if ptype == "ADVERT":
        adv_type = d.get("adv_type")
        parts.append(f"adv_type={ADV_TYPE_NAMES.get(adv_type, adv_type)}")
        if d.get("adv_name"):
            parts.append(f"name={d['adv_name']!r}")
        if d.get("adv_lat") is not None:
            parts.append(f"loc=({d['adv_lat']:.5f},{d['adv_lon']:.5f})")
    elif ptype == "GRP_TXT":
        chan = d.get("chan_name")
        if d.get("message") is not None:
            parts.append(f"chan={chan or '?'!r} msg={d['message']!r}")
        elif chan:
            parts.append(f"chan={chan!r} (encrypted)")
        else:
            parts.append(f"chan_hash={d.get('chan_hash')} (unknown channel)")

    return " ".join(parts)


def format_contact_msg(d, sender_name):
    """Render a decoded CONTACT_MSG_RECV (direct message) payload.

    sender_name is pre-resolved by the caller (worker.py, via
    meshcore.get_contact_by_key_prefix) -- pass a fallback string, not
    None, if resolution failed.
    """
    parts = [f"from={sender_name!r}"]
    if d.get("text") is not None:
        parts.append(f"msg={d['text']!r}")
    return " ".join(parts)


def format_channel_msg(d, channel_name):
    """Render a decoded CHANNEL_MSG_RECV (channel message) payload.

    channel_name is pre-resolved by the caller (worker.py, via
    commands.get_channel) -- pass None if unresolved/unnamed, falls back
    to showing the raw channel_idx.
    """
    if channel_name:
        parts = [f"chan={channel_name!r}"]
    else:
        parts = [f"chan_idx={d.get('channel_idx')} (unnamed channel)"]
    if d.get("text") is not None:
        parts.append(f"msg={d['text']!r}")
    return " ".join(parts)


# --- Terminal log line coloring -------------------------------------------
# Classifies each already-composed log line string (the same text that's
# written to the plaintext session log) into colored spans, rather than
# threading a structured event type through every producer.
_LOG_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\] (\S+): (.*)$")
_FENCE_RE = re.compile(r"^-- (.*) --$")
_TOKEN_RE = re.compile(r"(?P<kv>\b[a-zA-Z_][a-zA-Z0-9_]*=\S+)|(?P<warn>\(unknown channel\))")


def _tokenize(text):
    segments = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() > pos:
            segments.append((text[pos:m.start()], None))
        if m.group("kv"):
            key, _, val = m.group("kv").partition("=")
            segments.append((f"{key}=", "key"))
            segments.append((val, "val"))
        else:
            segments.append((m.group("warn"), "warn"))
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], None))
    return segments


def _tokenize_rest(rest):
    path_match = re.search(r"\bpath=(\S+)", rest)
    if not path_match:
        return _tokenize(rest)
    before = rest[:path_match.start()].rstrip()
    after = rest[path_match.end():]
    segments = _tokenize(before)
    segments.append(("\n        ", None))
    segments.append((f"path={path_match.group(1)}", "path"))
    segments.extend(_tokenize(after))
    return segments


def classify_log_line(line):
    """Return [(text, tag_or_None), ...] segments for coloring one terminal line."""
    m = _FENCE_RE.match(line)
    if m:
        body = m.group(1)
        tag = "warn" if re.search(r"fail|error", body, re.IGNORECASE) else "ok"
        return [(f"── {body}", tag)]

    m = _LOG_LINE_RE.match(line)
    if not m:
        return [(line, "dim")]

    ts, event, rest = m.groups()
    event_tag = "warn" if event in ("WARNING", "ERROR") else "event"
    segments = [(ts, "ts"), (" ", None), (event, event_tag)]
    if rest:
        segments.append((": ", None))
        segments.extend(_tokenize_rest(rest))
    return segments

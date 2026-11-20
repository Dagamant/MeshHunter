"""Dictionary attack to recover MeshCore #hashtag channel names.

A "#name" channel derives its key from the name alone (sha256("#name")[:16]
-- see core/channels.py), so a channel we have no key for can still be
recovered if we can *guess its name*: derive the key from a candidate name
and test it against a captured group packet. The 1-byte channel hash narrows
the candidates, and each packet's 2-byte MAC confirms a real hit (not just a
hash collision) before we ever trust a decrypted message.

This only works for #hashtag channels. A channel with a random 16-byte key
(a private club channel) can't be found this way -- there's nothing to
guess -- so those stay unreadable, which is the point of using one.

What it can't give you: the name of a channel nobody named in the wordlist.
Add likely names (your town, club, callsign area...) to widen the search.
"""

from .channels import DEFAULT_CHANNEL_NAMES, decrypt_group_text, make_channel, normalize_name
from .paths import DATA_DIR

# A candidate channel name list is just text, so users can grow it. The
# bundled starter list is common public-channel names plus US regions; a
# per-user file (channel_wordlist.txt in DATA_DIR, one name per line, with
# or without the leading #) is merged in when present.
WORDLIST_PATH = DATA_DIR / "channel_wordlist.txt"

_BUILTIN_WORDS = [
    # General public / chat channels seen in the wild.
    "test", "testing", "tester", "bot", "bots", "general", "chat", "public",
    "lobby", "main", "home", "local", "mesh", "meshcore", "random", "offtopic",
    "news", "alert", "alerts", "status", "net", "info", "help", "ping", "pong",
    "hamradio", "ham", "radio", "aprs", "digital", "voice", "ptt", "dev", "devs",
    "emergency", "ares", "races", "skywarn", "weather", "wx", "traffic", "survival",
    "preppers", "prepper", "offgrid", "solar", "fun", "jokes", "sports", "music",
    "gaming", "food", "coffee", "beer", "lounge", "social", "community", "events",
    "market", "forsale", "trade", "wardriving", "mapping", "nodes", "repeater",
    # Community bot channels documented by regional groups.
    "bot-sea", "bot-tacoma", "bot-islands", "bot-van", "olybot", "pathbot",
    "berlinmesh", "dwebcamp", "hackerspacenijmegen",
    # US states.
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "newhampshire", "newjersey", "newmexico", "newyork",
    "northcarolina", "northdakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhodeisland", "southcarolina", "southdakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "westvirginia", "wisconsin", "wyoming",
    # Major metros / common city names.
    "atlanta", "atl", "austin", "boston", "chicago", "chi", "dallas", "denver",
    "detroit", "houston", "miami", "nyc", "nyack", "philly", "phoenix", "pdx",
    "portland", "sandiego", "sanfran", "sf", "sfbay", "bayarea", "seattle", "sea",
    "tacoma", "tampa", "vegas", "la", "sacramento", "sanjose", "sanantonio",
    "columbus", "charlotte", "nashville", "memphis", "kansascity", "kc",
]


def candidate_names(extra=(), wordlist=None):
    """Distinct candidate "#name" strings. Built-ins + the default
    subscriptions + whatever's in the user wordlist + `extra` (e.g. words
    seen in already-decrypted chat). Each is tried with and without common
    variants so "#atlanta" also covers "#atl" only if listed separately."""
    names, seen = [], set()
    sources = [
        *_BUILTIN_WORDS,
        *(n.lstrip("#") for n in DEFAULT_CHANNEL_NAMES),
        *(_load_wordlist() if wordlist is None else wordlist),
        *extra,
    ]
    for word in sources:
        word = normalize_name(word).lstrip("#").lower()
        if not word or word in seen:
            continue
        seen.add(word)
        names.append("#" + word)
    return names


def _load_wordlist():
    """Names from the user wordlist. One per line, with or without a leading
    "#" (candidate_names strips it either way). Blank lines and ";" comments
    are ignored -- "#" can't start a comment since channel names use it."""
    try:
        text = WORDLIST_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(";"):
            out.append(line)
    return out


def _hex_to_payload(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    try:
        return bytes.fromhex(value)
    except (ValueError, TypeError):
        return None


def crack(payloads, names=None, known_hashes=()):
    """Try candidate channel names against captured group-text payloads.

    payloads: iterable of encrypted group payloads (bytes, or hex strings):
        each is [1-byte chan hash][2-byte MAC][AES ciphertext], exactly what
        decode stores as grp_cipher / a companion's pkt_payload.
    names: candidate "#name" list; defaults to candidate_names().
    known_hashes: channel hashes to skip (already subscribed), so the attack
        only spends time on channels you actually can't read.

    Returns a list of {"name", "hash", "key", "count", "messages"} for every
    name that MAC-verified against at least one packet, most-heard first.
    Never raises on bad input -- unparseable payloads are skipped.
    """
    if names is None:
        names = candidate_names()
    known = set(known_hashes)

    # Only build keys for names whose hash actually shows up in the capture
    # and isn't already known -- most candidates never match a single packet.
    by_hash = {}
    for pkt in payloads:
        raw = _hex_to_payload(pkt)
        if raw is None or len(raw) < 3:
            continue
        h = raw[0]
        if h in known:
            continue
        by_hash.setdefault(h, []).append(raw)
    if not by_hash:
        return []

    results = {}
    for name in names:
        try:
            chan = make_channel(name)
        except ValueError:
            continue
        packets = by_hash.get(chan["hash"])
        if not packets:
            continue
        for raw in packets:
            found = decrypt_group_text(raw, [chan])
            if not found:
                continue
            entry = results.setdefault(
                name, {"name": name, "hash": f"{chan['hash']:02x}",
                       "key": chan["key"].hex(), "count": 0, "messages": []}
            )
            entry["count"] += 1
            if len(entry["messages"]) < 5:
                entry["messages"].append(found["message"])
    return sorted(results.values(), key=lambda e: e["count"], reverse=True)


def captured_payloads(rows=(), raw_log=True):
    """Encrypted group payloads to attack, from two sources:

    rows: live MessageLog rows -- the encrypted ones carry grp_cipher.
    raw_log: also scan the on-disk sniffer packet log (all history, every
        packet the sniffer ever heard), re-decoding each to pull its group
        payload. Off for a companion-only session with no such log.
    """
    from .sniffer_decode import decode_meshcore

    seen, out = set(), []

    def take(hexstr):
        if hexstr and hexstr not in seen:
            seen.add(hexstr)
            out.append(hexstr)

    for m in rows:
        if m.get("grp_cipher"):
            take(m["grp_cipher"])

    if raw_log:
        from .paths import SNIFFER_PACKETS_PATH

        import json

        try:
            with SNIFFER_PACKETS_PATH.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        raw = bytes.fromhex(json.loads(line).get("hex", ""))
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                    d = decode_meshcore(raw)  # no keys: every group packet keeps grp_cipher
                    if d.get("grp_cipher"):
                        take(d["grp_cipher"])
        except OSError:
            pass
    return out


def words_from_messages(rows):
    """Words worth adding to the candidate list, harvested from chat we can
    already read -- people name other channels in conversation. Returns
    lowercased alphanumeric tokens 3-24 chars long."""
    import re

    words = set()
    for m in rows:
        if m.get("kind") not in ("text", "dm"):
            continue
        for tok in re.findall(r"#?([A-Za-z][A-Za-z0-9_-]{2,23})", m.get("text", "")):
            words.add(tok.lower())
    return sorted(words)

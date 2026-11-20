"""LoRa listening profiles for the MeshHunter sniffer firmware.

A "profile" is everything the SX1262 has to match in hardware before it
will hand us a packet: frequency, bandwidth, spreading factor, coding rate
and sync word. One radio can only sit on one profile at a time, which is
why the sniffer either stays fixed on one or hops between several.

Meshtastic picks its default US frequency by hashing the modem preset's
name into one of the band's channel slots (firmware RadioInterface.cpp:
djb2(name) % numChannels), so those frequencies are computed rather than
hardcoded -- the same code then also handles custom channel names.
"""

from dataclasses import asdict, dataclass

MESHCORE = "meshcore"
MESHTASTIC = "meshtastic"

SYNC_MESHCORE = 0x12  # RadioLib "private" sync word, as used by MeshCore
SYNC_MESHTASTIC = 0x2B

US_BAND_START_MHZ = 902.0
US_BAND_END_MHZ = 928.0

# Receiver preamble length. Shorter than either network transmits (16), so
# the SX1262 locks on to both, including packets joined part-way through
# their preamble while hopping.
RX_PREAMBLE = 8


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    family: str  # MESHCORE / MESHTASTIC / "other"
    freq: float  # MHz
    bw: float  # kHz
    sf: int
    cr: int  # 5..8 (4/5..4/8)
    sync: int
    preamble: int = RX_PREAMBLE
    # Meshtastic only: the channel name the default key is hashed with.
    channel_name: str = ""

    def symbol_ms(self):
        return (2 ** self.sf) / self.bw

    def auto_dwell_ms(self):
        """Mirrors the firmware's dwellFor() when dwell is 0 (auto)."""
        return max(8, int(-(-self.symbol_ms() * 10 // 1)))

    def label(self):
        return f"{self.name} — {self.freq:g} MHz / BW{self.bw:g} / SF{self.sf}"

    def to_command(self, profile_id):
        return f"P {profile_id} {self.freq:.4f} {self.bw:g} {self.sf} {self.cr} {self.sync:02x} {self.preamble}"


def djb2(text):
    h = 5381
    for c in text.encode():
        h = ((h << 5) + h + c) & 0xFFFFFFFF
    return h


def meshtastic_us_freq(channel_name, bw_khz):
    """Default-slot frequency for a channel name on the US band."""
    spacing = bw_khz / 1000.0
    num_channels = int((US_BAND_END_MHZ - US_BAND_START_MHZ) / spacing)
    slot = djb2(channel_name) % num_channels
    return round(US_BAND_START_MHZ + spacing / 2 + slot * spacing, 4)


# (preset display name, bw kHz, sf, cr)
_MESHTASTIC_PRESETS = [
    ("LongFast", 250, 11, 5),
    ("MediumFast", 250, 9, 5),
    ("MediumSlow", 250, 10, 5),
    ("ShortFast", 250, 7, 5),
    ("ShortSlow", 250, 8, 5),
    ("ShortTurbo", 500, 7, 5),
    ("LongMod", 125, 11, 8),
    ("LongSlow", 125, 12, 8),
]


def _build_builtin_profiles():
    profiles = [
        # MeshCore's recommended US/Canada setting. Other MeshCore radio
        # settings in use locally can be added via sniffer_custom_profiles.
        Profile("meshcore_us", "MeshCore US", MESHCORE, 910.525, 62.5, 7, 5, SYNC_MESHCORE),
    ]
    for name, bw, sf, cr in _MESHTASTIC_PRESETS:
        profiles.append(Profile(
            key=f"mt_{name.lower()}",
            name=f"Meshtastic {name}",
            family=MESHTASTIC,
            freq=meshtastic_us_freq(name, bw),
            bw=bw,
            sf=sf,
            cr=cr,
            sync=SYNC_MESHTASTIC,
            channel_name=name,
        ))
    return profiles


BUILTIN_PROFILES = _build_builtin_profiles()

DEFAULT_FIXED_PROFILE = "meshcore_us"
DEFAULT_HOP_PROFILES = ["meshcore_us", "mt_longfast", "mt_mediumfast"]


def custom_profiles(config):
    """Profiles from config.json's optional "sniffer_custom_profiles" list:
    [{"key": "...", "name": "...", "family": "meshcore", "freq": 910.525,
      "bw": 250, "sf": 10, "cr": 5, "sync": "12", "channel_name": ""}]
    Malformed entries are skipped rather than failing the whole list."""
    out = []
    for entry in config.get("sniffer_custom_profiles") or []:
        try:
            sync = entry.get("sync", "12")
            sync = int(sync, 16) if isinstance(sync, str) else int(sync)
            family = entry.get("family", "other")
            out.append(Profile(
                key=str(entry["key"]),
                name=str(entry.get("name") or entry["key"]),
                family=family,
                freq=float(entry["freq"]),
                bw=float(entry["bw"]),
                sf=int(entry["sf"]),
                cr=int(entry.get("cr", 5)),
                sync=sync,
                preamble=int(entry.get("preamble", RX_PREAMBLE)),
                channel_name=str(entry.get("channel_name", "")),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def all_profiles(config):
    return BUILTIN_PROFILES + custom_profiles(config)


def profiles_by_key(config):
    return {p.key: p for p in all_profiles(config)}


def selected_profiles(config):
    """The profiles the sniffer should listen on, in hop order."""
    by_key = profiles_by_key(config)
    if config.get("sniffer_mode", "hop") == "fixed":
        keys = [config.get("sniffer_fixed_profile") or DEFAULT_FIXED_PROFILE]
    else:
        keys = config.get("sniffer_hop_profiles") or DEFAULT_HOP_PROFILES
    chosen = [by_key[k] for k in keys if k in by_key]
    return chosen or [by_key[DEFAULT_FIXED_PROFILE]]


def profile_dict(profile):
    return asdict(profile)


def assign_profiles(config, radio_count):
    """Share the selected profiles out between `radio_count` radios, as a
    list of per-radio profile lists (in hop order).

    One radio: exactly selected_profiles(). Several radios:
    - hop mode: the hop list is dealt out round-robin, so each radio hops
      a shorter list (a radio left with one profile just stays on it);
    - fixed mode: the first radio takes the fixed profile and each other
      radio takes the next profile from the hop list, one each.
    A radio can end up with nothing to do (more radios than profiles); it
    stays idle.
    """
    if radio_count <= 1:
        return [selected_profiles(config)][:radio_count]
    by_key = profiles_by_key(config)
    hop = [by_key[k] for k in (config.get("sniffer_hop_profiles") or DEFAULT_HOP_PROFILES) if k in by_key]
    out = [[] for _ in range(radio_count)]
    if config.get("sniffer_mode", "hop") == "fixed":
        fixed = by_key.get(config.get("sniffer_fixed_profile") or DEFAULT_FIXED_PROFILE) or by_key[DEFAULT_FIXED_PROFILE]
        out[0] = [fixed]
        rest = [p for p in hop if p.key != fixed.key]
        for i in range(1, radio_count):
            if i - 1 < len(rest):
                out[i] = [rest[i - 1]]
    else:
        hop = hop or [by_key[DEFAULT_FIXED_PROFILE]]
        for i, p in enumerate(hop):
            out[i % radio_count].append(p)
    return out

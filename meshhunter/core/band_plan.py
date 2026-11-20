"""Known frequency allocations around the LoRa bands, drawn as a labelled
strip above the RTL-SDR waterfall (gui_qt/widgets/sdr_waterfall.py) so
traffic that isn't a mesh profile can be identified at a glance.

US allocations unless the label says otherwise. Mesh profiles themselves
aren't listed here; the waterfall already marks each sniffer profile.
Add entries freely: (low MHz, high MHz, short label, description, kind).
`kind` picks the colour (see KIND_COLORS).
"""

from collections import namedtuple

Band = namedtuple("Band", "lo hi label description kind")

BANDS = [
    # ---- 400-470 MHz
    Band(420.0, 450.0, "70 cm ham", "Amateur radio 70 cm band", "ham"),
    Band(433.05, 434.79, "ISM 433", "433 MHz ISM / LPD: key fobs, weather sensors, LoRa (EU433)", "ism"),
    Band(450.0, 470.0, "UHF LMR", "UHF land mobile: business and public-safety two-way radio", "lmr"),
    Band(462.55, 462.725, "FRS/GMRS", "FRS/GMRS walkie-talkie channels (462 MHz)", "lmr"),
    Band(467.5625, 467.7125, "FRS", "FRS walkie-talkie channels (467 MHz)", "lmr"),
    # ---- 800-870 MHz
    Band(806.0, 824.0, "800 LMR ↑", "800 MHz trunked radio (P25 / public safety), mobile transmit", "lmr"),
    Band(824.0, 849.0, "Cell B5 ↑", "Cellular band 5 (850 MHz) uplink: phones", "cell"),
    Band(851.0, 869.0, "800 LMR ↓", "800 MHz trunked radio (P25 / public safety), sites/repeaters", "lmr"),
    Band(863.0, 870.0, "EU SRD 868", "EU short-range devices: LoRaWAN EU868, Meshtastic/MeshCore EU, alarms", "ism"),
    Band(869.0, 894.0, "Cell B5 ↓", "Cellular band 5 (850 MHz) downlink: cell towers", "cell"),
    # ---- 896-941 MHz
    Band(896.0, 901.0, "900 SMR ↑", "900 MHz SMR / private broadband, mobile transmit", "lmr"),
    Band(901.0, 902.0, "NB-PCS", "Narrowband PCS (two-way paging)", "paging"),
    Band(902.0, 928.0, "ISM 902–928 · 33 cm ham · RFID",
         "Unlicensed ISM band, shared with the 33 cm amateur band; UHF RFID readers hop across it", "ism"),
    Band(902.3, 914.9, "LoRaWAN US915 ↑", "LoRaWAN uplink channels 0-71 (Helium, IoT sensors)", "lorawan"),
    Band(908.37, 908.47, "Z-Wave", "Z-Wave home automation, 908.42 MHz", "iot"),
    Band(915.95, 916.05, "Z-Wave", "Z-Wave home automation, 916.00 MHz", "iot"),
    Band(923.3, 927.5, "LoRaWAN US915 ↓", "LoRaWAN downlink channels (gateways)", "lorawan"),
    Band(928.0, 929.0, "MAS", "Multiple Address Systems (utility telemetry / SCADA)", "paging"),
    Band(929.0, 932.0, "Paging", "One-way paging (POCSAG / FLEX)", "paging"),
    Band(932.0, 935.0, "Fixed", "Fixed point-to-point / MAS", "paging"),
    Band(935.0, 940.0, "900 SMR ↓", "900 MHz SMR / private broadband, base transmit", "lmr"),
    Band(940.0, 941.0, "NB-PCS", "Narrowband PCS (two-way paging)", "paging"),
]

KIND_COLORS = {
    "ism": "#39ff14",
    "lorawan": "#4fd1c5",
    # Sniffer profiles (added by the waterfall, not listed in BANDS);
    # the same colours as the MC / MT badges.
    "meshcore": "#5ec8ff",
    "meshtastic": "#c79bff",
    "iot": "#ff8fd0",
    "ham": "#ffe14d",
    "lmr": "#ffb347",
    "cell": "#ff7878",
    "paging": "#c9b06a",
}


def bands_in(lo_hz, hi_hz):
    """Bands overlapping [lo_hz, hi_hz], widest first (so broad
    allocations take the top lane and narrow ones stack below)."""
    lo, hi = lo_hz / 1e6, hi_hz / 1e6
    found = [b for b in BANDS if b.hi > lo and b.lo < hi]
    return sorted(found, key=lambda b: (-(b.hi - b.lo), b.lo))


def bands_at(hz):
    mhz = hz / 1e6
    return [b for b in BANDS if b.lo <= mhz <= b.hi]


def assign_lanes(bands, min_gap=0.0):
    """[(band, lane)] with no two bands overlapping in the same lane."""
    lanes = []  # per lane: list of (lo, hi)
    out = []
    for b in bands:
        for i, spans in enumerate(lanes):
            if all(b.lo >= hi + min_gap or b.hi <= lo - min_gap for lo, hi in spans):
                spans.append((b.lo, b.hi))
                out.append((b, i))
                break
        else:
            lanes.append([(b.lo, b.hi)])
            out.append((b, len(lanes) - 1))
    return out

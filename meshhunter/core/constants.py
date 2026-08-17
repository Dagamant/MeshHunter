"""Pure, OS-independent constants shared across the app.

No filesystem/OS resolution here (that's paths.py) -- everything in this
module is safe to import from anywhere, including tests.
"""

GPS_BAUD = 9600

CHAT_TYPE = 1  # MeshCore ADV_TYPE_CHAT (companion/client nodes)
REPEATER_TYPE = 2  # MeshCore ADV_TYPE_REPEATER
ROOM_TYPE = 3  # MeshCore ADV_TYPE_ROOM
SENSOR_TYPE = 4  # MeshCore ADV_TYPE_SENSOR

TERMINAL_MAX_LINES = 500
ADV_TYPE_NAMES = {1: "Chat", 2: "Repeater", 3: "Room", 4: "Sensor"}
NODE_DISPLAY_TYPES = [(CHAT_TYPE, "Chat"), (REPEATER_TYPE, "Repeater"), (ROOM_TYPE, "Room"), (SENSOR_TYPE, "Sensor")]
SERIAL_BAUD = 115200

# repeaters.csv / chat_nodes.csv / room_nodes.csv / sensor_nodes.csv column
# shape mirrors WDGWars' meshcore_nodes upload record
# (github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars, heimdall.py
# _build_record), plus a trailing last_heard column that isn't part of that
# schema, plus our own logger_lat/logger_lon (where *we* were when this
# node was first logged, from the local GPS receiver -- not the node's own
# advertised position) and uploaded_wdgwars/uploaded_ingest (blank until
# that endpoint has accepted this row at least once; batch mode uses these
# to know what's still pending).
NODE_CSV_FIELDS = [
    "node_id", "node_type", "name", "lat", "lon", "rssi",
    "first_seen", "type", "network", "public_key", "last_heard",
    "logger_lat", "logger_lon", "uploaded_wdgwars", "uploaded_ingest",
]
NODE_ID_HEX_LEN = 16

# all_contacts.csv: an unfiltered-by-type (but still GPS-gated) dump of
# every contact the device reports -- unlike repeaters.csv/chat_nodes.csv/
# etc. this isn't split per adv type, it's just a full record of what's in
# the device's (and our) contact table.
ALL_CONTACTS_CSV_FIELDS = [
    "node_id", "name", "adv_type", "type_label", "public_key",
    "adv_lat", "adv_lon", "has_gps", "out_path", "last_advert", "lastmod",
    "first_seen", "last_heard",
]

WDGWARS_NODE_TYPE = "REPEATER"
MESHCORE_ENVELOPE_TYPE = "MESHCORE"
MESHCORE_NETWORK = "meshcore"

# BLE device-list entries are rendered as "BLE: <name> (<address>)" in the
# CONNECTION dropdown, appended after serial ports. The prefix is how
# parse_device_selection() tells a saved/selected value apart from a plain
# serial port path.
BLE_PREFIX = "BLE: "
BLE_SCAN_TIMEOUT = 4.0

DEFAULT_INGEST_URL = "http://localhost:8765/api/mesh_nodes/ingest"

# WDGWars upload: signed JSON envelope to /api/upload/, meshcore_nodes slot.
# Confirmed against github.com/Yggdrasil-AI-labs/gungnir (envelope.py) and
# github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars (heimdall.py upload()).
# The CSV upload endpoint (/api/upload-csv) is Wi-Fi/BLE-only (WiGLE/Bruce
# format) and has no mesh slot at all, so this is the only ingestion path.
WDGWARS_UPLOAD_URL = "https://wdgwars.pl/api/upload/"
WDGWARS_BATCH_SIZE = 1000

# Self-hosted dashboard ingest API (see ref/INGEST_API.md). Plain X-API-Key
# POST, no HMAC envelope. Only REPEATER/CHAT/ROOM are accepted node_types;
# SENSOR rows are skipped rather than sent.
INGEST_SUPPORTED_NODE_TYPES = {"REPEATER", "CHAT", "ROOM"}

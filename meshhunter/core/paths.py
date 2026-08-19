"""Where config/data/log files actually live on disk, per-OS.

The old single-file app kept everything next to main.py
(Path(__file__).with_name(...)) which breaks under packaging and may not
be writable on Windows/Mac install locations. This resolves a proper
per-OS user-data directory instead (via platformdirs), and includes a
one-time migration that copies any files found in the legacy
next-to-the-script location the first time the new location is used.
"""

import shutil
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from .constants import CHAT_TYPE, ROOM_TYPE, SENSOR_TYPE

APP_NAME = "meshhunter"

DATA_DIR = Path(user_data_dir(APP_NAME, appauthor=False))

# config.json holds plaintext API keys, so it gets its own per-user
# directory (platformdirs' config dir, e.g. ~/.config/meshhunter on Linux)
# rather than living alongside CSVs/logs in DATA_DIR, and save_config()
# writes it with 0600 permissions.
CONFIG_DIR = Path(user_config_dir(APP_NAME, appauthor=False))
CONFIG_PATH = CONFIG_DIR / "config.json"
REPEATERS_CSV_PATH = DATA_DIR / "repeaters.csv"
CHAT_NODES_CSV_PATH = DATA_DIR / "chat_nodes.csv"
ROOM_NODES_CSV_PATH = DATA_DIR / "room_nodes.csv"
SENSOR_NODES_CSV_PATH = DATA_DIR / "sensor_nodes.csv"
ALL_CONTACTS_CSV_PATH = DATA_DIR / "all_contacts.csv"
SESSION_LOG_DIR = DATA_DIR / "logs"

# Node types (beyond repeaters, handled separately) that only get
# logged/uploaded when they've advertised real coordinates (contacts with
# no GPS report adv_lat/adv_lon as 0,0, which WDGWars rejects outright as
# no_gps anyway). Repeaters are held to the same coordinates requirement,
# just via their own REPEATERS_CSV_PATH rather than this table.
# type -> (csv_path, wire node_type label, log label)
EXTRA_NODE_TYPES = {
    CHAT_TYPE: (CHAT_NODES_CSV_PATH, "CHAT", "chat node"),
    ROOM_TYPE: (ROOM_NODES_CSV_PATH, "ROOM", "room node"),
    SENSOR_TYPE: (SENSOR_NODES_CSV_PATH, "SENSOR", "sensor node"),
}

# Legacy data sources, newest/most-authoritative first:
#   1. The old platformdirs location from when this app was named
#      "mesh-sniff" (pre-rename) -- has whatever was most recently synced.
#   2. The very first layout, next to main.py/main_tk.py in the repo root --
#      only meaningful for the original dev-layout migration; packaged
#      builds won't have this at all, and migrate_legacy_data() just no-ops.
_OLD_APP_NAME = "mesh-sniff"
_LEGACY_DIRS = (
    Path(user_data_dir(_OLD_APP_NAME, appauthor=False)),
    Path(__file__).resolve().parent.parent.parent,
)
_LEGACY_FILES = (
    "config.json", "repeaters.csv", "chat_nodes.csv", "room_nodes.csv",
    "sensor_nodes.csv", "all_contacts.csv",
)


def migrate_legacy_data():
    """Copy files from a legacy data location into DATA_DIR, once.

    Tries each entry in _LEGACY_DIRS in order and takes the first match per
    file. Never overwrites an existing file in the new location; safe to
    call on every startup. Returns the list of filenames actually migrated
    (for logging), empty if there was nothing to do.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    migrated = []
    for name in _LEGACY_FILES:
        new = CONFIG_PATH if name == "config.json" else DATA_DIR / name
        if new.exists():
            continue
        for legacy_dir in _LEGACY_DIRS:
            old = legacy_dir / name
            if old.exists():
                shutil.copy2(old, new)
                if new is CONFIG_PATH:
                    new.chmod(0o600)
                migrated.append(name)
                break

    if not SESSION_LOG_DIR.exists():
        for legacy_dir in _LEGACY_DIRS:
            old_logs = legacy_dir / "logs"
            if old_logs.is_dir():
                shutil.copytree(old_logs, SESSION_LOG_DIR)
                migrated.append("logs/")
                break

    return migrated

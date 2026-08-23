"""Importing a meshcore-cli JSON export's contacts into the app's own
repeaters.csv/chat_nodes.csv/room_nodes.csv/sensor_nodes.csv.

Merges and dedupes by node_id exactly the way a live device sync would
populate them (see worker.py's _record_nodes): a contact already known
keeps its first_seen/logger_lat/logger_lon/uploaded_wdgwars/uploaded_ingest
untouched (so anything already sent to WDGWars/Ingest isn't re-sent), only
name/lat/lon/last_heard get refreshed. Contacts with no GPS fix (0,0) are
skipped, same as the live path.
"""

import json
from datetime import datetime
from pathlib import Path

from .constants import MESHCORE_ENVELOPE_TYPE, MESHCORE_NETWORK, REPEATER_TYPE, WDGWARS_NODE_TYPE
from .node_utils import derive_node_id, node_names
from .paths import EXTRA_NODE_TYPES, REPEATERS_CSV_PATH
from .storage import load_known_nodes, save_known_nodes


def _has_coords(lat, lon):
    try:
        return float(lat or 0) != 0 or float(lon or 0) != 0
    except (TypeError, ValueError):
        return False


def import_contacts_json(json_path, ui_queue):
    """Blocking; run on a background thread. Reports progress the same way
    the other upload/sync helpers in uploads.py do (kind, payload) tuples
    on ui_queue), and pushes a fresh node-count snapshot at the end so the
    NODES tiles reflect the import even if nothing is currently connected.
    """
    json_path = Path(json_path)
    try:
        data = json.loads(json_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        ui_queue.put(("log", f"-- Could not read {json_path.name}: {exc} --"))
        return

    contacts = data.get("contacts")
    if not isinstance(contacts, list):
        ui_queue.put(("log", f"-- {json_path.name} has no \"contacts\" list -- nothing imported --"))
        return

    type_to_csv = {REPEATER_TYPE: (REPEATERS_CSV_PATH, WDGWARS_NODE_TYPE, "repeater")}
    type_to_csv.update(EXTRA_NODE_TYPES)

    by_type = {}
    for c in contacts:
        by_type.setdefault(c.get("type"), []).append(c)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    counts = {}
    imported_anything = False

    for node_type, (csv_path, node_type_label, log_label) in type_to_csv.items():
        known_nodes = load_known_nodes(csv_path)
        group = [
            c for c in by_type.get(node_type, [])
            if c.get("public_key") and _has_coords(c.get("latitude"), c.get("longitude"))
        ]
        new_count = 0
        for c in group:
            node_id = derive_node_id(c["public_key"])
            is_new = node_id not in known_nodes
            row = known_nodes.setdefault(node_id, {
                "node_id": node_id,
                "first_seen": now,
                "logger_lat": "",
                "logger_lon": "",
            })
            last_advert = c.get("last_advert")
            row["node_type"] = node_type_label
            row["name"] = c.get("custom_name") or c.get("name") or node_id
            row["lat"] = c.get("latitude", "")
            row["lon"] = c.get("longitude", "")
            row["rssi"] = row.get("rssi", "")
            row["type"] = MESHCORE_ENVELOPE_TYPE
            row["network"] = MESHCORE_NETWORK
            row["public_key"] = c["public_key"]
            row["last_heard"] = (
                datetime.fromtimestamp(last_advert).strftime("%Y-%m-%d %H:%M:%S")
                if last_advert else now
            )
            if is_new:
                new_count += 1

        if group:
            save_known_nodes(known_nodes, csv_path)
            imported_anything = True
            ui_queue.put((
                "log",
                f"-- Imported {log_label}s from {json_path.name}: "
                f"{len(group)} with coordinates, {new_count} new --",
            ))
        counts[node_type] = len(node_names(known_nodes))

    if not imported_anything:
        ui_queue.put(("log", f"-- {json_path.name}: no contacts with coordinates to import --"))
    ui_queue.put(("nodes", counts))

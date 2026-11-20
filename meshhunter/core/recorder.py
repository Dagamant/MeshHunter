"""Persisting discovered MeshCore nodes to CSV and auto-uploading new ones.

Shared by MeshCoreWorker (nodes reported by a companion device) and
SnifferWorker (adverts decoded straight off the air), so a node lands in
the same CSVs and goes through the same upload path whichever way it was
heard.

`ctx` is any object with the worker's settings attributes: gps_state,
ui_queue, api_key, auto_upload, ingest_url, ingest_api_key,
ingest_auto_send, batch_uploads.
"""

import threading
from datetime import datetime

from .constants import ADV_TYPE_NAMES, MESHCORE_ENVELOPE_TYPE, MESHCORE_NETWORK
from .node_utils import derive_node_id, has_coords
from .storage import save_all_contacts, save_known_nodes
from .uploads import build_ingest_records, build_meshcore_records, send_to_ingest_api, upload_to_wdgwars


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def persist_nodes(ctx, nodes, known_nodes, csv_path, node_type_label):
    """Merge contact dicts into known_nodes and rewrite csv_path. Returns
    the node_ids that weren't known before.

    A contact dict may carry an optional "rssi" (the sniffer knows it; a
    companion's contact list doesn't), which is kept as the strongest
    signal heard so far.
    """
    now = _now()
    new_node_ids = []
    for c in nodes:
        pk = c["public_key"]
        node_id = derive_node_id(pk)
        if node_id not in known_nodes:
            new_node_ids.append(node_id)
        # logger_lat/lon (where *we* were) only get set here, inside
        # setdefault's default -- so a node that's already known keeps
        # whatever location it was first logged at, rather than getting
        # overwritten with wherever we are on every later resync.
        fix = ctx.gps_state.fix if ctx.gps_state else None
        logger_lat, logger_lon = fix if fix else ("", "")
        row = known_nodes.setdefault(node_id, {
            "node_id": node_id,
            "first_seen": now,
            "logger_lat": logger_lat,
            "logger_lon": logger_lon,
        })
        row["node_type"] = node_type_label
        row["name"] = c.get("adv_name") or node_id
        row["lat"] = c.get("adv_lat", "")
        row["lon"] = c.get("adv_lon", "")
        row["rssi"] = _best_rssi(row.get("rssi", ""), c.get("rssi"))
        row["type"] = MESHCORE_ENVELOPE_TYPE
        row["network"] = MESHCORE_NETWORK
        row["public_key"] = pk
        row["last_heard"] = now
    save_known_nodes(known_nodes, csv_path)
    return new_node_ids


def _best_rssi(existing, new):
    if new is None:
        return existing
    try:
        return existing if existing != "" and float(existing) >= float(new) else round(float(new), 1)
    except ValueError:
        return round(float(new), 1)


def upload_new_nodes(ctx, new_rows, log_label):
    """Auto-upload freshly discovered rows per ctx's settings. No-op in
    batch mode -- those rows are left for "Send pending batch"."""
    if not new_rows or ctx.batch_uploads:
        return

    if ctx.auto_upload and ctx.api_key:
        records = build_meshcore_records(new_rows)
        ctx.ui_queue.put(("log", f"-- Auto-uploading {len(records)} new {log_label}(s) to WDGWars --"))
        threading.Thread(
            target=upload_to_wdgwars, args=(records, ctx.api_key, ctx.ui_queue), daemon=True
        ).start()

    if ctx.ingest_auto_send and ctx.ingest_api_key and ctx.ingest_url:
        ingest_records = build_ingest_records(new_rows)
        if ingest_records:
            ctx.ui_queue.put(("log", f"-- Auto-sending {len(ingest_records)} new {log_label}(s) to Ingest API --"))
            threading.Thread(
                target=send_to_ingest_api,
                args=(ingest_records, ctx.ingest_url, ctx.ingest_api_key, ctx.ui_queue),
                daemon=True,
            ).start()


def record_all_contacts(known_all_contacts, contacts, csv_path):
    """Record every contact (any adv type) with a GPS fix into all_contacts.csv."""
    now = _now()
    for c in contacts:
        pk = c.get("public_key")
        if not pk or not has_coords(c):
            continue
        node_id = derive_node_id(pk)
        row = known_all_contacts.setdefault(node_id, {"node_id": node_id, "first_seen": now})
        row["name"] = c.get("adv_name") or node_id
        row["adv_type"] = c.get("type", "")
        row["type_label"] = ADV_TYPE_NAMES.get(c.get("type"), str(c.get("type", "")))
        row["public_key"] = pk
        row["adv_lat"] = c.get("adv_lat", "")
        row["adv_lon"] = c.get("adv_lon", "")
        row["has_gps"] = "yes" if has_coords(c) else "no"
        row["out_path"] = c.get("out_path", "")
        row["last_advert"] = c.get("last_advert", "")
        row["lastmod"] = c.get("lastmod", "")
        row["last_heard"] = now
    save_all_contacts(known_all_contacts, csv_path)

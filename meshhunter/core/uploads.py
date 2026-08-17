"""Uploading newly-discovered nodes to WDGWars and/or a generic ingest API."""

import base64
import hashlib
import hmac
import json
import secrets
import ssl
import urllib.error
import urllib.request
from datetime import datetime

from .constants import (
    INGEST_SUPPORTED_NODE_TYPES,
    MESHCORE_ENVELOPE_TYPE,
    MESHCORE_NETWORK,
    WDGWARS_BATCH_SIZE,
    WDGWARS_NODE_TYPE,
    WDGWARS_UPLOAD_URL,
)
from .paths import EXTRA_NODE_TYPES, REPEATERS_CSV_PATH
from .storage import load_known_nodes, save_known_nodes

_SSL_CTX = ssl.create_default_context()


def build_wdgwars_envelope(nodes, api_key):
    payload = {"networks": [], "aircraft": [], "meshcore_nodes": nodes}
    body_json = json.dumps(payload, separators=(",", ":"))
    data_b64 = base64.b64encode(body_json.encode()).decode()
    nonce = secrets.token_hex(8)
    sig = hmac.new(api_key.encode(), (nonce + data_b64).encode(), hashlib.sha256).hexdigest()
    return {"data": data_b64, "nonce": nonce, "sig": sig}


def build_meshcore_records(rows):
    records = []
    for row in rows.values():
        try:
            lat = float(row.get("lat") or 0.0)
            lon = float(row.get("lon") or 0.0)
        except ValueError:
            lat, lon = 0.0, 0.0
        rssi = None
        try:
            rssi = float(row["rssi"]) if row.get("rssi") else None
        except ValueError:
            rssi = None
        record = {
            "node_id": row["node_id"],
            "node_type": row.get("node_type") or WDGWARS_NODE_TYPE,
            "name": row.get("name") or row["node_id"],
            "lat": lat,
            "lon": lon,
            "rssi": rssi,
            "first_seen": row.get("first_seen", ""),
            "type": MESHCORE_ENVELOPE_TYPE,
            "network": MESHCORE_NETWORK,
        }
        if row.get("public_key"):
            record["public_key"] = row["public_key"]
        records.append(record)
    return records


def upload_to_wdgwars(records, api_key, ui_queue):
    """Returns True only if every batch's HTTP request succeeded (used by
    batch mode to decide whether the uploaded rows can be marked done --
    the response's rejected/reasons counts are informational, not a
    per-record result we could use for finer-grained marking)."""
    if not api_key:
        ui_queue.put(("log", "-- WDGWars upload failed: no API key configured --"))
        return False
    if not records:
        ui_queue.put(("log", "-- WDGWars upload skipped: no repeaters logged yet --"))
        return False
    all_ok = True
    for i in range(0, len(records), WDGWARS_BATCH_SIZE):
        chunk = records[i:i + WDGWARS_BATCH_SIZE]
        envelope = build_wdgwars_envelope(chunk, api_key)
        body = json.dumps(envelope).encode()
        req = urllib.request.Request(
            WDGWARS_UPLOAD_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "meshcore-cli/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", "replace")[:300]
            ui_queue.put(("log", f"-- WDGWars upload failed (HTTP {e.code}): {body_text} --"))
            all_ok = False
            continue
        except Exception as exc:
            ui_queue.put(("log", f"-- WDGWars upload failed: {exc} --"))
            all_ok = False
            continue

        imported = data.get("meshcore_imported", 0)
        seen = data.get("meshcore_already_seen", 0)
        rejected = data.get("meshcore_rejected", 0)
        reasons = data.get("meshcore_reject_reasons") or {}
        msg = f"-- WDGWars: {imported} new, {seen} already known"
        if rejected:
            msg += f", {rejected} rejected {reasons}"
        msg += " --"
        ui_queue.put(("log", msg))
        ui_queue.put(("wdgwars_sent", imported))
    return all_ok


def build_ingest_records(rows):
    records = []
    for row in rows.values():
        node_type = row.get("node_type", "")
        if node_type not in INGEST_SUPPORTED_NODE_TYPES:
            continue
        pk = row.get("public_key")
        if not pk:
            continue
        record = {"public_key": pk, "node_type": node_type}
        if row.get("node_id"):
            record["node_id"] = row["node_id"]
        if row.get("name"):
            record["name"] = row["name"]
        try:
            if row.get("lat"):
                record["lat"] = float(row["lat"])
            if row.get("lon"):
                record["lon"] = float(row["lon"])
        except ValueError:
            pass
        try:
            if row.get("rssi"):
                record["rssi"] = float(row["rssi"])
        except ValueError:
            pass
        if row.get("first_seen"):
            record["first_seen"] = row["first_seen"]
        if row.get("last_heard"):
            record["last_heard"] = row["last_heard"]
        if row.get("network"):
            record["network"] = row["network"]
        if row.get("type"):
            record["type"] = row["type"]
        # Where *we* were when this node was first logged (local GPS
        # receiver), not the node's own advertised lat/lon above.
        try:
            if row.get("logger_lat"):
                record["logger_lat"] = float(row["logger_lat"])
            if row.get("logger_lon"):
                record["logger_lon"] = float(row["logger_lon"])
        except ValueError:
            pass
        records.append(record)
    return records


def send_to_ingest_api(records, url, api_key, ui_queue):
    """Returns True only if the HTTP request itself succeeded (see
    upload_to_wdgwars's docstring for why that's the granularity batch
    mode marks rows done at)."""
    if not url:
        ui_queue.put(("log", "-- Ingest API send failed: no ingest URL configured --"))
        return False
    if not api_key:
        ui_queue.put(("log", "-- Ingest API send failed: no ingest API key configured --"))
        return False
    if not records:
        ui_queue.put(("log", "-- Ingest API send skipped: no nodes to send --"))
        return False
    body = json.dumps(records).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "meshcore-cli/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", "replace")[:300]
        ui_queue.put(("log", f"-- Ingest API send failed (HTTP {e.code}): {body_text} --"))
        return False
    except Exception as exc:
        ui_queue.put(("log", f"-- Ingest API send failed: {exc} --"))
        return False

    ingested = data.get("ingested", 0)
    errors = data.get("errors") or []
    msg = f"-- Ingest API: {ingested} node(s) ingested"
    if errors:
        msg += f", {len(errors)} error(s): {errors}"
    msg += " --"
    ui_queue.put(("log", msg))
    ui_queue.put(("ingest_sent", ingested))
    return True


def flush_pending_batch(config, ui_queue):
    """Load every known node from all four CSVs and send whatever's still
    pending (per the uploaded_wdgwars/uploaded_ingest columns) to each
    configured endpoint, marking rows done on success. Blocking HTTP calls
    -- run in a background thread. Used by the "Send pending batch" button
    regardless of whether batch mode is on, since it also serves as a
    manual retry for anything an earlier immediate-mode attempt failed to
    send (immediate mode never marks rows done, so a failed send just
    leaves them pending here too).
    """
    csv_paths = [REPEATERS_CSV_PATH] + [spec[0] for spec in EXTRA_NODE_TYPES.values()]
    sent_anything = False
    for csv_path in csv_paths:
        rows = load_known_nodes(csv_path)
        if not rows:
            continue

        if config.get("api_key"):
            pending = {nid: r for nid, r in rows.items() if not r.get("uploaded_wdgwars")}
            records = build_meshcore_records(pending) if pending else []
            if records:
                sent_anything = True
                ui_queue.put(("log", f"-- Sending {len(records)} pending node(s) from {csv_path.name} to WDGWars --"))
                if upload_to_wdgwars(records, config["api_key"], ui_queue):
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    for rec in records:
                        rows[rec["node_id"]]["uploaded_wdgwars"] = ts
                    save_known_nodes(rows, csv_path)

        if config.get("ingest_api_key") and config.get("ingest_url"):
            pending = {nid: r for nid, r in rows.items() if not r.get("uploaded_ingest")}
            records = build_ingest_records(pending) if pending else []
            if records:
                sent_anything = True
                ui_queue.put(
                    ("log", f"-- Sending {len(records)} pending node(s) from {csv_path.name} to Ingest API --")
                )
                if send_to_ingest_api(records, config["ingest_url"], config["ingest_api_key"], ui_queue):
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    for rec in records:
                        if "node_id" in rec:
                            rows[rec["node_id"]]["uploaded_ingest"] = ts
                    save_known_nodes(rows, csv_path)

    if not sent_anything:
        ui_queue.put(("log", "-- No pending nodes to send --"))

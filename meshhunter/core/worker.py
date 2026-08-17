"""The core MeshCore connection engine: subscribes to device events and
persists discovered nodes to CSV.

Runs as a task on the current, already-running event loop -- the app
merges Qt's event loop with asyncio via qasync before constructing the
main window (see gui_qt/app.py), so this worker shares that same loop
rather than owning a dedicated thread. Because the caller (a Qt slot,
on the same thread as this worker's loop) and the target loop are one
and the same, stop()/clear_all_contacts()/send_advert() need no
threadsafe wrapping -- direct calls and asyncio.ensure_future() are
enough, and stop() takes effect immediately.
"""

import asyncio
import threading
import time
from datetime import datetime

from meshcore import EventType, MeshCore

from .constants import (
    ADV_TYPE_NAMES,
    MESHCORE_ENVELOPE_TYPE,
    MESHCORE_NETWORK,
    NODE_DISPLAY_TYPES,
    REPEATER_TYPE,
    WDGWARS_NODE_TYPE,
)
from .node_utils import derive_node_id, has_coords, node_names
from .paths import ALL_CONTACTS_CSV_PATH, EXTRA_NODE_TYPES, REPEATERS_CSV_PATH
from .storage import load_all_contacts, load_known_nodes, save_all_contacts, save_known_nodes
from .log_format import format_rx_log
from .uploads import build_ingest_records, build_meshcore_records, send_to_ingest_api, upload_to_wdgwars


class MeshCoreWorker:
    """Connects to a MeshCore node, subscribes to its events, and persists
    discovered nodes to CSV."""

    def __init__(
        self,
        port,
        ui_queue,
        connection_kind="serial",
        api_key="",
        auto_upload=False,
        ingest_url="",
        ingest_api_key="",
        ingest_auto_send=False,
        auto_clear_contacts=False,
        batch_uploads=False,
        gps_state=None,
    ):
        self.port = port
        self.connection_kind = connection_kind
        self.ui_queue = ui_queue
        self.api_key = api_key
        self.auto_upload = auto_upload
        self.ingest_url = ingest_url
        self.ingest_api_key = ingest_api_key
        self.ingest_auto_send = ingest_auto_send
        self.auto_clear_contacts = auto_clear_contacts
        self.batch_uploads = batch_uploads
        self.gps_state = gps_state
        self.loop = None
        self.meshcore = None
        self.known_repeaters = {}
        self.known_extra_nodes = {t: {} for t in EXTRA_NODE_TYPES}
        self.known_all_contacts = {}
        self._stop_event = None
        self._contacts_sync_lock = None
        self._resync_pending = False
        # Set while _clear_all_contacts's bulk loop is removing every
        # contact, so _record_nodes's own auto-clear (triggered by the same
        # get_contacts() refresh _clear_all_contacts forces) doesn't try to
        # remove the same contacts a second time -- see _remove_contact's
        # docstring for why that race matters.
        self._bulk_clearing = False
        # Set the instant stop() is called even if _main() hasn't started
        # running yet (or hasn't reached its first line) and
        # self._stop_event is still None -- without this, a disconnect
        # click landing in that startup window would be silently dropped
        # and the connect would go ahead anyway, with nothing left able to
        # stop it.
        self._stop_requested = False
        self._task = None

    def start(self):
        self.loop = asyncio.get_event_loop()
        self._task = asyncio.ensure_future(self._main())

    def is_alive(self):
        return self._task is not None and not self._task.done()

    def stop(self):
        self._stop_requested = True
        if self._stop_event is not None:
            self._stop_event.set()

    def clear_all_contacts(self):
        """Remove every contact (all types) stored on the device."""
        if self.meshcore is not None:
            asyncio.ensure_future(self._clear_all_contacts())

    def send_advert(self, flood):
        if self.meshcore is not None:
            asyncio.ensure_future(self._send_advert(flood))

    async def _main(self):
        self._stop_event = asyncio.Event()
        if self._stop_requested:
            self.ui_queue.put(("status", "Disconnected"))
            return
        target = f"BLE {self.port}" if self.connection_kind == "ble" else self.port
        try:
            if self.connection_kind == "ble":
                meshcore = await MeshCore.create_ble(address=self.port)
            else:
                meshcore = await MeshCore.create_serial(self.port)
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))
            return
        if meshcore is None:
            self.ui_queue.put(("error", f"Could not connect to {target}"))
            return

        # We drive contact resyncs ourselves (see _request_resync) instead of
        # the library's built-in auto_update_contacts: that path is triggered
        # independently, per-event, with no locking, so a burst of adverts
        # can fire overlapping get_contacts() calls that race on the reader's
        # shared contacts dict (reset to {} at the start of every sync),
        # producing exactly the "clears then reloads" symptom.
        meshcore.auto_update_contacts = False
        meshcore.set_decrypt_channel_logs(True)
        self._contacts_sync_lock = asyncio.Lock()
        meshcore.subscribe(EventType.CONTACTS, self._on_contacts)
        meshcore.subscribe(EventType.NEW_CONTACT, self._on_new_contact)
        meshcore.subscribe(EventType.ADVERTISEMENT, self._request_resync)
        meshcore.subscribe(EventType.PATH_UPDATE, self._request_resync)
        meshcore.subscribe(None, self._on_any_event)
        self.meshcore = meshcore

        self.known_repeaters = load_known_nodes(REPEATERS_CSV_PATH)
        self.known_all_contacts = load_all_contacts(ALL_CONTACTS_CSV_PATH)
        self.ui_queue.put(("status", f"Connected to {target}"))
        self.ui_queue.put(("log", f"-- Logging full contact list (any type, GPS or not) to {ALL_CONTACTS_CSV_PATH} --"))
        self.ui_queue.put(("log", f"-- Logging heard repeaters with coordinates to {REPEATERS_CSV_PATH} --"))
        initial_nodes = {REPEATER_TYPE: len(self.known_repeaters)}
        for node_type, (csv_path, _, log_label) in EXTRA_NODE_TYPES.items():
            self.known_extra_nodes[node_type] = load_known_nodes(csv_path)
            initial_nodes[node_type] = len(self.known_extra_nodes[node_type])
            self.ui_queue.put(("log", f"-- Logging {log_label}s with coordinates to {csv_path} --"))
        self.ui_queue.put(("nodes", initial_nodes))

        await self._stop_event.wait()

        self.meshcore = None
        await meshcore.disconnect()
        self.ui_queue.put(("status", "Disconnected"))

    def _known_dict(self, node_type):
        return self.known_repeaters if node_type == REPEATER_TYPE else self.known_extra_nodes[node_type]

    async def _record_contact(self, c):
        """Record one contact dict into the right known-nodes dict/CSV.

        Used both for a full CONTACTS sync and for a single NEW_CONTACT push
        payload. The latter matters because PUSH_CODE_NEW_ADVERT carries the
        full contact record the moment the device hears it; if the device's
        own contact table is full, the firmware may not keep the node in its
        stored table, and a later get_contacts() resync would then silently
        omit it. Recording the push payload directly means a node we were
        told about is never lost to our CSV purely because the device
        couldn't (or didn't) retain it.
        """
        node_type = c.get("type")
        if node_type == REPEATER_TYPE and has_coords(c):
            await self._record_nodes([c], self.known_repeaters, REPEATERS_CSV_PATH, WDGWARS_NODE_TYPE, "repeater")
        elif node_type in EXTRA_NODE_TYPES and has_coords(c):
            csv_path, node_type_label, log_label = EXTRA_NODE_TYPES[node_type]
            await self._record_nodes([c], self.known_extra_nodes[node_type], csv_path, node_type_label, log_label)

    def _push_node_display(self, live_contacts=None):
        # Count = CSV-backed knowledge merged with whatever the device is
        # currently reporting live (if we have a fresh live snapshot),
        # de-duplicated by name so a node known both ways isn't double
        # counted. A node recorded previously (or dropped from the device's
        # own contact table, e.g. because it's full) still counts; anything
        # the device reports live still counts even if it didn't qualify for
        # CSV persistence (e.g. a chat contact with no GPS fix).
        by_type = {node_type: set(node_names(self._known_dict(node_type))) for node_type, _ in NODE_DISPLAY_TYPES}
        if live_contacts:
            for c in live_contacts.values():
                names = by_type.get(c.get("type"))
                if names is not None:
                    names.add(c.get("adv_name") or c["public_key"][:8])
        self.ui_queue.put(("nodes", {t: len(names) for t, names in by_type.items()}))

    def _record_all_contacts(self, contacts):
        """Record every contact (any adv type) into all_contacts.csv.

        Unlike _record_nodes/_record_contact, this doesn't filter by adv
        type -- it's a full dump of whatever the device (or a push payload)
        reports, across Chat/Repeater/Room/Sensor alike. It does still skip
        contacts with no GPS fix, same as the per-type CSVs.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for c in contacts:
            pk = c.get("public_key")
            if not pk or not has_coords(c):
                continue
            node_id = derive_node_id(pk)
            row = self.known_all_contacts.setdefault(node_id, {"node_id": node_id, "first_seen": now})
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
        save_all_contacts(self.known_all_contacts, ALL_CONTACTS_CSV_PATH)

    async def _on_contacts(self, event):
        contacts = event.payload or {}
        self._record_all_contacts(contacts.values())

        repeaters = [c for c in contacts.values() if c.get("type") == REPEATER_TYPE and has_coords(c)]
        if repeaters:
            await self._record_nodes(repeaters, self.known_repeaters, REPEATERS_CSV_PATH, WDGWARS_NODE_TYPE, "repeater")

        for node_type, (csv_path, node_type_label, log_label) in EXTRA_NODE_TYPES.items():
            matched = [c for c in contacts.values() if c.get("type") == node_type and has_coords(c)]
            if matched:
                await self._record_nodes(matched, self.known_extra_nodes[node_type], csv_path, node_type_label, log_label)

        self._push_node_display(contacts)

    async def _on_new_contact(self, event):
        self._record_all_contacts([event.payload])
        await self._record_contact(event.payload)
        # Refresh the GUI immediately with what we just recorded, rather
        # than waiting on the round-trip get_contacts() resync below (which
        # can take a while on a large contact list, and per _record_contact's
        # docstring may not even come back with this node if the device's
        # own table is full).
        self._push_node_display()
        await self._request_resync(event)

    async def _request_resync(self, event):
        # NEW_CONTACT/ADVERTISEMENT/PATH_UPDATE can all fire in bursts (several
        # nodes advertising close together). get_contacts() streams
        # CONTACT_START..CONTACT_END, and the reader resets its contacts dict
        # to {} at CONTACT_START, so two overlapping syncs stomp on each
        # other and can hand _on_contacts a partial list mid-stream. The lock
        # serializes actual syncs; the pending flag coalesces bursts into at
        # most one extra sync after the in-flight one finishes, rather than
        # queuing a redundant full resync per event. Since everything now
        # runs on one event loop (no separate worker thread), this guards
        # against coroutine interleaving at await points rather than a
        # second OS thread racing in -- same lock, same coalescing
        # behavior, different kind of overlap prevented.
        if self._resync_pending:
            return
        self._resync_pending = True
        async with self._contacts_sync_lock:
            self._resync_pending = False
            await self.meshcore.commands.get_contacts()

    async def _record_nodes(self, nodes, known_nodes, csv_path, node_type_label, log_label):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            fix = self.gps_state.fix if self.gps_state else None
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
            row["rssi"] = row.get("rssi", "")
            row["type"] = MESHCORE_ENVELOPE_TYPE
            row["network"] = MESHCORE_NETWORK
            row["public_key"] = pk
            row["last_heard"] = now
        save_known_nodes(known_nodes, csv_path)

        if self.auto_clear_contacts and not self._bulk_clearing:
            # Once a node is safely persisted to our CSV, its device-side
            # contact slot isn't needed anymore -- removing it here keeps
            # the device's (limited) contact table free for discovering
            # nodes we haven't logged yet, instead of filling up with ones
            # we already have. Applies to every contact just synced, not
            # just newly-seen ones, so a node that reappears on the device
            # after an earlier removal (e.g. re-heard later) gets cleared
            # again rather than being left there because it wasn't "new".
            # Skipped entirely while a bulk "Clear device contacts" is in
            # progress -- that loop's own get_contacts() refresh would
            # otherwise trigger this same auto-clear concurrently on the
            # same contacts it's already removing (see _remove_contact).
            for c in nodes:
                ok, detail = await self._remove_contact(c)
                if not ok:
                    name = c.get("adv_name") or c["public_key"][:8]
                    self.ui_queue.put(("log", f"-- Failed to auto-clear {name} from device: {detail} --"))

        if not new_node_ids:
            return
        new_rows = {nid: known_nodes[nid] for nid in new_node_ids}

        if self.batch_uploads:
            # Deferred: leave these rows for "Send pending batch" to pick
            # up later instead of uploading immediately, so a live internet
            # connection isn't required right when a node is heard.
            return

        if self.auto_upload and self.api_key:
            records = build_meshcore_records(new_rows)
            self.ui_queue.put(("log", f"-- Auto-uploading {len(records)} new {log_label}(s) to WDGWars --"))
            threading.Thread(
                target=upload_to_wdgwars, args=(records, self.api_key, self.ui_queue), daemon=True
            ).start()

        if self.ingest_auto_send and self.ingest_api_key and self.ingest_url:
            ingest_records = build_ingest_records(new_rows)
            if ingest_records:
                self.ui_queue.put(("log", f"-- Auto-sending {len(ingest_records)} new {log_label}(s) to Ingest API --"))
                threading.Thread(
                    target=send_to_ingest_api,
                    args=(ingest_records, self.ingest_url, self.ingest_api_key, self.ui_queue),
                    daemon=True,
                ).start()

    async def _on_any_event(self, event):
        ts = time.strftime("%H:%M:%S")
        if event.type == EventType.RX_LOG_DATA:
            payload_str = format_rx_log(event.payload or {})
        else:
            payload_str = str(event.payload)
            if len(payload_str) > 200:
                payload_str = payload_str[:200] + "…"
        self.ui_queue.put(("log", f"[{ts}] {event.type.value}: {payload_str}"))

    async def _remove_contact(self, c):
        """Remove one contact from the device, treating "already gone"
        (ERR_CODE_NOT_FOUND) as success rather than a failure -- it just
        means something else (a concurrent auto-clear, another resync)
        removed it first, not that removal is actually broken.
        commands.remove_contact() doesn't raise for a device-level error,
        it returns an Event, so the result has to be checked directly
        rather than relying on try/except.

        Returns (ok, detail): ok is True if the contact is gone either way;
        detail is the failure's code_string when ok is False.
        """
        result = await self.meshcore.commands.remove_contact(c)
        if result.type != EventType.ERROR:
            return True, None
        code = result.payload.get("code_string", result.payload)
        return code == "ERR_CODE_NOT_FOUND", code

    async def _clear_all_contacts(self):
        # meshcore.contacts is only as fresh as the last resync, and nothing
        # forces one right after connecting (_request_resync only fires off
        # ADVERTISEMENT/PATH_UPDATE/NEW_CONTACT events) -- so without this,
        # clicking "Clear device contacts" soon after connecting can see an
        # empty/stale cache and remove nothing, despite the device holding
        # plenty of contacts. Force a fresh full sync first, through the
        # same lock _request_resync uses, so it can't race an in-flight one.
        async with self._contacts_sync_lock:
            await self.meshcore.commands.get_contacts()
        targets = list(self.meshcore.contacts.values())
        self.ui_queue.put(("log", f"-- Removing {len(targets)} device contact(s) --"))
        # The get_contacts() above is itself a CONTACTS event that _on_contacts
        # (a global subscriber, not just this method's caller) also reacts
        # to -- with auto_clear_contacts on, that would otherwise start
        # removing the same contacts this loop is about to remove. Suppress
        # that path for the duration so the two don't race each other.
        self._bulk_clearing = True
        try:
            removed = 0
            for c in targets:
                ok, detail = await self._remove_contact(c)
                if ok:
                    removed += 1
                else:
                    name = c.get("adv_name") or c["public_key"][:8]
                    self.ui_queue.put(("log", f"-- Failed to remove {name}: {detail} --"))
        finally:
            self._bulk_clearing = False
        self.ui_queue.put(("log", f"-- Removed {removed}/{len(targets)} contact(s) --"))
        await self.meshcore.commands.get_contacts()

    async def _send_advert(self, flood):
        kind = "flood" if flood else "0-hop"
        try:
            result = await self.meshcore.commands.send_advert(flood=flood)
        except Exception as exc:
            self.ui_queue.put(("log", f"-- Failed to send {kind} advert: {exc} --"))
            return
        if result.type == EventType.ERROR:
            detail = result.payload.get("code_string", result.payload)
            self.ui_queue.put(("log", f"-- Failed to send {kind} advert: {detail} --"))
        else:
            self.ui_queue.put(("log", f"-- Sent {kind} advert --"))

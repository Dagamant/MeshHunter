"""SnifferWorker: drives one or more MeshHunter LoRa sniffer radios.

Counterpart to MeshCoreWorker for sniffer hardware (see firmware/sniffer):
the XIAO running the sniffer firmware over USB serial, and/or CH341 USB
LoRa sticks such as the MeshToad (core/usb_sniffer.py runs the same
firmware logic on this machine). Instead of asking a companion node what
it knows, it receives every raw packet the radios hear, decodes MeshCore
and Meshtastic traffic itself, and records:

- MeshCore adverts with valid signatures -> the same repeaters/chat/room/
  sensor CSVs + uploads as the companion path (via core.recorder)
- Meshtastic nodes -> meshtastic_nodes.csv (local only)
- every packet, raw + decoded -> sniffer_packets.jsonl

Several radios work as one device: the selected profiles are shared out
between them (sniffer_profiles.assign_profiles) and each profile keeps one
global id, so events from every radio land in one waterfall and one set
of node tables. Each radio has its own thread (_RadioLink, since pyserial
reads block); all of them feed one queue that a single thread decodes and
records from, so the node tables and CSVs are only ever touched by that
thread. With several radios, a radio that fails or is unplugged is retried
every RADIO_RETRY_S while the others carry on; with one radio a failure
ends the connection, as before.

Reports go out through ui_queue exactly like MeshCoreWorker's. Settings
changes are pushed live: apply_config() re-shares the profiles and the
radios re-send their lists without reconnecting.
"""

import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import serial

from .constants import (
    ADV_TYPE_NAMES,
    MESHTASTIC_COUNT_KEY,
    NODE_DISPLAY_TYPES,
    REPEATER_TYPE,
    SERIAL_BAUD,
    WDGWARS_NODE_TYPE,
)
from .devices import parse_device_selection
from .log_format import format_rx_log
from .messages import meshcore_message, meshtastic_message
from .node_utils import derive_node_id, node_names
from .paths import (
    ALL_CONTACTS_CSV_PATH,
    EXTRA_NODE_TYPES,
    MESHTASTIC_NODES_CSV_PATH,
    REPEATERS_CSV_PATH,
    SNIFFER_PACKETS_PATH,
)
from .recorder import persist_nodes, record_all_contacts, upload_new_nodes
from .sniffer_decode import (
    decode_meshcore,
    decode_meshtastic,
    default_meshcore_channels,
    default_meshtastic_channels,
    format_meshtastic,
)
from .sniffer_profiles import BUILTIN_PROFILES, SYNC_MESHCORE, SYNC_MESHTASTIC, assign_profiles
from .storage import load_all_contacts, load_known_nodes, load_meshtastic_nodes, save_meshtastic_nodes

HELLO_TIMEOUT_S = 5.0
MESHTASTIC_SAVE_INTERVAL_S = 5.0
RADIO_RETRY_S = 10.0
RSSI_MERGE_S = 0.2  # matches the firmware's RSSI_REPORT_MS

DEFAULT_TIMING = {
    "sniffer_dwell_ms": 0,
    "sniffer_linger_ms": 3000,
    "sniffer_partial_linger_ms": 2000,
    "sniffer_linger_max_ms": 12000,
}


def radio_label(port):
    """Short name for a radio in logs, the waterfall and Messages."""
    kind, target = parse_device_selection(port)
    if kind == "usb_radio":
        return f"USB {target[-4:]}" if target else "USB"
    return Path(port).name


def open_radio_port(port):
    """The XIAO firmware over USB serial, or -- for a CH341 USB LoRa stick,
    which has no firmware -- the same protocol served by core/usb_sniffer.py
    running the radio from this machine."""
    kind, target = parse_device_selection(port)
    if kind == "usb_radio":
        from .usb_sniffer import UsbRadioPort
        return UsbRadioPort(target or None)
    try:
        # exclusive: a second program opening the same sniffer gets a clear
        # "busy" error instead of both silently stealing each other's data.
        return serial.Serial(port, SERIAL_BAUD, timeout=0.2, exclusive=True)
    except serial.SerialException as exc:
        if "lock" in str(exc).lower():
            raise OSError(f"{port} is in use by another program (another MeshHunter?)") from exc
        raise


class _RadioFailed(Exception):
    pass


class _RadioLink:
    """One radio: open it, configure its share of the profiles, and pass
    every event line to the worker's queue as (link, msg)."""

    def __init__(self, worker, port):
        self.worker = worker
        self.port = port
        self.label = radio_label(port)
        self.assigned = []  # [(global_id, Profile)]
        self.reconfigure = threading.Event()
        self.ser = None
        # Shown in the UI's radio status chips (see SnifferWorker._radios_changed).
        self.state = "connecting"  # connecting | listening | idle | retrying | failed | stopped
        self.detail = ""
        self.kind = "USB stick" if parse_device_selection(port)[0] == "usb_radio" else "XIAO"
        self.noise = None
        self.thread = threading.Thread(target=self._run, name=f"sniffer-{self.label}", daemon=True)

    def _post(self, msg):
        self.worker.events.put((self, msg))

    def _run(self):
        w = self.worker
        while not w.stop_event.is_set():
            try:
                self._session()
            except _RadioFailed as exc:
                self._post({"ev": "_failed", "msg": str(exc)})
            except (serial.SerialException, OSError) as exc:
                if not w.stop_event.is_set():
                    self._post({"ev": "_failed", "msg": f"connection lost: {exc}", "lost": True})
            finally:
                self._close()
            if not w.multi or w.stop_event.wait(RADIO_RETRY_S):
                break
        self._post({"ev": "_exit"})

    def _close(self):
        if self.ser is None:
            return
        try:
            self._send("STOP")
        except (serial.SerialException, OSError):
            pass
        try:
            self.ser.close()
        except (serial.SerialException, OSError):
            pass
        self.ser = None

    def _send(self, line):
        self.ser.write((line + "\n").encode())
        self.ser.flush()

    def _read_msg(self):
        line = self.ser.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _session(self):
        try:
            self.ser = open_radio_port(self.port)
        except (serial.SerialException, OSError) as exc:
            raise _RadioFailed(f"could not open: {exc}") from exc
        hello = self._wait_for_hello()
        if hello is None:
            raise _RadioFailed("no response from sniffer firmware")
        if hello.get("radio") != 0:
            raise _RadioFailed(f"radio failed to start (RadioLib code {hello.get('radio')}) -- "
                               "check the LoRa board is seated")
        self._post({"ev": "_hello", "fw": hello.get("fw"), "ver": hello.get("ver")})
        self._configure()
        self._post({"ev": "_connected"})
        while not self.worker.stop_event.is_set():
            if self.reconfigure.is_set():
                self.reconfigure.clear()
                self._configure()
            msg = self._read_msg()
            if msg is None:
                continue
            if msg.get("ev") == "hello" and not msg.get("running") and self.assigned:
                # Firmware restarted under us (e.g. brown-out): set it up again.
                self._post({"ev": "_restarted"})
                self._configure()
                continue
            self._post(msg)

    def _wait_for_hello(self):
        deadline = time.monotonic() + HELLO_TIMEOUT_S
        next_ping = 0.0
        while time.monotonic() < deadline and not self.worker.stop_event.is_set():
            now = time.monotonic()
            if now >= next_ping:
                self._send("HELLO")
                next_ping = now + 1.0
            msg = self._read_msg()
            if msg and msg.get("ev") == "hello":
                return msg
        return None

    def _configure(self):
        timing = {k: int(self.worker.config.get(k, v)) for k, v in DEFAULT_TIMING.items()}
        assigned = list(self.assigned)
        self._send("STOP")
        self._send("CLR")
        if not assigned:
            self._post({"ev": "_configured", "profiles": [], "timing": timing})
            return
        for gid, p in assigned:
            self._send(p.to_command(gid))
        self._send(
            f"T {timing['sniffer_dwell_ms']} {timing['sniffer_linger_ms']} "
            f"{timing['sniffer_partial_linger_ms']} {timing['sniffer_linger_max_ms']}"
        )
        self._send("GO")
        self._await_go()
        self._post({"ev": "_configured", "profiles": [p for _, p in assigned], "timing": timing})

    def _await_go(self, timeout=2.0):
        """Consume replies up to the GO acknowledgement, so idle hello lines
        the firmware queued before it started aren't mistaken afterwards for
        a firmware restart."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._read_msg()
            if not msg:
                continue
            if msg.get("ev") == "err":
                self._post(msg)
            elif msg.get("ev") == "ok" and msg.get("cmd") == "GO":
                return True
        self._post({"ev": "err", "msg": "did not acknowledge GO", "code": 0})
        return False


class SnifferWorker:
    is_sniffer = True

    def __init__(self, ports, ui_queue, config, gps_state=None):
        """ports: one device ("/dev/ttyACM1", "USB LoRa: <serial>") or a list."""
        self.ports = [ports] if isinstance(ports, str) else list(ports)
        self.port = ", ".join(self.ports)
        self.multi = len(self.ports) > 1
        self.ui_queue = ui_queue
        self.gps_state = gps_state
        self._apply_settings(config)
        self.config = config
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self._thread = None
        self.links = [_RadioLink(self, p) for p in self.ports]
        self.profiles = []  # global id -> (Profile, link)
        self._assign_lock = threading.Lock()
        self.known_repeaters = {}
        self.known_extra_nodes = {t: {} for t in EXTRA_NODE_TYPES}
        self.known_all_contacts = {}
        self.meshtastic_nodes = {}
        self._meshtastic_dirty = False
        self._meshtastic_last_save = 0.0
        self._heard_meshcore = {}  # adv_type -> set(names), incl. GPS-less nodes
        self._last_fail = {}
        self._raw_log = None
        self._rs_values = {}
        self._rs_cur = {}
        self._rs_last = 0.0

    # ---- MeshCoreWorker-compatible interface ------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._run, name="sniffer", daemon=True)
        self._thread.start()

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def stop(self):
        self.stop_event.set()

    def apply_config(self, config):
        self._apply_settings(config)
        self.config = config
        self._assign()
        for link in self.links:
            link.reconfigure.set()

    def clear_all_contacts(self):
        self.ui_queue.put(("log", "-- Not available: the sniffer has no contact table --"))

    def send_advert(self, flood):
        self.ui_queue.put(("log", "-- Not available: the sniffer firmware is receive-only --"))

    # ---- internals --------------------------------------------------------

    def _apply_settings(self, config):
        self.api_key = config.get("api_key", "")
        self.auto_upload = config.get("auto_upload", False)
        self.ingest_url = config.get("ingest_url", "")
        self.ingest_api_key = config.get("ingest_api_key", "")
        self.ingest_auto_send = config.get("ingest_auto_send", False)
        self.batch_uploads = config.get("batch_uploads", False)
        self.mc_channels = default_meshcore_channels(config)
        self.mt_channels = default_meshtastic_channels(
            [p.channel_name for p in BUILTIN_PROFILES if p.channel_name], config
        )

    def _log(self, text):
        self.ui_queue.put(("log", text))

    def _event_log(self, event, text):
        self._log(f"[{time.strftime('%H:%M:%S')}] {event}: {text}")

    def _assign(self):
        """Share the profiles out between the radios, giving every profile a
        global id (its waterfall column), grouped by radio."""
        with self._assign_lock:
            shares = assign_profiles(self.config, len(self.links))
            profiles = []
            for link, share in zip(self.links, shares):
                link.assigned = [(len(profiles) + i, p) for i, p in enumerate(share)]
                profiles += [(p, link) for p in share]
            self.profiles = profiles
        if self._thread is not None:
            self._radios_changed()
        self.ui_queue.put(("sniffer_profiles", [
            {"name": p.name, "freq": p.freq, "bw": p.bw, "sf": p.sf, "radio": link.label if self.multi else ""}
            for p, link in profiles
        ]))

    def _run(self):
        if not self.links:
            self.ui_queue.put(("error", "No LoRa sniffers found (plug one in, or pick a device)"))
            return
        self._load_tables()
        self._open_raw_log()
        self._assign()
        if self.multi:
            self._log(f"-- Using {len(self.links)} radios: {', '.join(link.label for link in self.links)} --")
        for link in self.links:
            link.thread.start()
        self._radios_changed()
        fatal = None
        connected = False
        seen = set()  # radios that have connected at least once
        alive = len(self.links)
        try:
            while alive:
                try:
                    link, msg = self.events.get(timeout=0.2)
                except queue.Empty:
                    self._flush_rssi()
                    self._save_meshtastic()
                    continue
                ev = msg.get("ev")
                if ev == "_exit":
                    alive -= 1
                    self._set_radio(link, "stopped" if self.stop_event.is_set() else link.state)
                elif ev == "_failed":
                    self._set_radio(link, "retrying" if self.multi else "failed", msg["msg"])
                    if self.multi:
                        # Say it once; a radio that stays unplugged would
                        # otherwise repeat the same line every retry.
                        if self._last_fail.get(link.label) != msg["msg"]:
                            self._last_fail[link.label] = msg["msg"]
                            self._log(f"-- Radio {link.label}: {msg['msg']}; retrying every {RADIO_RETRY_S:.0f} s --")
                    elif msg.get("lost"):
                        fatal = f"Sniffer {msg['msg']}"
                    else:
                        fatal = f"Sniffer on {link.port}: {msg['msg']}"
                elif ev == "_hello":
                    self._log(f"-- {self._who(link)}firmware {msg.get('fw')} v{msg.get('ver')} --")
                elif ev == "_configured":
                    self._log_configured(link, msg["profiles"], msg["timing"])
                    if link.state in ("listening", "idle"):
                        self._set_radio(link, "listening" if msg["profiles"] else "idle")
                elif ev == "_connected":
                    if self.multi:
                        self._log(f"-- Radio {link.label} {'back' if link in seen else 'listening'} --")
                    seen.add(link)
                    self._last_fail.pop(link.label, None)
                    self._set_radio(link, "listening" if link.assigned else "idle")
                    if not connected:
                        connected = True
                        self.ui_queue.put(("status", f"Connected to {self.port} (sniffer)"))
                        self._push_node_counts()
                elif ev == "_restarted":
                    self._log(f"-- {self._who(link)}sniffer restarted; reconfiguring --")
                elif ev == "pkt":
                    self._on_packet(msg, link)
                elif ev == "partial":
                    self._on_partial(msg, link)
                elif ev == "rs":
                    self._on_rssi(msg, link)
                elif ev == "stat":
                    nf = msg.get("nf")
                    if nf is not None and nf > -127 and (link.noise is None or round(nf) != round(link.noise)):
                        link.noise = nf
                        self._radios_changed()
                    self._on_stat(msg, link)
                elif ev == "err":
                    self._log(f"-- {self._who(link)}sniffer error: {msg.get('msg')} ({msg.get('code')}) --")
                self._flush_rssi()
                self._save_meshtastic()
        finally:
            self.stop_event.set()
            self._save_meshtastic(force=True)
            if self._raw_log is not None:
                self._raw_log.close()
                self._raw_log = None
        if fatal and not self.multi:
            self.ui_queue.put(("error", fatal))
        else:
            self.ui_queue.put(("status", "Disconnected"))

    def _set_radio(self, link, state, detail=""):
        link.state, link.detail = state, detail
        if state not in ("listening", "idle"):
            link.noise = None
        self._radios_changed()

    def _radios_changed(self):
        """Per-radio status for the UI: which radios exist, whether each is
        listening, and to what."""
        radios = []
        for link in self.links:
            radios.append({
                "label": link.label,
                "kind": link.kind,
                "port": link.port,
                "state": link.state,
                "detail": link.detail,
                "noise": link.noise,
                "profiles": [p.name for _, p in link.assigned],
            })
        self.ui_queue.put(("sniffer_radios", radios))

    def _who(self, link):
        return f"{link.label}: " if self.multi else ""

    def _log_configured(self, link, profiles, timing):
        who = f"Sniffer {link.label}" if self.multi else "Sniffer"
        if not profiles:
            self._log(f"-- {who}: idle (more radios than profiles to listen on) --")
        elif len(profiles) == 1:
            self._log(f"-- {who}: fixed on {profiles[0].label()} --")
        else:
            dwell = timing["sniffer_dwell_ms"]
            dwells = ", ".join(f"{p.name} {dwell or p.auto_dwell_ms()} ms" for p in profiles)
            self._log(f"-- {who}: hopping {len(profiles)} profiles ({dwells}); "
                      f"linger {timing['sniffer_linger_ms']} ms after a packet, "
                      f"{timing['sniffer_partial_linger_ms']} ms after a partial --")

    # ---- packet handling --------------------------------------------------

    def _profile(self, msg):
        idx = msg.get("p", 0)
        profiles = self.profiles
        return profiles[idx][0] if isinstance(idx, int) and 0 <= idx < len(profiles) else None

    def _on_rssi(self, msg, link):
        """Each radio reports on its own clock; merge them into one report
        per RSSI_MERGE_S so the waterfall gets one row per window."""
        for pid, dbm in msg.get("v", []):
            if dbm is not None and (pid not in self._rs_values or dbm > self._rs_values[pid]):
                self._rs_values[pid] = dbm
        if msg.get("cur") is not None:
            self._rs_cur[link.label] = msg["cur"]

    def _flush_rssi(self):
        now = time.monotonic()
        if now - self._rs_last < RSSI_MERGE_S or not (self._rs_values or self._rs_cur):
            return
        self._rs_last = now
        self.ui_queue.put(("sniffer_rssi", {
            "cur": sorted(self._rs_cur.values()),
            "v": [[pid, dbm] for pid, dbm in sorted(self._rs_values.items())],
        }))
        self._rs_values = {}

    def _on_packet(self, msg, link):
        profile = self._profile(msg)
        pname = profile.name if profile else f"p{msg.get('p')}"
        try:
            raw = bytes.fromhex(msg.get("hex", ""))
        except ValueError:
            return
        rssi, snr = msg.get("rssi"), msg.get("snr")
        self.ui_queue.put(("sniffer_event",
                           {"p": msg.get("p"), "kind": "ok" if msg.get("crc") else "crc", "rssi": rssi}))
        record = {
            "t": datetime.now().isoformat(timespec="milliseconds"),
            "radio": link.label,
            "profile": profile.key if profile else None,
            "freq": msg.get("f"), "bw": msg.get("bw"), "sf": msg.get("sf"), "cr": msg.get("cr"),
            "sync": msg.get("sw"), "rssi": rssi, "snr": snr, "crc_ok": bool(msg.get("crc")),
            "hex": raw.hex(),
        }
        fix = self.gps_state.fix if self.gps_state else None
        if fix:
            record["logger_lat"], record["logger_lon"] = fix
        who = f"radio={link.label} " if self.multi else ""

        if not msg.get("crc"):
            self._write_raw(record)
            self._event_log("RX_BAD_CRC", f"{who}prof={pname!r} rssi={rssi} snr={snr} len={len(raw)}")
            return

        source = link.label if self.multi else "sniffer"
        sync = msg.get("sw")
        if sync == SYNC_MESHCORE:
            d = decode_meshcore(raw, self.mc_channels)
            d["rssi"], d["snr"] = rssi, snr
            record["decoded"] = _jsonable(d)
            self._write_raw(record)
            self._event_log("MC_RX", who + format_rx_log(d) + ("" if "error" not in d else f" error={d['error']!r}"))
            self._on_meshcore(d, rssi)
            self.ui_queue.put(("message", meshcore_message(d, source, profile=pname)))
        elif sync == SYNC_MESHTASTIC:
            d = decode_meshtastic(raw, self.mt_channels)
            record["decoded"] = _jsonable(d)
            self._write_raw(record)
            self._event_log("MT_RX", f"{who}snr={snr} rssi={rssi} " + format_meshtastic(d))
            self._on_meshtastic(d, msg, profile)
            m = meshtastic_message(
                d, rssi, snr, pname, {k: v.get("long_name") for k, v in self.meshtastic_nodes.items() if v.get("long_name")},
            )
            m["source"] = source
            self.ui_queue.put(("message", m))
        else:
            self._write_raw(record)
            self._event_log("LORA_RX", f"{who}prof={pname!r} sync=0x{sync:02x} rssi={rssi} snr={snr} len={len(raw)}")

    def _on_partial(self, msg, link):
        profile = self._profile(msg)
        pname = profile.name if profile else f"p{msg.get('p')}"
        self.ui_queue.put(("sniffer_event",
                           {"p": msg.get("p"), "kind": "partial", "rssi": msg.get("rssi")}))
        who = f"radio={link.label} " if self.multi else ""
        self._event_log("RX_PARTIAL", f"{who}kind={msg.get('kind')} prof={pname!r} rssi={msg.get('rssi')}")

    def _on_stat(self, msg, link):
        """Radio telemetry (noise floor, per-profile RX counters, hop count),
        sent ~every 5 s. These used to be a SNIFF_STAT line in the terminal;
        now they feed the GUI's live stats strip and noise-floor graph via a
        sniffer_stat event instead of scrolling the log."""
        profiles = []
        ok_total = bad_total = pre_total = 0
        for pid, ok, crc, hdr, tmo, pre, listen_ms in msg.get("profiles", []):
            prof = self.profiles[pid][0] if 0 <= pid < len(self.profiles) else None
            bad = crc + hdr + tmo
            ok_total += ok
            bad_total += bad
            pre_total += pre
            profiles.append({
                "name": prof.name if prof else f"p{pid}",
                "ok": ok, "bad": bad, "pre": pre, "listen_s": listen_ms // 1000,
            })
        # Until the radio has a real noise floor it reports a sentinel
        # (<= -127); gate it so the graph/stats stay blank rather than
        # plotting that bogus pre-measurement value.
        nf = msg.get("nf")
        noise = nf if nf is not None and nf > -127 else None
        self.ui_queue.put(("sniffer_stat", {
            "radio": link.label,
            "multi": self.multi,
            "noise": noise,
            "hops": msg.get("hops"),
            "ok": ok_total, "bad": bad_total, "pre": pre_total,
            "profiles": profiles,
        }))

    def _on_meshcore(self, d, rssi):
        if d.get("payload_typename") != "ADVERT" or "adv_key" not in d:
            return
        if not d.get("sig_ok"):
            self._event_log("WARNING", f"advert from {d['adv_key'][:8]} failed signature check; ignored")
            return
        adv_type = d.get("adv_type")
        contact = {
            "public_key": d["adv_key"],
            "type": adv_type,
            "adv_name": d.get("adv_name") or "",
            "adv_lat": d.get("adv_lat", 0.0),
            "adv_lon": d.get("adv_lon", 0.0),
            "last_advert": d.get("adv_timestamp", ""),
            "out_path": d.get("path", ""),
            "rssi": rssi,
        }
        name = contact["adv_name"] or derive_node_id(contact["public_key"])
        self._heard_meshcore.setdefault(adv_type, set()).add(name)
        has_coords = bool(contact["adv_lat"]) or bool(contact["adv_lon"])
        if has_coords:
            record_all_contacts(self.known_all_contacts, [contact], ALL_CONTACTS_CSV_PATH)
            if adv_type == REPEATER_TYPE:
                self._record(contact, self.known_repeaters, REPEATERS_CSV_PATH, WDGWARS_NODE_TYPE, "repeater")
            elif adv_type in EXTRA_NODE_TYPES:
                csv_path, label, log_label = EXTRA_NODE_TYPES[adv_type]
                self._record(contact, self.known_extra_nodes[adv_type], csv_path, label, log_label)
        self._push_node_counts()

    def _record(self, contact, known, csv_path, label, log_label):
        new_ids = persist_nodes(self, [contact], known, csv_path, label)
        if new_ids:
            type_name = ADV_TYPE_NAMES.get(contact["type"], contact["type"])
            self._log(f"-- New {type_name}: {contact['adv_name']!r} --")
        upload_new_nodes(self, {nid: known[nid] for nid in new_ids}, log_label)

    def _on_meshtastic(self, d, msg, profile):
        node_id = d.get("from")
        if not node_id or d.get("from_num") in (0, 0xFFFFFFFF):
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        is_new = node_id not in self.meshtastic_nodes
        fix = self.gps_state.fix if self.gps_state else None
        row = self.meshtastic_nodes.setdefault(node_id, {
            "node_id": node_id,
            "first_seen": now,
            "packets": 0,
            "logger_lat": fix[0] if fix else "",
            "logger_lon": fix[1] if fix else "",
        })
        row["packets"] = int(row.get("packets") or 0) + 1
        row["last_heard"] = now
        row["profile"] = profile.key if profile else ""
        # Signal/hop data only describes *this* node when we heard it
        # directly (0 hops); a relayed packet's RSSI is the relayer's.
        if d.get("hops_away") == 0 or "hops_away" not in d:
            row["rssi"] = msg.get("rssi")
            row["snr"] = msg.get("snr")
        if "hops_away" in d:
            row["hops_away"] = d["hops_away"]
        for key in ("long_name", "short_name", "hw_model", "role", "public_key", "lat", "lon", "alt"):
            if d.get(key) not in (None, ""):
                row[key] = d[key]
        self._meshtastic_dirty = True
        if is_new:
            self._log(f"-- New Meshtastic node: {node_id} --")
            self._save_meshtastic(force=True)
            self._push_node_counts()

    # ---- persistence ------------------------------------------------------

    def _load_tables(self):
        self.known_repeaters = load_known_nodes(REPEATERS_CSV_PATH)
        self.known_all_contacts = load_all_contacts(ALL_CONTACTS_CSV_PATH)
        for node_type, (csv_path, _, _) in EXTRA_NODE_TYPES.items():
            self.known_extra_nodes[node_type] = load_known_nodes(csv_path)
        self.meshtastic_nodes = load_meshtastic_nodes(MESHTASTIC_NODES_CSV_PATH)
        self._log(f"-- Logging Meshtastic nodes to {MESHTASTIC_NODES_CSV_PATH} --")
        self._log(f"-- Logging raw packets to {SNIFFER_PACKETS_PATH} --")

    def _open_raw_log(self):
        try:
            SNIFFER_PACKETS_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._raw_log = SNIFFER_PACKETS_PATH.open("a", encoding="utf-8")
        except OSError as exc:
            self._log(f"-- Could not open raw packet log: {exc} --")

    def _write_raw(self, record):
        if self._raw_log is None:
            return
        try:
            self._raw_log.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._raw_log.flush()
        except OSError:
            pass

    def _save_meshtastic(self, force=False):
        if not self._meshtastic_dirty:
            return
        now = time.monotonic()
        if not force and now - self._meshtastic_last_save < MESHTASTIC_SAVE_INTERVAL_S:
            return
        try:
            save_meshtastic_nodes(self.meshtastic_nodes, MESHTASTIC_NODES_CSV_PATH)
        except OSError as exc:
            self._log(f"-- Could not save {MESHTASTIC_NODES_CSV_PATH.name}: {exc} --")
        self._meshtastic_dirty = False
        self._meshtastic_last_save = now

    def _push_node_counts(self):
        counts = {}
        for node_type, _ in NODE_DISPLAY_TYPES:
            known = self.known_repeaters if node_type == REPEATER_TYPE else self.known_extra_nodes[node_type]
            names = set(node_names(known)) | self._heard_meshcore.get(node_type, set())
            counts[node_type] = len(names)
        counts[MESHTASTIC_COUNT_KEY] = len(self.meshtastic_nodes)
        self.ui_queue.put(("nodes", counts))


def _jsonable(d):
    return {k: v for k, v in d.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}

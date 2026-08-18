<p align="center">
  <img src="meshhunter/gui_qt/assets/logo.png" alt="MeshHunter" width="360">
</p>

# MeshHunter

A PySide6 desktop app for logging [MeshCore](https://meshcore.co.uk/) mesh
network nodes. Connect to a MeshCore device over serial or BLE, and
MeshHunter records every node it hears — repeaters, chat/companion nodes,
rooms, and sensors — with GPS coordinates, and can auto-upload discoveries
to [WDGWars](https://wdgwars.pl) and/or a self-hosted ingest API.

## Features

- Connect to a MeshCore device over **serial or BLE**
- Logs **all node types** (repeater, chat, room, sensor) that advertise GPS coordinates, each to its own CSV
- Optional GPS receiver support for tagging *where you were* when a node was heard
- Auto-upload to **WDGWars** and/or a self-hosted **wardriver ingest API**, immediate or batched
- Send 0-hop/flood adverts and clear the device's stored contacts from the GUI
- Full serial terminal log, saved to a session log file per connection

## Requirements

- Python 3.11+ (developed against 3.13)
- A MeshCore-compatible device reachable over serial or BLE

## Setup

```bash
git clone https://github.com/Dagamant/MeshHunter.git
cd MeshHunter
./run.sh
```

`run.sh` creates a `venv/`, installs `requirements.txt` into it, and launches
the app. On later runs it just launches — re-run `pip install -r
requirements.txt` yourself after pulling changes that touch dependencies.

## Configuration

Copy `example-config.json` to `config.json` in the project root (or edit the
fields from the Settings dialog once the app is running — it writes back to
the same file):

```json
{
  "serial_device": "",
  "api_key": "",
  "auto_upload": false,
  "auto_clear_contacts": false,
  "batch_uploads": false
}
```

| Field | Meaning |
|---|---|
| `serial_device` | Last-selected device, in the rail's device picker |
| `api_key` | Your WDGWars API key (from `wdgwars.pl/profile`) |
| `auto_upload` | Upload newly-logged nodes to WDGWars as they're heard |
| `auto_clear_contacts` | Remove a node from the device's contact table once it's safely logged, to free up space for new ones |
| `batch_uploads` | Defer uploads to the "Send pending batch" button instead of sending immediately |

Two power-user sections only appear once you've added their keys to
`config.json` at least once:

| Field | Meaning |
|---|---|
| `gps_port`, `gps_enabled` | Serial GPS receiver for logger-location tagging |
| `ingest_url`, `ingest_api_key`, `ingest_auto_send` | A self-hosted ingest API (see `POST /endpoint/upload/`-style ingest, not WDGWars) |

`config.json` is gitignored — it holds your real API keys, never commit it.

## Usage

1. Launch the app (`./run.sh`), pick your device from the CONNECTION panel in
   the left rail, and hit **Connect**.
2. Discovered nodes are written to CSVs in the app's per-OS data directory
   (`repeaters.csv`, `chat_nodes.csv`, `room_nodes.csv`, `sensor_nodes.csv`,
   plus `all_contacts.csv` for every contact regardless of GPS status).
3. With `auto_upload` enabled and an API key set, newly-logged nodes upload
   to WDGWars automatically. Otherwise, use **Send pending batch** to send
   whatever's queued up.
4. Open **Settings** for GPS, endpoint, and automation configuration.

## Data sent over each API

Both endpoints only ever receive **node data you've logged** (id, type,
name, position, signal strength, timestamps) — never your config file,
device identity, or anything from the serial terminal log.

### WDGWars (`POST https://wdgwars.pl/api/upload/`)

Fixed endpoint, not configurable. Per node: `node_id`, `node_type`,
`name`, `lat`, `lon`, `rssi`, `first_seen`, `type` (`"MESHCORE"`),
`network` (`"meshcore"`), and `public_key` when known. Records are
wrapped in a signed envelope — `{"data": <base64 JSON>, "nonce": ...,
"sig": HMAC-SHA256(nonce + data, your API key)}` — sent alongside an
`X-API-Key` header. Your API key is used as the HMAC secret and is also
sent directly in the header on every request.

### Self-hosted ingest API (your own `ingest_url`)

Only sent if you've configured one — off by default. Per node:
`public_key` and `node_type` (required; only `REPEATER`/`CHAT`/`ROOM` are
sent, `SENSOR` rows are skipped), plus whichever of `node_id`, `name`,
`lat`, `lon`, `rssi`, `first_seen`, `last_heard`, `network`, `type` are
known. Also includes `logger_lat`/`logger_lon` — *your* position (from
the configured GPS receiver) when you first logged that node, not the
node's own advertised position — which isn't part of the standard
ingest schema, so a strict server-side validator may need to allow it.
Sent as a plain JSON array with an `X-API-Key` header (no HMAC signing).

## Device modifications

MeshHunter mostly reads from a connected device, but a few actions do
change what's stored on it:

- **Removing contacts.** The **"Clear device contacts"** button removes
  *every* contact (any type, including chat/companion contacts) from the
  device's stored contact table. If `auto_clear_contacts` is enabled in
  Settings (off by default), each node is also removed individually right
  after MeshHunter logs it to CSV — this keeps the device's (limited)
  contact table free for new discoveries, but means a node won't show up
  in the device's own contact list again until it's heard once more.
- **Draining pending messages.** On every connect, MeshHunter
  automatically fetches any messages waiting on the device, in order to
  decode and display them in the terminal log. This is not optional/
  configurable. Fetching a message removes it from the device's queue —
  if you also use another companion app (a phone app, `meshcore-cli`,
  etc.) against the same device, whichever client fetches a message first
  is the one that receives it.
- **Sending adverts.** The **0-hop** / **Flood** buttons cause the device
  to actually **transmit an advertisement packet over the air**, the same
  as pressing the advertise button on the device itself — a Flood advert
  is rebroadcast by other repeaters that hear it, using real mesh airtime.
  Manual only, never automatic.
- **Forcing "auto-add contacts" on.** MeshHunter only ever learns about a
  node via the device's own contact table (`NEW_CONTACT` pushes and
  `get_contacts()` syncs) — it never adds contacts itself. If your
  device's auto-add-contacts setting is off, it silently never adds
  anything new to discover, so MeshHunter forces this setting **on** on
  every connect. This is not optional/configurable, and it persists on
  the device after you disconnect, like any other device setting change.

Beyond what's listed above, MeshHunter never adds new contacts, changes
other device settings (name, radio parameters, channel keys), or sends
chat/channel messages on your behalf.

## Project layout

```
main.py                   entry point
meshhunter/core/          MeshCore connection, uploads, config, storage
meshhunter/gui_qt/        PySide6 GUI (main window, rail widgets, theme)
```

## License

[BSD Zero Clause License](LICENSE) — do whatever you want with this, no
attribution required.

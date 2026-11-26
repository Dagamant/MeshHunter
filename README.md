<p align="center">
  <img src="meshhunter/gui_qt/assets/logo.png" alt="MeshHunter" width="360">
</p>

# MeshHunter

<p align="center">
  <a href="https://ko-fi.com/dagamant">
    <img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi">
  </a>
</p>

MeshHunter logs the [MeshCore](https://meshcore.co.uk/) and Meshtastic nodes
around you: repeaters, chat/companion nodes, rooms and sensors, together
with where you were when you heard them. It can auto-upload what it finds to
[WDGWars](https://wdgwars.pl) and/or a self-hosted ingest API.

It runs as a PySide6 desktop app, a compact touch layout for small screens,
or a terminal app for headless boxes, and there's a separate receive-only
port for the [CardputerZero](cardputer-zero/README.md).

**Contents:** [Radios it works with](#radios-it-works-with) ·
[Install](#install) · [Using the app](#using-the-app) ·
[LoRa sniffer](#lora-sniffer) · [LoRa HAT](#lora-hat-raspberry-pi) ·
[RF sensors](#rf-sensors-rtl-sdr--rtl_433) ·
[MeshCore channels](#meshcore-channels) · [Configuration](#configuration) ·
[Your data](#your-data) · [Uploads](#uploads) ·
[Changes to your device](#changes-made-to-a-connected-device) ·
[Development](#development)

## Radios it works with

| Radio | Connects over | What you get |
|---|---|---|
| MeshCore companion device | Serial or BLE | Nodes from the device's contact table and RX log, Public/hashtag channel messages, adverts you can send |
| [LoRa sniffer](#lora-sniffer): Seeed XIAO nRF52840 + Wio-SX1262 with the MeshHunter firmware, or a CH341 USB LoRa stick (MeshToad) | USB | Receive-only. MeshCore **and** Meshtastic straight off the air, with RSSI, a live waterfall, and several radios at once |
| [LoRa HAT](#lora-hat-raspberry-pi): HackerGadgets RTL-SDR/LoRa/GPS/RTC HAT on a Raspberry Pi | SPI (Linux only) | Receive-only MeshCore or Meshtastic decoding beside an RTL-SDR waterfall, in a fullscreen touch UI |
| [RTL-SDR dongle](#rf-sensors-rtl-sdr--rtl_433) | USB | A spectrum waterfall, or rtl_433 decoding of meters, weather stations and other sensors |
| Serial GPS receiver (optional) | Serial | Tags every logged node with *your* position |

## Install

Requirements: Python 3.11+ (developed against 3.13).

### Prebuilt downloads

Tagged releases are on the [Releases page](https://github.com/Dagamant/MeshHunter/releases)
with prebuilt downloads: the CardputerZero `.deb`, the sniffer firmware
(`.uf2` / `.hex`) and the Python wheel. See [CHANGELOG.md](CHANGELOG.md) for
what changed.

### From source

```bash
git clone https://github.com/Dagamant/MeshHunter.git
cd MeshHunter
./run.sh        # Windows: run.bat
```

`run.sh` (`run.bat` on Windows) creates a `venv/`, installs `requirements.txt`
into it, and launches the app. On later runs it just launches, so re-run `pip
install -r requirements.txt` yourself after pulling changes that touch
dependencies.

On macOS you can double-click `MeshHunter.command` in Finder instead. On
Linux, a desktop launcher just needs `Exec=/path/to/MeshHunter/run.sh --desktop`.

### With pipx

Instead of a checkout, you can install MeshHunter as a command with
[pipx](https://pipx.pypa.io/) (or `pip install` into a venv of your own):

```bash
# Desktop app + terminal version
pipx install "meshhunter[gui] @ git+https://github.com/Dagamant/MeshHunter"
# Terminal version only (no Qt), e.g. on a headless Pi
pipx install "git+https://github.com/Dagamant/MeshHunter"
```

That gives you two commands:

- `meshhunter`: the desktop app (same flags as `run.sh`: `--desktop`,
  `--pocket`, `--hat`; `--tui` / `--headless` / `--list-devices` hand over to
  the terminal version)
- `meshhunter-tui`: the terminal version (`--headless`, `--device`, ...)

`pipx upgrade meshhunter` pulls the latest `main`; add `@v0.1.0` (any tag) to
the URL to pin a release. Config and data live in the same per-user folders
either way, so a pipx install and a checkout share them. The `[gui]` extra is
what adds PySide6, NumPy and, on Linux, the LoRa HAT's `spidev` / `gpiod`.

## Using the app

### Desktop

1. Launch the app (`./run.sh`), pick your device from the CONNECTION panel in
   the left rail, and hit **Connect**.
2. Discovered nodes are written to CSVs in the [data directory](#your-data):
   one file per node type for nodes that advertise GPS coordinates, plus
   `all_contacts.csv` for every contact regardless of GPS status.
3. With `auto_upload` enabled and an API key set, newly-logged nodes upload
   to WDGWars automatically. Otherwise, use **Send pending to WDGWars** /
   **Send pending to Ingest API** to send whatever's queued up.
4. Open **Settings** for GPS, endpoint, and automation configuration.

The rail also has **0-hop** / **Flood** advert buttons and **Clear device
contacts** (see [Changes made to a connected device](#changes-made-to-a-connected-device)).
The terminal shows the full serial log, which is also saved to a session log
file per connection.

The **Messages** panel under the terminal lists every packet the app can
decode, whether or not it qualifies for logging: chat text, adverts
without GPS, positions, node info, and encrypted traffic it can't read.
Each row is tagged **MeshCore** or **Meshtastic**. Copies of the same
packet relayed by several repeaters are folded into one row with a ×N
count, showing the strongest signal and fewest hops. You can filter by
mesh or channel, or switch on **Text only**. A companion only contributes MeshCore
traffic (its RX log plus direct messages to you); Meshtastic needs the
[LoRa sniffer](#lora-sniffer).

### Pocket mode (640×480)

```bash
./run.sh --pocket        # or: venv/bin/python main.py --pocket
```

A compact, touch-friendly layout for small fixed displays (the
"PocketTerm35" target), instead of the normal resizable desktop window.
Same connection/upload/settings logic as the desktop app; only the
layout changes: a slim top bar (connection status, LIVE indicator,
Settings) plus tabs sized for a finger instead of a mouse:

- **Home**: device picker, Connect/Disconnect, and node counts (the
  first thing you see)
- **Terminal**: the same serial log as the desktop view
- **Messages**: the decoded message feed (Signal column moves to the
  row tooltip)
- **Actions**: advertise, sync/upload, import contacts, clear device
  contacts
- **Radio**: the sniffer waterfall, while a sniffer is connected

The window is fixed at 640×480 and doesn't resize. One current
limitation: there's no room in the compact top bar for a live GPS fix
readout, so it isn't shown in pocket mode. GPS still works and still
tags logged nodes with your position, it's just not displayed on screen.

### Terminal mode (TUI / headless)

For headless boxes (a Raspberry Pi in a car or on a mast, a server over
SSH) there's a terminal version with the same logging, uploads, sniffer
support and config as the desktop app. It doesn't need Qt:

```bash
./run-tui.sh                               # full-screen TUI (curses)
./run-tui.sh --device /dev/ttyACM0         # ...and connect right away
./run-tui.sh --headless --device /dev/ttyACM0   # no UI: log lines to stdout
./run-tui.sh --list-devices                # serial ports (sniffers are marked)
```

`run-tui.sh` creates the venv from `requirements-cli.txt` (no PySide6) if
there isn't one yet. In an existing full install, `venv/bin/python main.py
--tui` works too, and a pipx install has the same thing as `meshhunter-tui`.

The TUI has three views (keys **1**–**3**):

- **Log**: the serial terminal.
- **Messages**: the decoded message feed, with **t** for text only, **m** to
  filter by mesh and ↑↓ to show a message in full.
- **Radio**: the sniffer waterfall in text, one line per second, packet marks
  across the column.

Other keys: **d** picks a device, **c** connects or disconnects, **s** opens
Settings (same options as the GUI dialog; **S** saves), **a**/**A**
advertise, **x** clears device contacts, **u** sends pending batch
uploads, **i** imports a contacts JSON, **?** lists every key and **q**
quits. It works in an 80×24 terminal and uses 256 colors where the
terminal supports them.

Unlike the GUI, the terminal versions retry every 10 seconds when the
connection fails or drops (`--no-reconnect` turns that off), so a radio
that's unplugged or not ready at boot gets picked up once it appears.
Headless mode is also what you get when stdout isn't a terminal.

To run at boot, see `contrib/meshhunter.service` (a systemd unit; watch it
with `journalctl -u meshhunter -f`). Use a `/dev/serial/by-id/...` path
for `--device` so it survives USB ports being renumbered. Configure it
once with the TUI as the same user, since config and data are per user.

### CardputerZero

[`cardputer-zero/`](cardputer-zero/README.md) is a separate receive-only
C++/LVGL port for the CardputerZero with the Cap LoRa-1262 hat and/or a XIAO
sniffer. It writes the same CSV layout as the desktop app, so you can copy
its logs over and upload them from here. Its own README covers building,
installing and using it.

## LoRa sniffer

Instead of a MeshCore companion, MeshHunter can connect to a **Seeed XIAO
nRF52840 + Wio-SX1262** running the receive-only sniffer firmware in
[`firmware/sniffer/`](firmware/sniffer/). The firmware sends every raw
packet it hears to the app, and MeshHunter decodes it.

The app recognizes the sniffer by its USB name ("MeshHunter Sniffer"), so
you pick its port and click Connect as usual.

A CH341-based USB LoRa stick such as the **Nullhop MeshToad** works as a
sniffer too, with no firmware to flash: it appears as `USB LoRa: <serial>`
in the device list and needs a udev rule on Linux (see
`firmware/sniffer/README.md`).

### What's decoded

- **MeshCore**: adverts (public key, name, type, location; the signature is
  checked) go into the same repeater/chat/room/sensor CSVs and uploads as
  companion-reported nodes, now with RSSI. Public and `#hashtag` channel
  messages are decrypted. Packet paths show which repeaters relayed them.
- **Meshtastic**: header info (sender, hops, relay node) for every packet.
  On default-key channels it also decodes NodeInfo (names, hardware, role),
  Position, NeighborInfo, MapReport, Traceroute and text. Results go to
  `meshtastic_nodes.csv`, which is kept locally and not uploaded.
- Every packet, raw and decoded, is appended to `sniffer_packets.jsonl` so
  it can be re-decoded later.

### Fixed and hop

**Settings → LoRa sniffer** chooses what it listens to:

- **Fixed**: one profile (frequency, bandwidth, SF, sync word), listening
  continuously. Catches everything on that profile and nothing else.
- **Hop**: cycles through the ticked profiles. *Listen per profile* (Auto
  = about 10 LoRa symbols: 21 ms for MeshCore US, 82 ms for LongFast) is
  long enough to detect a preamble that's already in progress. When it
  detects one, it stays until the packet ends. After a good packet it
  stays for *Stay after a packet* (default 3 s, extended by each new
  packet up to *Max stay*) to catch mesh rebroadcasts. After a partial (a
  header or payload CRC error, or a timeout mid-packet) it stays for *Stay
  after a partial* (default 2 s) in case it's repeated. The dialog shows
  the hop cycle length and an estimate of how often a first transmission
  is caught on each profile.

A single SX1262 can only receive one profile at a time, so hop mode trades
completeness for coverage. To capture everything on several networks, add
radios until each has one profile (see below).

Extra radio profiles and channel keys can be added in `config.json`
(`sniffer_custom_profiles`, `sniffer_meshtastic_channels`; MeshCore channels
are in Settings, see [MeshCore channels](#meshcore-channels)); see
`firmware/sniffer/README.md`.

### Waterfall and stats

While a sniffer is connected, a **Radio** waterfall appears below the
terminal (in pocket mode it's a Radio tab). It has one column per profile,
and the newest rows are at the top, 5 rows per second. Each cell's
brightness shows the strongest signal above that column's noise floor.
Received packets are marked white, bad-CRC packets amber, and partials red.
The highlighted column is the one being listened to right now. The SX1262
can't do an FFT, so this shows signal strength per listening frequency, not
a full-band spectrum.

Beside the waterfall, a **noise-floor graph** tracks each radio's measured
noise floor (dBm) over the last ~10 minutes, one line per radio. Each
received packet is plotted on it as a dot at its own RSSI (white good, amber
bad-CRC, red partial), so the gap up from the noise line shows how far above
the floor it landed.

The radio also reports telemetry (noise floor, RX counts, errors, preambles,
hop count) about every 5 seconds. Rather than scrolling past in the terminal,
this shows as a live **sniffer stats** strip between the terminal and the
Messages panel (in the TUI's Radio view it's a summary line).

### Several radios at once

With two or more sniffer radios plugged in (any mix of XIAOs and USB
sticks), the device list gains **All LoRa sniffers**, which runs them
together as one device. The selected profiles are shared out between the
radios:

- In Hop mode the hop list is split across them (a radio left with one
  profile just stays on it).
- In Fixed mode the first radio takes the fixed profile and each other radio
  the next profile from the hop list.

For example, with MeshCore, LongFast and MediumFast ticked, the first radio
hops MeshCore and MediumFast while the second stays on LongFast. To choose
what one radio listens to, click its chip in the top bar and pick a profile;
the other radios share the rest (pick "Automatic" to undo).

Everything lands in one waterfall (column headers name the radio), one
Messages list (the tooltip/detail says which radio heard it) and one set of
node files. If one radio is unplugged, the others carry on and it's picked
up again when it returns.

## LoRa HAT (Raspberry Pi)

With a [HackerGadgets RTL-SDR/LoRa/GPS/RTC HAT](https://hackergadgets.com/products/rtl-sdr-lora-gps-rtc-hat-for-raspberry-pi),
MeshHunter listens directly with the HAT's SX1262 and shows a live RTL-SDR
waterfall of the mesh frequency side by side. Receive only: the radio is
never put into transmit.

### Desktop

Pick **LoRa HAT** under MODE in the rail, choose MeshCore (preset or custom
frequency/SF/BW/CR) or Meshtastic (region + LongFast/MediumFast), and press
**Start listening**. The window splits into decoded packets (top) and the
RTL-SDR waterfall (bottom) with the LoRa channel highlighted. Changing the
network or preset while listening retunes immediately.

### Touch UI

When the HAT is detected (`/dev/spidev1.0` exists), MeshHunter launches a
fullscreen touch UI sized for small 640x480 screens such as the PocketTerm35:
a setup screen with one button each for MeshCore, Meshtastic LongFast and
Meshtastic MediumFast plus logging checkboxes, then a live screen where decoded
packets and the waterfall fill the display (**Options** returns to setup).
A fourth **Custom** button opens a settings screen for your own frequency,
bandwidth, SF, CR and sync word, decoded as MeshCore, Meshtastic (with a
channel name of your choice), or **Packet Capture**: raw hex of every frame,
CRC failures included.

Force the HAT touch UI with `./run.sh --hat`, or skip it with `./run.sh --desktop`
(or `./run.sh --pocket` for the [compact companion layout](#pocket-mode-640480)).

### What's decoded

- **MeshCore**: route/hops, adverts (name, type, position), and Public-channel
  messages (add `"meshcore_channels": ["#yourchannel"]` to `config.json` to also
  decrypt hashtag channels). DMs are end-to-end encrypted and shown as such.
- **Meshtastic**: messages, positions, node info and telemetry on the preset's
  default channel/key. Packets on other channels or PKI DMs are listed as encrypted.

Every packet is appended to `lora_packets.csv` in the data directory together
with your GPS position, and MeshCore adverts with coordinates go into the same
node CSVs as companion mode.

### Raspberry Pi setup

One-time setup (from the HAT's guide; reboot afterwards):

```ini
# /boot/firmware/config.txt
dtoverlay=spi1-1cs   # SX1262 on /dev/spidev1.0
dtparam=uart0=on     # GPS on /dev/ttyAMA0 (Pi 5; /dev/ttyS0 + enable_uart=1 on Pi 4)
```

Then:

- remove `console=serial0,115200` from `/boot/firmware/cmdline.txt`
- blacklist the DVB driver: `echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf`
- install `librtlsdr0`: `sudo apt install rtl-sdr`
- set the GPS port to `/dev/ttyAMA0` in Settings

## RF sensors (RTL-SDR + rtl_433)

With an RTL-SDR plugged in, the RF band appears even without a LoRa device.
Its **Waterfall | Sensors** switch turns the dongle from a spectrum
waterfall into a decoder for the other devices transmitting around you,
using [rtl_433](https://github.com/merbanan/rtl_433).

### Setup

rtl_433 must be installed separately:

- Debian/Ubuntu/Raspberry Pi OS: `sudo apt install rtl-433`
- macOS: `brew install rtl_433`
- Windows: the release zip from GitHub. Set `rtl_433_path` in `config.json`
  if it isn't on your PATH.

MeshHunter detects the dongle through librtlsdr, so that also has to be
findable. Otherwise the RF band acts as if no dongle is plugged in.

- Linux: install `rtl-sdr` (`librtlsdr0`).
- macOS: Homebrew's `rtl_433` pulls in `librtlsdr`, and both `/opt/homebrew`
  and `/usr/local` are searched.
- Windows: put the folder that contains `rtlsdr.dll` (from an rtl-sdr
  release) on your PATH.

### Bands

Sensors mode hops between the frequencies where 902–928 MHz devices
actually transmit, `rf_sensor_dwell_s` (default 20) seconds each:

| Centre | What's decoded there |
|---|---|
| 912.6 MHz | Itron ERT electric/gas/water meters (SCM, SCM+, IDM), Neptune R900 water meters |
| 915.0 MHz | US 915 MHz weather and home sensors: Ecowitt / Fine Offset / Ambient Weather, LaCrosse, F007TH, and others |
| 916.6 MHz | Badger ORION water meters, Honeywell ActivLink doorbells |

**Bands ▾** adds bands outside 33 cm to the cycle. A 915 MHz antenna hears
these less well.

- 315 MHz: US tire-pressure sensors, keyfobs.
- 345 MHz: Honeywell 5800 / 2Gig security sensors.
- 433.92 MHz: AcuRite, Oregon Scientific, Bresser, LaCrosse 433, ThermoPro, and others.

Some devices can't be decoded this way: Davis weather stations, Z-Wave,
LoRaWAN, and Sensus / Landis+Gyr smart meters.

### Results

Each device gets one row in the table: type, model, ID, latest readings,
signal, how often it was heard and when. Hover a row for the raw decode.
History goes to `rf_sensors.csv` (one row per device, persists across
sessions) and `rf_sensors.jsonl` (every raw decode) in the
[data directory](#your-data). Nothing is uploaded.

## MeshCore channels

The app listens to MeshCore group channels on its own: it decrypts them
itself, for both a companion's RX log and the LoRa sniffer, so it doesn't
use up or change the companion's channel slots. Public is always on, and
these widely used hashtag channels are subscribed out of the box: `#test`,
`#testing`, `#bot`, `#hamradio`, `#emergency`.

Manage them in **Settings → MeshCore channels** (TUI: `s`, then the
*MeshCore channels* section). A `#hashtag` channel only needs its name,
because its key is derived from the name. A private channel is added as
`Name <key>` (32 hex characters or base64). Untick a channel to pause it
without forgetting it. They're stored in `config.json` as
`meshcore_channels`, e.g.
`[{"name": "#hamradio"}, {"name": "Club", "key": "00112233445566778899aabbccddeeff"}]`.
Private Meshtastic channel keys go in `sniffer_meshtastic_channels`.

### Finding channels you don't have a key for

A `#hashtag` channel's key is derived from its name alone, so a channel you
have no key for can still be recovered if you can guess the name. **Find
channels** (button in the Messages panel; `f` in the TUI messages view) runs
a dictionary attack: it takes every encrypted group packet you've captured
(the live session plus the on-disk sniffer log), derives a key for each
candidate name, and keeps only the names whose key passes a packet's
built-in MAC check, so a hit is the real channel, not a hash collision.
Recovered channels can be subscribed to in one click.

It only works for `#hashtag` channels: a private channel with a random key
has nothing to guess, so it stays unreadable. The candidate list is common
public-channel names plus US regions, widened with words seen in chat you
can already read. Add your own likely names (town, club, callsign area) to
`channel_wordlist.txt` in the data directory, one per line, to widen the
search.

## Configuration

Settings live in `config.json` in the app's per-OS user config directory:

| OS | Config directory |
|---|---|
| Linux | `~/.config/meshhunter/` |
| macOS | `~/Library/Application Support/meshhunter/` |
| Windows | `%LOCALAPPDATA%\meshhunter\` |

Edit them from the Settings dialog once the app is running (recommended), or
write `config.json` yourself; `example-config.json` shows the available
fields. It's created with `0600` permissions since it holds plaintext API
keys, and it's gitignored: never commit it.

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
| `batch_uploads` | Defer uploads to the "Send pending to WDGWars" / "Send pending to Ingest API" buttons instead of sending immediately |

Two power-user sections only appear once you've added their keys to
`config.json` at least once:

| Field | Meaning |
|---|---|
| `gps_port`, `gps_enabled` | Serial GPS receiver for logger-location tagging |
| `ingest_url`, `ingest_api_key`, `ingest_auto_send` | A self-hosted ingest API (see [Uploads](#self-hosted-ingest-api-your-own-ingest_url)) |

Other keys are covered in their sections: `meshcore_channels`
([MeshCore channels](#meshcore-channels)), `sniffer_custom_profiles` and
`sniffer_meshtastic_channels` ([LoRa sniffer](#lora-sniffer)),
`rtl_433_path` and `rf_sensor_dwell_s` ([RF sensors](#rf-sensors-rtl-sdr--rtl_433)).

## Your data

Everything MeshHunter records goes in its per-OS data directory
(`~/.local/share/meshhunter/` on Linux,
`~/Library/Application Support/meshhunter/` on macOS,
`%LOCALAPPDATA%\meshhunter\` on Windows). A pipx install and a
checkout share it.

| File | What's in it | Written by |
|---|---|---|
| `repeaters.csv`, `chat_nodes.csv`, `room_nodes.csv`, `sensor_nodes.csv` | MeshCore nodes that advertised GPS coordinates, one file per type, with your position when first logged | Companion, sniffer, LoRa HAT |
| `all_contacts.csv` | Every MeshCore contact, with or without GPS | Companion, sniffer |
| `meshtastic_nodes.csv` | Meshtastic nodes heard on default-key channels (kept locally, never uploaded) | Sniffer |
| `sniffer_packets.jsonl` | Every sniffer packet, raw and decoded, for re-decoding later | Sniffer |
| `lora_packets.csv` | Every packet the LoRa HAT heard, with your GPS position | LoRa HAT |
| `rf_sensors.csv`, `rf_sensors.jsonl` | One row per RF sensor device, and every raw rtl_433 decode | RF sensors |
| `logs/` | The serial terminal log, one file per connection | All |
| `channel_wordlist.txt` | Your extra names for [Find channels](#finding-channels-you-dont-have-a-key-for) (you create it) | You |

The CardputerZero port writes the same node CSVs, but to `~/MeshHunter/` on
the device.

## Uploads

Both endpoints only ever receive **node data you've logged** (id, type,
name, position, signal strength, timestamps), never your config file,
device identity, or anything from the serial terminal log. Meshtastic nodes
and RF sensors are never uploaded.

### WDGWars (`POST https://wdgwars.pl/api/upload/`)

Fixed endpoint, not configurable. Per node: `node_id`, `node_type`,
`name`, `lat`, `lon`, `rssi`, `first_seen`, `type` (`"MESHCORE"`),
`network` (`"meshcore"`), and `public_key` when known. Records are
wrapped in a signed envelope, `{"data": <base64 JSON>, "nonce": ...,
"sig": HMAC-SHA256(nonce + data, your API key)}`, sent alongside an
`X-API-Key` header. Your API key is used as the HMAC secret and is also
sent directly in the header on every request.

### Self-hosted ingest API (your own `ingest_url`)

Only sent if you've configured one; off by default. Per node:
`public_key` and `node_type` (required; only `REPEATER`/`CHAT`/`ROOM` are
sent, `SENSOR` rows are skipped), plus whichever of `node_id`, `name`,
`lat`, `lon`, `rssi`, `first_seen`, `last_heard`, `network`, `type` are
known. Also includes `logger_lat`/`logger_lon`: *your* position (from
the configured GPS receiver) when you first logged that node, not the
node's own advertised position. That isn't part of the standard
ingest schema, so a strict server-side validator may need to allow it.
Sent as a plain JSON array with an `X-API-Key` header (no HMAC signing).

## Changes made to a connected device

The sniffer, LoRa HAT and RTL-SDR are receive-only. A MeshCore companion is
mostly only read from, but a few actions do change what's stored on it:

- **Removing contacts.** The **"Clear device contacts"** button removes
  *every* contact (any type, including chat/companion contacts) from the
  device's stored contact table. If `auto_clear_contacts` is enabled in
  Settings (off by default), each node is also removed individually right
  after MeshHunter logs it to CSV. This keeps the device's (limited)
  contact table free for new discoveries, but means a node won't show up
  in the device's own contact list again until it's heard once more.
- **Draining pending messages.** On every connect, MeshHunter
  automatically fetches any messages waiting on the device, in order to
  decode and display them in the terminal log. This is not optional or
  configurable. Fetching a message removes it from the device's queue, so
  if you also use another companion app (a phone app, `meshcore-cli`,
  etc.) against the same device, whichever client fetches a message first
  is the one that receives it.
- **Sending adverts.** The **0-hop** / **Flood** buttons cause the device
  to actually **transmit an advertisement packet over the air**, the same
  as pressing the advertise button on the device itself. A Flood advert
  is rebroadcast by other repeaters that hear it, using real mesh airtime.
  Manual only, never automatic.
- **Forcing "auto-add contacts" on.** MeshHunter only ever learns about a
  node via the device's own contact table (`NEW_CONTACT` pushes and
  `get_contacts()` syncs); it never adds contacts itself. If your
  device's auto-add-contacts setting is off, it silently never adds
  anything new to discover, so MeshHunter forces this setting **on** on
  every connect. This is not optional or configurable, and it persists on
  the device after you disconnect, like any other device setting change.

Beyond what's listed above, MeshHunter never adds new contacts, changes
other device settings (name, radio parameters, channel keys), or sends
chat/channel messages on your behalf.

## Development

### Project layout

```
main.py             entry point
meshhunter/core/    MeshCore connection, sniffer, decoders, uploads, config, storage
meshhunter/gui_qt/  PySide6 GUI (main window, rail widgets, theme)
  pocket_window.py  compact 640x480 layout, launched with --pocket
meshhunter/tui/     terminal front ends: curses TUI (--tui) and --headless
firmware/sniffer/   XIAO nRF52840 + SX1262 LoRa sniffer firmware (PlatformIO)
cardputer-zero/     receive-only C++/LVGL port for the CardputerZero
contrib/            example systemd unit for headless use, udev rule for USB LoRa sticks
tests/              pytest suite (decoders, channels, profiles, rtl_433, GUI smoke)
```

### Tests

```bash
pip install -r requirements.txt pytest
pytest
```

The decoder, channel, profile and rtl_433 tests need only
`requirements-cli.txt`; the GUI tests open each window offscreen and are
skipped when PySide6 isn't installed. GitHub Actions runs them on every push
and pull request (Linux, macOS, Windows), and also builds the CardputerZero
`mesh_tests` and checks its decoder against the desktop one using a capture
from `tests/make_golden.py`.

## License

[BSD Zero Clause License](LICENSE): do whatever you want with this, no
attribution required.

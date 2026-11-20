# MeshHunter for CardputerZero

A receive-only port of MeshHunter for the CardputerZero with the
**Cap LoRa-1262** hat (SX1262 radio + ATGM336H GPS).

It listens on the MeshCore and Meshtastic LoRa presets, verifies MeshCore
adverts (Ed25519), decodes Public / default-channel traffic, and logs every
located node together with where you were when you heard it. The CSVs it
writes use the same layout as the desktop app, so you can copy them over and
upload to WDGWars or a self-hosted ingest API from there.

It never transmits.

Built from the [CardputerZero app template](https://github.com/CardputerZero/template)
(LVGL 9.5, MVVM, keyboard-only input).

## Pages and keys

| Key | Page | |
| --- | --- | --- |
| `4` | Nodes | Nodes heard this session. UP/DOWN select, ENTER shows details |
| `5` | Live | Packet feed: `C` MeshCore, `T` Meshtastic, `!` bad CRC, `?` other |
| `6` | Radio | Profiles, per-profile counts, RSSI. `M` hop/fixed, `R` US/EU868, ENTER toggles a profile |
| `7` | GPS | Fix, satellites, UTC, storage counters |
| `8` | | Light/dark theme |

UP/DOWN also work as `;` `.` or `F` `X`, and LEFT/RIGHT as `,` `/` or `Z` `C`.
`H` opens help on every page, a short ESC closes the details view, and
holding ESC exits.

## Hardware

| | |
| --- | --- |
| SX1262 SPI | `/dev/spidev0.1` (CE1; CE0 is the LCD), mode 0, 1 MHz |
| RESET / BUSY / DIO1 | gpiochip0 lines 26 / 22 / 23 |
| TCXO | 3.0 V on DIO3; DIO2 drives the RF switch |
| Antenna switch | PI4IOE5V6408 at I2C1 `0x43`, P0 high |
| Cap 5 V rail | `/sys/class/leds/ext_5v_out` |
| GPS | `/dev/ttyS0`, 115200 8N1, NMEA GN/GP |

The app user needs to be in the `spi`, `gpio`, `i2c` and `dialout` groups
(the default CardputerZero user already is).

Only one program can drive the radio at a time. Close MeshTerm, Cap LoRa-1262
and Cap LoRa-1262 GPS before starting MeshHunter.

## Data

Everything is written to `~/.local/share/meshhunter/`:

- `repeaters.csv`, `chat_nodes.csv`, `room_nodes.csv`, `sensor_nodes.csv`:
  MeshCore nodes that advertised a location, in the desktop `NODE_CSV_FIELDS`
  format (`logger_lat`/`logger_lon` = your GPS fix when the node was first logged)
- `all_contacts.csv`: every located MeshCore advert, all types
- `meshtastic_nodes.csv`: Meshtastic nodes heard on default channels
- `sniffer_packets.jsonl`: every packet received, raw hex plus the decoded fields
  (turn off with `raw_log=no`)

Adverts without a location are shown on the Nodes page but aren't logged,
same as the desktop app.

## Configuration

Packaged defaults are in `/etc/meshhunter.conf` (see
[`config/meshhunter.conf`](config/meshhunter.conf) for every key). Changes made
in the app (region, hop/fixed, profiles, theme) are saved to
`~/.config/meshhunter/meshhunter.conf`, which is read after the packaged
defaults and overrides them.

Extra channel keys go in `[channels]`: `#hashtag` or `Name=<hex|base64>` for
MeshCore, `Name=<base64 psk>` for Meshtastic.

## Building

You need CMake ≥ 3.31 and Ninja. For the device you also need the aarch64 cross
compiler (`crossbuild-essential-arm64` on Debian/Ubuntu); the BSP sysroot is
downloaded into `.cache/` on the first configure.

```bash
# CardputerZero
cmake --preset cp0-cross
cmake --build --preset cp0-cross-rel
cpack --preset cp0-cross-deb            # -> dist/MeshHunter_<ver>_rev1_arm64.deb
# or all of the above:
cmake --workflow --preset cp0-cross-package

# Install on the device
scp dist/MeshHunter_*_arm64.deb <user>@<cardputer>:
ssh <user>@<cardputer> sudo apt install ./MeshHunter_*_arm64.deb
```

The desktop simulator (SDL2, fmt, freetype, libpng, libjpeg and OpenSSL dev
packages required) can't drive a radio, so replay a capture from the desktop
app or from the device instead:

```bash
cmake --preset linux-x86-64
cmake --build build/linux-x86-64 --config Release
MESHHUNTER_REPLAY=~/.local/share/meshhunter/sniffer_packets.jsonl \
    ./build/linux-x86-64/Release/meshhunter
```

Replay mode appends to the same data directory, so point `HOME` or
`XDG_DATA_HOME` somewhere else if you don't want replayed nodes mixed into
your real logs.

### Tools

Both are built by default (`-DMESHHUNTER_BUILD_TOOLS=OFF` to skip):

- `mesh_tests [sniffer_packets.jsonl]`: unit tests. Given a capture written by
  the desktop app, it also re-decodes every packet and compares the result
  with the desktop decoder's output.
- `radio_probe [seconds] [profile keys…]`: command-line listener for checking
  the hat without the UI. It prints per-profile counts, RSSI, every packet and
  the GPS status.

## Layout

```
src/mesh/        radio, decoding, GPS, CSV storage (no LVGL)
  sx1262.*         RX-only SX1262 driver
  cap_lora_io.*    Cap LoRa-1262 SPI/GPIO/expander/5 V glue
  sniffer.*        hop/lock/linger receive loop, replay source
  meshcore.* meshtastic.* crypto.*  packet decoding
  engine.*         ties it together; owns the CSVs and the packet log
src/viewmodel/   key handling, page state
src/view/        LVGL screens (Nodes, Live, Radio, GPS), nav bar, help
src/app/         startup, settings, shortcuts
```

## License

0BSD, same as MeshHunter. The template code is MIT (M5Stack), and the bundled
fonts carry their own licenses (`assets/fonts/LICENSE.txt`).

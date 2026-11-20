# MeshHunter LoRa sniffer firmware

Receive-only firmware for a **Seeed XIAO nRF52840 + Wio-SX1262** (XIAO kit
pinout: NSS D4, DIO1 D1, RESET D2, BUSY D3, RXEN D5, 1.8 V TCXO). It
listens on one LoRa profile, or hops across several, and sends every packet
it hears to MeshHunter as JSON lines over USB serial. All decoding happens
in the app (`meshhunter/core/sniffer_decode.py`), so new packet types don't
need a firmware update.

## Build and flash

```bash
cd firmware/sniffer
~/.platformio/penv/bin/pio run -t upload --upload-port /dev/ttyACM1
```

Upload uses the stock Adafruit bootloader over serial DFU (a 1200 bps
touch puts the board into the bootloader). This firmware replaces whatever
was on the board. To go back, reflash MeshCore or Meshtastic the usual way; the
bootloader isn't touched.

## Serial protocol (115200, newline-terminated)

Host → device:

| Command | Meaning |
|---|---|
| `HELLO` | reply with a hello line |
| `CLR` | stop, clear the profile list |
| `P <id> <MHz> <bw kHz> <sf> <cr> <sync hex> <preamble>` | add a profile (max 16) |
| `T <dwell ms> <linger ms> <partial linger ms> <max linger ms>` | hop timing; dwell 0 = auto |
| `GO` | start: 1 profile = fixed, more = hop |
| `STOP` | radio to standby |

Device → host: `hello` (every second while idle), `pkt` (`crc` 1/0, `hex`,
`rssi`, `snr`, profile fields), `partial` (`kind`: `header` / `timeout`),
`stat` every 5 s (`nf` = noise floor, then per profile
`[id, ok, crc_err, hdr_err, timeouts, preamble_only, listen_ms]`),
`rs` (~5×/s, peak RSSI per profile visited, for the waterfall), and `ok` / `err`.

The firmware stops listening when the host closes the port (DTR drops), so
each connection starts from a clean state.

## Extra profiles and keys (config.json)

```json
"sniffer_custom_profiles": [
  {"key": "mc_alt", "name": "MeshCore 250/SF10", "family": "meshcore",
   "freq": 910.525, "bw": 250, "sf": 10, "cr": 5, "sync": "12"}
],
"sniffer_meshcore_channels": [{"name": "#local"}, {"name": "Club", "key": "<32 hex chars>"}],
"sniffer_meshtastic_channels": [{"name": "MyChan", "psk": "<base64 psk>"}]
```

Custom profiles show up in Settings → LoRa sniffer alongside the built-in
ones. A sync word of `12` is decoded as MeshCore and `2b` as Meshtastic.
Anything else is logged raw.

## USB LoRa sticks (MeshToad, MeshStick) -- no firmware needed

CH341-based sticks such as the Nullhop MeshToad (EBYTE E22/E22P, SX1262)
have no microcontroller, so there's nothing to flash: MeshHunter drives
the SX1262 directly over USB and runs this firmware's listen/lock/linger/
hop logic on the computer (`meshhunter/core/usb_sniffer.py`), speaking the
same protocol internally. Everything else (decoding, waterfall, Messages,
logging) is shared.

The stick shows up in the device list as `USB LoRa: <serial>`. Linux
needs a udev rule so it can be opened without root:

```bash
sudo cp contrib/99-meshhunter-usb-lora.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

then replug it. It can't be shared with meshtasticd at the same time.
Every radio operation is a USB round trip (~1 ms), so retuning is slower
than on the XIAO. Fixed mode isn't affected; in hop mode each profile
gets at least 30 ms.

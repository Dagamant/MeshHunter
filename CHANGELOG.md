# Changelog

All notable changes to MeshHunter. Versions follow
[semantic versioning](https://semver.org/); the desktop app, the
CardputerZero port and the Python package share one version number.

## [Unreleased]

## [0.1.0] - 2026-11-26

First tagged release. Everything below is what MeshHunter could do at this point.

### Desktop app
- Log MeshCore nodes from a companion device over serial or BLE. Every node
  type that advertises a location (repeater, chat, room, sensor) goes to its
  own CSV, along with where you were when you heard it (optional GPS receiver).
- Auto-upload discoveries to WDGWars and/or a self-hosted ingest API,
  immediately or in batches.
- Send 0-hop/flood adverts, clear the device's contacts, and import a MeshCore
  contacts backup JSON.
- Decrypt the Public channel and subscribed #hashtag or keyed MeshCore channels
  without using up the companion's own channel slots. Includes a dictionary
  search for channels you don't have a key for.
- Redesigned desktop window: top bar, stats strip, packet inspector, Settings
  dialog with left navigation, a small-window layout, and a multi-radio view.
- Pocket mode (`--pocket`) for fixed 640×480 touch screens.

### Radios and SDR
- LoRa sniffer support: XIAO nRF52840 + Wio-SX1262 with the receive-only
  firmware in `firmware/sniffer/`, and USB LoRa sticks (MeshToad, MeshStick)
  with no firmware to flash. Each radio is fixed on one profile or hops across
  several, and can be pinned to a profile. Decodes MeshCore and Meshtastic
  default channels.
- LoRa HAT mode for the Raspberry Pi with the HackerGadgets RTL-SDR/LoRa/GPS/RTC
  HAT, including a fullscreen touch UI for the PocketTerm35.
- Live RTL-SDR waterfall with a band-plan strip, mesh profiles drawn in the band
  lanes, and a noise-floor tick on the signal bars.
- RF sensors mode: rtl_433 hops the 33 cm meter and weather-sensor frequencies
  (plus optional 315/345/433 MHz) and keeps a per-device sensor table in
  `rf_sensors.csv`.

### Terminal
- Curses TUI (`--tui`) and `--headless` mode with the same logging, uploads,
  sniffer support and config as the desktop app, without Qt. Includes an
  example systemd unit.

### CardputerZero
- Receive-only C++/LVGL port for the CardputerZero with the Cap LoRa-1262
  (`cardputer-zero/`). It writes the same CSVs as the desktop app and installs
  from `MeshHunter_<version>_rev1_arm64.deb`.

### Packaging and project
- Installable with pipx: `meshhunter` (desktop, `[gui]` extra) and
  `meshhunter-tui` commands.
- pytest suite and GitHub Actions CI on Linux, macOS and Windows. CI includes
  an offscreen GUI smoke test, the CardputerZero `mesh_tests`, and a check that
  the CardputerZero decoder agrees with the desktop decoder.
- Release workflow: pushing a `v*` tag builds the Python wheel and sdist, the
  CardputerZero `.deb` and the sniffer firmware, and attaches them to the
  GitHub release.

[Unreleased]: https://github.com/Dagamant/MeshHunter/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Dagamant/MeshHunter/releases/tag/v0.1.0

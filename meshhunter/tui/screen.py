"""The curses TUI: header, view tabs (Log / Messages / Radio), key hints,
and modal overlays (device picker, settings, help, prompts).

Drawing is pull-based: the controller sets `changed` whenever state moves,
and the render task redraws at most ~20 times a second. ncurses only
sends the cells that actually changed, so this stays light over SSH and
on small boards.
"""

import asyncio
import curses
import os
import signal
import sys
import time
import unicodedata

from ..core.log_format import classify_log_line
from ..core.messages import MESH_LABELS, channel_choices, channel_text, from_text, matches, signal_text
from ..core.waterfall_model import NOISE_HEADROOM_DB, ROWS_PER_SECOND, SCALE_SPAN_DB
from . import settings as settings_ui

VIEWS = ("Log", "Messages", "Radio")
MESH_FILTERS = (None, "meshcore", "meshtastic")
WATERFALL_SECONDS_PER_LINE = 1
SHADES = " ░▒▓█"

# Color roles -> (256-color, 8-color fallback)
_ROLES = {
    "accent": (82, curses.COLOR_GREEN),
    "dim": (242, curses.COLOR_WHITE),
    "text": (252, curses.COLOR_WHITE),
    "ts": (28, curses.COLOR_GREEN),
    "path": (65, curses.COLOR_GREEN),
    "warn": (179, curses.COLOR_YELLOW),
    "ok": (151, curses.COLOR_GREEN),
    "error": (203, curses.COLOR_RED),
    "meshcore": (81, curses.COLOR_CYAN),
    "meshtastic": (177, curses.COLOR_MAGENTA),
    "marker_ok": (231, curses.COLOR_WHITE),
    "marker_crc": (179, curses.COLOR_YELLOW),
    "marker_partial": (203, curses.COLOR_RED),
}
# Waterfall background ramp (256-color only): black -> greens -> near white.
_RAMP = [16, 22, 22, 28, 28, 34, 40, 46, 82, 118, 157, 194, 230]
_RADIO_ROLES = {"listening": "accent", "idle": "dim", "connecting": "warn", "retrying": "warn",
                "failed": "error", "stopped": "dim"}
_LOG_TAGS = {"ts": "ts", "event": "accent", "key": "dim", "val": "text", "path": "path",
             "warn": "warn", "ok": "ok", "dim": "dim", None: "text"}


def _char_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def clip(text, width):
    """Cut text to `width` terminal cells (wide characters count double)."""
    if width <= 0:
        return ""
    out, used = [], 0
    for ch in text:
        if ch in "\n\r\t":
            ch = " "
        cw = _char_width(ch)
        if used + cw > width:
            if out and width >= 1:
                out[-1] = "…" if _char_width(out[-1]) == 1 else out[-1]
            break
        out.append(ch)
        used += cw
    return "".join(out)


def cell_width(text):
    return sum(_char_width(ch) for ch in text)


def pad(text, width):
    text = clip(text, width)
    return text + " " * (width - cell_width(text))


def wrap(segments, width):
    """[(text, attr)] -> visual lines, each [(text, attr)], hard-wrapped."""
    lines, cur, used = [], [], 0
    for text, attr in segments:
        buf = ""
        for ch in text.replace("\t", " "):
            cw = _char_width(ch)
            if used + cw > width:
                if buf:
                    cur.append((buf, attr))
                lines.append(cur)
                cur, buf, used = [], "", 0
            buf += ch
            used += cw
        if buf:
            cur.append((buf, attr))
    lines.append(cur)
    return lines


class Screen:
    def __init__(self, stdscr, controller):
        self.scr = stdscr
        self.c = controller
        self.view = 0
        self.running = True
        self.modal = None  # None | "devices" | "settings" | "help"
        self.prompt = None  # {"label", "text", "on_submit", "secret"}
        self.flash = ("", 0.0)

        self.log_scroll = 0  # visual lines up from the bottom; 0 = follow
        self.msg_mesh = 0
        self.msg_text_only = False
        self.msg_channel = None  # None = all, else a channel_text() value
        self.msg_selected = None  # the selected message dict; None = follow newest
        self.dev_list = []
        self.dev_index = 0
        self.set_items = []
        self.set_draft = {}
        self.set_index = 0
        self.set_top = 0

        self._setup_curses()

    # ---- curses setup ------------------------------------------------------

    def _setup_curses(self):
        curses.curs_set(0)
        self.scr.nodelay(True)
        self.scr.keypad(True)
        self.colors256 = False
        self.attrs = {role: curses.A_NORMAL for role in _ROLES}
        self.attrs["title"] = curses.A_REVERSE
        self.attrs["select"] = curses.A_REVERSE
        self.ramp = []
        if not curses.has_colors():
            self.attrs["accent"] = curses.A_BOLD
            self.attrs["dim"] = curses.A_DIM
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        self.colors256 = curses.COLORS >= 256
        pair = 1
        for role, (c256, c8) in _ROLES.items():
            curses.init_pair(pair, c256 if self.colors256 else c8, bg)
            self.attrs[role] = curses.color_pair(pair)
            pair += 1
        if self.attrs["dim"] and not self.colors256:
            self.attrs["dim"] |= curses.A_DIM
        self.attrs["title"] = self._pair(pair, curses.COLOR_BLACK, 82 if self.colors256 else curses.COLOR_GREEN)
        pair += 1
        self.attrs["select"] = self._pair(pair, 231 if self.colors256 else curses.COLOR_WHITE,
                                          236 if self.colors256 else curses.COLOR_BLUE)
        pair += 1
        if self.colors256:
            for color in _RAMP:
                self.ramp.append(self._pair(pair, color, color))
                pair += 1

    def _pair(self, n, fg, bg):
        curses.init_pair(n, fg, bg)
        return curses.color_pair(n)

    def a(self, role):
        return self.attrs.get(role, curses.A_NORMAL)

    # ---- main loop ---------------------------------------------------------

    async def run(self):
        loop = asyncio.get_running_loop()
        loop.add_reader(sys.stdin.fileno(), self._read_keys)
        try:
            loop.add_signal_handler(signal.SIGWINCH, self._on_resize)
        except (NotImplementedError, AttributeError, ValueError):
            pass
        try:
            while self.running:
                try:
                    await asyncio.wait_for(self.c.changed.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass  # redraw anyway: the clock and GPS fix move
                self.c.changed.clear()
                self.draw()
                await asyncio.sleep(0.05)
        finally:
            loop.remove_reader(sys.stdin.fileno())

    def _on_resize(self):
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
            curses.resizeterm(size.lines, size.columns)
        except (OSError, curses.error):
            pass
        self.scr.clear()
        self.c.changed.set()

    def _read_keys(self):
        while True:
            try:
                key = self.scr.get_wch()
            except curses.error:
                break
            if key == curses.KEY_RESIZE:
                self._on_resize()
                continue
            self.handle_key(key)
            self.c.changed.set()

    def say(self, text):
        self.flash = (text, time.monotonic() + 4)

    # ---- input -------------------------------------------------------------

    def handle_key(self, key):
        if self.prompt is not None:
            self._prompt_key(key)
        elif self.modal == "help":
            self.modal = None
        elif self.modal == "devices":
            self._devices_key(key)
        elif self.modal == "settings":
            self._settings_key(key)
        else:
            self._main_key(key)

    def _main_key(self, key):
        c = self.c
        if key in ("q", "Q"):
            if c.busy:
                self.ask("Disconnect and quit? (y/N)", lambda yes: self._quit() if yes else None, confirm=True)
            else:
                self._quit()
        elif key in ("1", "2", "3"):
            self.view = int(key) - 1
        elif key == "\t":
            self.view = (self.view + 1) % len(VIEWS)
        elif key == curses.KEY_BTAB:
            self.view = (self.view - 1) % len(VIEWS)
        elif key == "c":
            c.toggle_connection()
        elif key == "d":
            self._open_devices()
        elif key == "s":
            self._open_settings()
        elif key == "?":
            self.modal = "help"
        elif key == "a":
            c.send_advert(False)
        elif key == "A":
            c.send_advert(True)
        elif key == "x":
            if c.connected and not c.is_sniffer:
                self.ask("Remove ALL contacts stored on the device? (y/N)",
                         lambda yes: c.clear_device_contacts() if yes else None, confirm=True)
            else:
                self.say("Clear contacts needs a connected companion")
        elif key == "u":
            c.send_pending()
        elif key == "i":
            self.ask("Import contacts JSON from path:", lambda path: c.import_json(path) if path else None)
        elif self.view == 0:
            self._log_key(key)
        elif self.view == 1:
            self._messages_key(key)

    def _quit(self):
        self.running = False

    def _log_key(self, key):
        page = max(1, self._body_height() - 1)
        if key in (curses.KEY_UP, "k"):
            self.log_scroll += 1
        elif key in (curses.KEY_DOWN, "j"):
            self.log_scroll = max(0, self.log_scroll - 1)
        elif key == curses.KEY_PPAGE:
            self.log_scroll += page
        elif key == curses.KEY_NPAGE:
            self.log_scroll = max(0, self.log_scroll - page)
        elif key in (curses.KEY_HOME, "g"):
            self.log_scroll = 10 ** 9
        elif key in (curses.KEY_END, "G"):
            self.log_scroll = 0
        elif key == "C":
            self.c.log.clear()
            self.log_scroll = 0

    def _filtered_messages(self):
        mesh = MESH_FILTERS[self.msg_mesh]
        return [m for m in self.c.messages.rows if matches(m, mesh, self.msg_text_only, self.msg_channel)]

    def _find_channels(self):
        """Dictionary-attack unknown #hashtag channels from captured traffic
        and auto-subscribe to any recovered, confirming via each packet's
        MAC (see core/channel_crack.py)."""
        from ..core import channel_crack as cc
        from ..core.channels import clean_entries, subscribed_channels

        rows = self.c.messages.rows
        payloads = cc.captured_payloads(rows)
        if not payloads:
            self.say("No captured group packets to search")
            return
        known = {ch["hash"] for ch in subscribed_channels(self.c.config)}
        names = cc.candidate_names(extra=cc.words_from_messages(rows))
        results = cc.crack(payloads, names, known_hashes=known)
        if not results:
            self.say(f"No new channels found in {len(payloads)} packets (try more names)")
            return
        entries = list(self.c.config.get("meshcore_channels") or [])
        have = {e["name"].lower() for e in entries}
        new = [r["name"] for r in results if r["name"].lower() not in have]
        entries += [{"name": n, "enabled": True} for n in new]
        self.c.apply_settings({**self.c.config, "meshcore_channels": clean_entries(entries)})
        self.say(f"Found & subscribed: {', '.join(r['name'] for r in results)}")

    def _cycle_channel(self, step):
        subscribed = [e["name"] for e in self.c.config.get("meshcore_channels") or [] if e.get("enabled", True)]
        options = [None, "DM", *channel_choices(self.c.messages.rows, subscribed)]
        current = options.index(self.msg_channel) if self.msg_channel in options else 0
        self.msg_channel = options[(current + step) % len(options)]
        self.msg_selected = None

    def _messages_key(self, key):
        rows = self._filtered_messages()
        idx = self._selected_index(rows)
        page = max(1, self._body_height() - 6)
        if key == "t":
            self.msg_text_only = not self.msg_text_only
        elif key == "m":
            self.msg_mesh = (self.msg_mesh + 1) % len(MESH_FILTERS)
        elif key in ("n", "N"):
            self._cycle_channel(1 if key == "n" else -1)
        elif key in ("f", "F"):
            self._find_channels()
        elif key == "C":
            self.c.messages.clear()
            self.msg_selected = None
        elif not rows:
            return
        elif key in (curses.KEY_DOWN, "j"):
            self.msg_selected = rows[min(len(rows) - 1, idx + 1)]
        elif key in (curses.KEY_UP, "k"):
            self.msg_selected = rows[idx - 1] if idx > 0 else None
        elif key == curses.KEY_NPAGE:
            self.msg_selected = rows[min(len(rows) - 1, idx + page)]
        elif key == curses.KEY_PPAGE:
            self.msg_selected = rows[idx - page] if idx - page > 0 else None
        elif key in (curses.KEY_HOME, "g", curses.KEY_END, "G"):
            self.msg_selected = None if key in (curses.KEY_HOME, "g") else rows[-1]

    def _selected_index(self, rows):
        if self.msg_selected is None:
            return 0
        for i, m in enumerate(rows):
            if m is self.msg_selected:
                return i
        self.msg_selected = None
        return 0

    # ---- prompts -----------------------------------------------------------

    def ask(self, label, on_submit, text="", secret=False, confirm=False):
        self.prompt = {"label": label, "text": text, "on_submit": on_submit, "secret": secret, "confirm": confirm}

    def _prompt_key(self, key):
        p = self.prompt
        if p["confirm"]:
            self.prompt = None
            p["on_submit"](key in ("y", "Y"))
            return
        if key == "\x1b":
            self.prompt = None
        elif key in ("\n", "\r", curses.KEY_ENTER):
            self.prompt = None
            p["on_submit"](p["text"])
        elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            p["text"] = p["text"][:-1]
        elif key == "\x15":  # Ctrl-U
            p["text"] = ""
        elif isinstance(key, str) and key.isprintable():
            p["text"] += key

    # ---- device picker -----------------------------------------------------

    def _open_devices(self):
        self.dev_list = self.c.devices()
        self.dev_index = self.dev_list.index(self.c.device) if self.c.device in self.dev_list else 0
        self.modal = "devices"

    def _devices_key(self, key):
        n = len(self.dev_list) + 1  # last row: type a path
        if key == "\x1b" or key == "q":
            self.modal = None
        elif key in (curses.KEY_DOWN, "j"):
            self.dev_index = (self.dev_index + 1) % n
        elif key in (curses.KEY_UP, "k"):
            self.dev_index = (self.dev_index - 1) % n
        elif key == "r":
            self.c.scan_ble()
            self.dev_list = self.c.devices()
            self.say("Rescanning serial ports; BLE results appear in a few seconds (press r again)")
        elif key in ("\n", "\r", curses.KEY_ENTER, " "):
            self.modal = None
            if self.dev_index < len(self.dev_list):
                self._switch_device(self.dev_list[self.dev_index])
            else:
                self.ask("Device path or BLE address:", lambda t: self._switch_device(t.strip()) if t.strip() else None,
                         text=self.c.device)

    def _switch_device(self, selection):
        if self.c.busy or self.c.retrying:
            self.c.disconnect()
        self.c.connect(selection)

    # ---- settings ----------------------------------------------------------

    def _open_settings(self):
        self.set_draft = dict(self.c.config)
        self.set_draft["sniffer_hop_profiles"] = list(self.set_draft.get("sniffer_hop_profiles") or [])
        self.set_draft["meshcore_channels"] = [dict(e) for e in self.set_draft.get("meshcore_channels") or []]
        self.set_items = settings_ui.build_items(self.set_draft)
        self.set_index = next(i for i, it in enumerate(self.set_items) if it["kind"] != "heading")
        self.set_top = 0
        self.modal = "settings"

    def _settings_move(self, step):
        i = self.set_index
        while True:
            i += step
            if not 0 <= i < len(self.set_items):
                return
            if self.set_items[i]["kind"] != "heading":
                self.set_index = i
                return

    def _settings_key(self, key):
        item = self.set_items[self.set_index]
        if key == "\x1b":
            self.modal = None
            self.say("Settings not saved")
        elif key in ("S", curses.KEY_F2) or key == "\x13":  # S, F2, Ctrl-S
            self.c.apply_settings(settings_ui.finalize(self.set_draft))
            self.modal = None
        elif key in (curses.KEY_DOWN, "j"):
            self._settings_move(1)
        elif key in (curses.KEY_UP, "k"):
            self._settings_move(-1)
        elif key in (curses.KEY_LEFT, "h") and item["kind"] == "choice":
            settings_ui.activate(item, self.set_draft, -1)
        elif key in (curses.KEY_RIGHT, "l") and item["kind"] == "choice":
            settings_ui.activate(item, self.set_draft, 1)
        elif key in ("x", curses.KEY_DC) and settings_ui.remove_channel(item, self.set_draft):
            self.say(f"Removed {item['key']} (S to save)")
            self._rebuild_settings()
        elif key in ("\n", "\r", curses.KEY_ENTER, " "):
            if settings_ui.activate(item, self.set_draft):
                if item["kind"] == "defchan":
                    self._rebuild_settings()
            else:
                current = "" if item["kind"] == "addchan" else str(self.set_draft.get(item["key"], "") or "")
                label = "Channel (#name, or Name <key>):" if item["kind"] == "addchan" else f"{item['label']}:"
                self.ask(label, lambda text, it=item: self._settings_text(it, text),
                         text=current, secret=item.get("secret", False))

    def _rebuild_settings(self):
        """Channel items add/remove rows: rebuild, keeping the cursor on a
        selectable row near where it was."""
        self.set_items = settings_ui.build_items(self.set_draft)
        self.set_index = min(self.set_index, len(self.set_items) - 1)
        if self.set_items[self.set_index]["kind"] == "heading":
            self._settings_move(1)

    def _settings_text(self, item, text):
        error = settings_ui.set_text(item, self.set_draft, text)
        if error:
            self.say(error)
        elif item["kind"] == "addchan":
            self._rebuild_settings()

    # ---- drawing helpers ---------------------------------------------------

    def put(self, y, x, text, attr=0, width=None):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return 0
        room = w - x if width is None else min(width, w - x)
        text = clip(text, room)
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass  # writing the bottom-right cell raises after succeeding
        return cell_width(text)

    def put_segments(self, y, x, segments, width):
        for text, attr in segments:
            if width <= 0:
                break
            used = self.put(y, x, text, attr, width)
            x += used
            width -= used

    def fill(self, y, attr, x=0, width=None):
        h, w = self.scr.getmaxyx()
        width = w - x if width is None else width
        self.put(y, x, " " * width, attr, width)

    def _body_height(self):
        h, _ = self.scr.getmaxyx()
        return max(0, h - 4)

    # ---- drawing -----------------------------------------------------------

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < 8 or w < 40:
            self.put(0, 0, "Terminal too small for MeshHunter (need 40x8)")
            self.scr.refresh()
            return
        self._draw_header(w)
        top, bottom = 3, h - 1
        if self.modal == "settings":
            self._draw_settings(top, bottom, w)
        elif self.view == 0:
            self._draw_log(top, bottom, w)
        elif self.view == 1:
            self._draw_messages(top, bottom, w)
        else:
            self._draw_radio(top, bottom, w)
        if self.modal == "devices":
            self._draw_devices(h, w)
        elif self.modal == "help":
            self._draw_help(h, w)
        self._draw_footer(h - 1, w)
        self.scr.noutrefresh()
        curses.doupdate()

    def _draw_header(self, w):
        c = self.c
        title = self.a("title")
        self.fill(0, title)
        x = self.put(0, 0, " MESHHUNTER ", title | curses.A_BOLD)
        if c.connected:
            state, role = "● LIVE", "accent"
        elif c.retrying or c.status == "waiting to reconnect":
            state, role = "◌ RETRYING", "warn"
        elif c.busy:
            state, role = "◌ CONNECTING", "warn"
        else:
            state, role = "○ OFFLINE", "dim"
        x += 1
        x += self.put(0, x, f" {state} ", self.a(role) | curses.A_BOLD | curses.A_REVERSE)
        kind = " · sniffer" if c.is_sniffer and not c.radios else ""
        right_w = 30
        x += 1 + self.put(0, x + 1, f"{c.device_text()}{kind}", title, w - x - right_w)
        # One dot per sniffer radio: green listening, amber connecting/retrying, red failed.
        for radio in c.radios:
            if x + cell_width(radio["label"]) + 4 > w - right_w:
                break
            x += self.put(0, x + 1, " ● ", self.a(_RADIO_ROLES.get(radio["state"], "dim")) | curses.A_REVERSE) + 1
            x += self.put(0, x, radio["label"], title)
        right = f"GPS {c.gps_text()}  {time.strftime('%H:%M:%S')} "
        self.put(0, max(x + 2, w - cell_width(right)), right, title)

        # Counts line.
        x = 1
        for label, value in c.count_items():
            x += self.put(1, x, f"{label} ", self.a("dim"))
            x += self.put(1, x, f"{value}", self.a("accent") | curses.A_BOLD)
            x += self.put(1, x, "   ")
        totals = f"WDGWars sent {c.wdgwars_sent}  Ingest sent {c.ingest_sent} "
        if x + cell_width(totals) < w:
            self.put(1, w - cell_width(totals), totals, self.a("dim"))

        # Tabs.
        x = 1
        for i, name in enumerate(VIEWS):
            label = f" {i + 1} {name} "
            if self.modal == "settings":
                attr = self.a("dim")
            elif i == self.view:
                attr = self.a("accent") | curses.A_REVERSE | curses.A_BOLD
            else:
                attr = self.a("dim")
            x += self.put(2, x, label, attr) + 1
        if self.modal == "settings":
            self.put(2, x + 1, " Settings ", self.a("accent") | curses.A_REVERSE | curses.A_BOLD)
        elif self.view == 1:
            mesh = MESH_FILTERS[self.msg_mesh]
            info = (f"mesh: {MESH_LABELS.get(mesh, 'all')}  chan: {self.msg_channel or 'all'}  "
                    f"{'text only' if self.msg_text_only else 'all packets'} ")
            self.put(2, max(x + 2, w - cell_width(info)), info, self.a("dim"))
        elif self.view == 0 and self.log_scroll:
            info = f"scrolled ↑ {self.log_scroll} lines (End to follow) "
            self.put(2, max(x + 2, w - cell_width(info)), info, self.a("warn"))

    def _draw_footer(self, y, w):
        if self.prompt is not None:
            p = self.prompt
            text = "•" * len(p["text"]) if p["secret"] else p["text"]
            self.fill(y, self.a("select"))
            x = self.put(y, 0, f" {p['label']} ", self.a("select") | curses.A_BOLD)
            if p["confirm"]:
                return
            shown = text[-max(1, w - x - 2):]
            x += self.put(y, x, shown, self.a("select"))
            self.put(y, x, "▏", self.a("select") | curses.A_BLINK)
            return
        msg, until = self.flash
        if msg and time.monotonic() < until:
            self.fill(y, self.a("select"))
            self.put(y, 1, msg, self.a("select"))
            return
        if self.modal == "settings":
            keys = [("↑↓", "move"), ("Enter", "toggle/edit"), ("←→", "choose"), ("x", "remove channel"),
                    ("S", "save"), ("Esc", "cancel")]
        elif self.modal == "devices":
            keys = [("↑↓", "move"), ("Enter", "connect"), ("r", "rescan"), ("Esc", "close")]
        else:
            keys = [("c", "disconnect" if (self.c.busy or self.c.retrying) else "connect"),
                    ("d", "device"), ("s", "settings"), ("1-3", "view")]
            if self.view == 1:
                keys += [("t", "text only"), ("m", "mesh"), ("n", "channel"), ("f", "find"), ("↑↓", "select")]
            elif self.view == 0:
                keys += [("PgUp/PgDn", "scroll")]
            if self.c.connected and not self.c.is_sniffer:
                keys += [("a/A", "advert")]
            if self.c.config.get("batch_uploads"):
                keys += [("u", "send pending")]
            keys += [("?", "help"), ("q", "quit")]
        x = 0
        for key, label in keys:
            if x + cell_width(key) + cell_width(label) + 3 > w:
                break
            x += self.put(y, x, f" {key}", self.a("accent") | curses.A_BOLD)
            x += self.put(y, x, f" {label} ", self.a("dim"))

    # ---- views -------------------------------------------------------------

    def _draw_log(self, top, bottom, w):
        height = bottom - top
        width = w - 1
        need = height + self.log_scroll
        visual = []
        for line in reversed(self.c.log):
            segs = [(text, self.a(_LOG_TAGS.get(tag, "text"))) for text, tag in classify_log_line(line)]
            visual[:0] = wrap(segs, width)
            if len(visual) >= need:
                break
        if not visual:
            self.put(top + 1, 2, "Nothing yet. Press d to pick a device, c to connect, ? for help.", self.a("dim"))
            return
        self.log_scroll = min(self.log_scroll, max(0, len(visual) - height))
        end = len(visual) - self.log_scroll
        shown = visual[max(0, end - height):end]
        for i, segs in enumerate(shown):
            self.put_segments(top + i, 1, segs, width)

    def _message_columns(self, w):
        cols = [("Time", 8), ("Mesh", 10), ("Channel", 11), ("From", 22 if w >= 120 else 16)]
        signal_w = 26 if w >= 110 else 0
        used = sum(cw + 1 for _, cw in cols) + 1
        msg_w = max(10, w - used - (signal_w + 1 if signal_w else 0))
        cols.append(("Message", msg_w))
        if signal_w:
            cols.append(("Signal", signal_w))
        return cols

    def _draw_messages(self, top, bottom, w):
        rows = self._filtered_messages()
        cols = self._message_columns(w)
        x = 1
        for name, cw in cols:
            self.put(top, x, pad(name, cw), self.a("dim") | curses.A_UNDERLINE)
            x += cw + 1
        detail_h = 3
        list_top = top + 1
        list_h = bottom - list_top - detail_h - 1
        if not rows:
            total = len(self.c.messages.rows)
            hint = ("No packets decoded yet." if not total
                    else f"{total} packets hidden by the filter (t / m to change).")
            self.put(list_top + 1, 2, hint, self.a("dim"))
            return
        sel = self._selected_index(rows)
        start = 0 if sel < list_h else sel - list_h + 1
        for i, m in enumerate(rows[start:start + list_h]):
            y = list_top + i
            selected = start + i == sel and self.msg_selected is not None
            self._draw_message_row(y, cols, m, selected, w)
        # Detail pane for the selected (or newest) message.
        m = rows[sel]
        y = bottom - detail_h
        self.put(y - 1, 1, "─" * (w - 2), self.a("dim"))
        head = [(MESH_LABELS.get(m["mesh"], m["mesh"]), self.a(m["mesh"]) | curses.A_BOLD),
                (f"  via {m['source']}", self.a("dim"))]
        if m["profile"] and m["profile"] != channel_text(m):
            head.append((f" · {m['profile']}", self.a("dim")))
        if channel_text(m):
            head.append((f" · {channel_text(m)}", self.a("text")))
        head.append((f" · {from_text(m)}", self.a("accent")))
        sig = signal_text(m)
        if sig:
            head.append((f" · {sig}", self.a("dim")))
        self.put_segments(y, 1, head, w - 2)
        body = wrap([(m["text"], self.a("text"))], w - 2)
        for i, segs in enumerate(body[:detail_h - 1]):
            self.put_segments(y + 1 + i, 1, segs, w - 2)

    def _draw_message_row(self, y, cols, m, selected, w):
        is_text = m["kind"] in ("text", "dm")
        base = self.a("select") if selected else 0
        if selected:
            self.fill(y, base, 1, w - 2)
        values = {
            "Time": (m["time"], self.a("dim")),
            "Mesh": (MESH_LABELS.get(m["mesh"], m["mesh"]), self.a(m["mesh"]) | curses.A_BOLD),
            "Channel": (channel_text(m), self.a("text")),
            "From": (from_text(m), self.a("accent") if is_text else self.a("text")),
            "Message": (m["text"], self.a("text") if is_text else self.a("dim")),
            "Signal": (signal_text(m), self.a("dim")),
        }
        x = 1
        for name, cw in cols:
            text, attr = values[name]
            if m["kind"] == "encrypted" and name == "Message":
                attr |= curses.A_ITALIC if hasattr(curses, "A_ITALIC") else 0
            if selected:
                attr = base | (attr & curses.A_BOLD)
            self.put(y, x, pad(text, cw), attr)
            x += cw + 1

    def _draw_radio(self, top, bottom, w):
        model = self.c.waterfall
        n = len(model.profiles)
        if not n:
            msg = ("The radio waterfall needs the MeshHunter LoRa sniffer."
                   if not self.c.is_sniffer else "Waiting for the sniffer to start...")
            self.put(top + 1, 2, msg, self.a("dim"))
            return
        axis_w = 6
        gap = 1
        col_w = max(3, (w - 2 - axis_w - gap * (n - 1)) // n)
        # Radio status line.
        x = 1
        for radio in self.c.radios:
            role = _RADIO_ROLES.get(radio["state"], "dim")
            if radio["state"] == "listening":
                what = radio["profiles"][0] if len(radio["profiles"]) == 1 else f"hopping {len(radio['profiles'])}"
                if radio.get("noise") is not None:
                    what += f" {radio['noise']:.0f}dBm"
            else:
                what = radio["state"]
            x += self.put(top, x, "● ", self.a(role) | curses.A_BOLD)
            x += self.put(top, x, f"{radio['label']} ", self.a("text") | curses.A_BOLD)
            x += self.put(top, x, f"{radio['kind']} · ", self.a("dim"))
            x += self.put(top, x, what, self.a(role)) + 3
        stats = self.c.sniffer_stat.values()
        if stats:
            ok = sum(s.get("ok") or 0 for s in stats)
            bad = sum(s.get("bad") or 0 for s in stats)
            pre = sum(s.get("pre") or 0 for s in stats)
            hops = sum(s.get("hops") or 0 for s in stats)
            summary = f"rx {ok} · err {bad} · pre {pre} · hops {hops}"
            self.put(top, max(x + 1, w - cell_width(summary) - 1), summary, self.a("dim"))
        if self.c.radios:
            top += 1
        # Headers.
        for i, prof in enumerate(model.profiles):
            x = 1 + i * (col_w + gap)
            cur = i in model.current
            attr = self.a("accent") | curses.A_BOLD if cur else self.a("text")
            self.put(top, x, pad(("▶ " if cur else "") + model.column_name(i), col_w), attr)
            last = model.last[i]
            sub = f"{prof['freq']:g}"
            if last is not None:
                sub += f" {last:.0f}dBm"
            if model.counts[i]:
                sub += f" {model.counts[i]}pkt"
            self.put(top + 1, x, pad(sub, col_w), self.a("dim"))
            self.put(top + 2, x, ("━" if cur else "─") * col_w, self.a("accent") if cur else self.a("dim"))
        body_top = top + 3
        body_h = bottom - body_top - 1
        per_line = ROWS_PER_SECOND * WATERFALL_SECONDS_PER_LINE
        lines = model.merged_rows(per_line)[:body_h]
        for r, row in enumerate(lines):
            y = body_top + r
            for i, (dbm, live, marker) in enumerate(row):
                x = 1 + i * (col_w + gap)
                if marker:
                    self.put(y, x, "▬" * col_w, self.a(f"marker_{marker}") | curses.A_BOLD)
                elif dbm is not None:
                    self._put_level(y, x, col_w, model.level(i, dbm), live)
        # Time axis.
        ax = 1 + n * (col_w + gap)
        for r in range(0, body_h, 5):
            if r:
                self.put(body_top + r, ax, f"-{r * WATERFALL_SECONDS_PER_LINE}s", self.a("dim"))
        # Legend.
        y = bottom - 1
        x = 1
        x += self.put(y, x, f"noise … +{SCALE_SPAN_DB - NOISE_HEADROOM_DB} dB ", self.a("dim"))
        for t in (0.15, 0.35, 0.55, 0.75, 0.95):
            self._put_level(y, x, 2, t, True)
            x += 2
        x += 2
        for kind, label in (("ok", "packet"), ("crc", "bad CRC"), ("partial", "partial")):
            x += self.put(y, x, "▬▬", self.a(f"marker_{kind}") | curses.A_BOLD)
            x += self.put(y, x, f" {label}  ", self.a("dim"))
        self.put(y, x, "dim = not visited (hop)", self.a("dim"))

    def _put_level(self, y, x, width, level, live):
        if self.ramp:
            idx = int(level * (len(self.ramp) - 1))
            if not live:
                idx = max(0, idx // 2)
            self.put(y, x, " " * width, self.ramp[idx])
        else:
            ch = SHADES[min(len(SHADES) - 1, int(level * len(SHADES)))]
            attr = self.a("accent") | (curses.A_DIM if not live else curses.A_BOLD)
            self.put(y, x, ch * width, attr)

    # ---- overlays ----------------------------------------------------------

    def _box(self, h, w, height, width, title):
        height = min(height, h - 2)
        width = min(width, w - 2)
        y0, x0 = max(1, (h - height) // 2), max(1, (w - width) // 2)
        border = self.a("accent")
        self.put(y0, x0, "┌" + "─" * (width - 2) + "┐", border)
        for y in range(y0 + 1, y0 + height - 1):
            self.put(y, x0, "│" + " " * (width - 2) + "│", border)
        self.put(y0 + height - 1, x0, "└" + "─" * (width - 2) + "┘", border)
        self.put(y0, x0 + 2, f" {title} ", border | curses.A_BOLD)
        return y0 + 1, x0 + 2, height - 2, width - 4

    def _draw_devices(self, h, w):
        entries = self.dev_list + ["Type a path or BLE address…"]
        y, x, bh, bw = self._box(h, w, len(entries) + 4, 64, "Choose device")
        if not self.dev_list:
            self.put(y, x, "No serial ports found (r to rescan, or type a path)", self.a("dim"), bw)
        for i, entry in enumerate(entries[:bh - 1]):
            selected = i == self.dev_index
            label = entry
            if entry == self.c.device:
                label += "  (current)"
            attr = self.a("select") if selected else self.a("text")
            self.put(y + 1 + i, x, pad(("▶ " if selected else "  ") + label, bw), attr)

    def _draw_help(self, h, w):
        rows = [
            ("1 2 3 / Tab", "switch view: Log, Messages, Radio"),
            ("c", "connect / disconnect (retries every 10 s if the link drops)"),
            ("d", "choose device (serial port, BLE, or type a path)"),
            ("s", "settings (S to save, Esc to cancel)"),
            ("a / A", "send 0-hop / flood advert (companion)"),
            ("x", "clear all contacts on the companion"),
            ("u", "send pending uploads (batch mode)"),
            ("i", "import contacts from a MeshCore backup JSON"),
            ("↑↓ PgUp PgDn", "scroll the log / select a message"),
            ("Home / End", "oldest / follow newest"),
            ("t / m", "Messages: text only / cycle mesh filter"),
            ("n / N", "Messages: cycle channel filter (settings: channels)"),
            ("f", "Messages: find unknown #hashtag channels in captured traffic"),
            ("C", "clear the current view (Log or Messages)"),
            ("q", "quit"),
        ]
        y, x, bh, bw = self._box(h, w, len(rows) + 4, 78, "Keys")
        for i, (key, text) in enumerate(rows[:bh - 1]):
            self.put(y + i, x, pad(key, 14), self.a("accent") | curses.A_BOLD)
            self.put(y + i, x + 15, text, self.a("text"), bw - 15)
        self.put(y + bh - 1, x, "any key to close", self.a("dim"))

    def _draw_settings(self, top, bottom, w):
        height = bottom - top
        if self.set_index < self.set_top + 1:
            self.set_top = max(0, self.set_index - 1)
        elif self.set_index >= self.set_top + height:
            self.set_top = self.set_index - height + 1
        label_w = min(44, max(20, w // 2 - 4))
        for i, item in enumerate(self.set_items[self.set_top:self.set_top + height]):
            y = top + i
            idx = self.set_top + i
            if item["kind"] == "heading":
                self.put(y, 1, item["label"].upper(), self.a("accent") | curses.A_BOLD)
                continue
            selected = idx == self.set_index
            attr = self.a("select") if selected else self.a("text")
            if selected:
                self.fill(y, attr, 1, w - 2)
            self.put(y, 3, pad(item["label"], label_w), attr)
            self.put(y, 4 + label_w, settings_ui.value_text(item, self.set_draft),
                     attr if selected else self.a("accent"), w - label_w - 6)

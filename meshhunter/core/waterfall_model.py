"""Data side of the sniffer's RSSI waterfall, shared by the Qt and TUI views.

The SX1262 can't do an FFT, so this isn't a spectrum: each column is one
profile (frequency + modulation) the sniffer listens on, each row is one
~200 ms `rs` report, and a cell's level is the strongest signal seen on
that profile during the row, relative to that column's own noise floor
(so brightness means "signal above noise", comparable across bandwidths).
Newest rows first. Packet events become markers on the next row.

In hop mode a profile isn't visited every row; cells it skipped repeat its
last value with live=False, so views can dim them.
"""

from collections import deque

ROWS_PER_SECOND = 5  # firmware RSSI_REPORT_MS = 200
MAX_ROWS = 900  # 3 minutes of history
SCALE_SPAN_DB = 40
NOISE_HEADROOM_DB = 8  # noise sits this far above the bottom of the scale
INVALID_RSSI = -127.0  # what the SX1262 reads while it's still settling
MARKER_RANK = {"partial": 1, "crc": 2, "ok": 3}


class WaterfallModel:
    def __init__(self, max_rows=MAX_ROWS):
        self.rows = deque(maxlen=max_rows)  # each: [(dbm|None, live, marker|None), ...]
        self.set_profiles([])

    def set_profiles(self, profiles):
        self.profiles = list(profiles)
        n = len(self.profiles)
        self.rows.clear()
        self.last = [None] * n
        # Per-column noise tracking: a 250 kHz profile sees ~6 dB more noise
        # than a 62.5 kHz one and would otherwise look "busier" for nothing.
        self._recent = [deque(maxlen=150) for _ in range(n)]
        self._low = [None] * n
        self.counts = [0] * n
        self._pending_markers = {}
        self.current = set()  # column(s) being listened to now: one per radio

    def column_name(self, i):
        """Profile name, plus which radio listens to it when there are several."""
        prof = self.profiles[i]
        return f"{prof['name']} · {prof['radio']}" if prof.get("radio") else prof["name"]

    def radio_count(self):
        return len({p.get("radio") for p in self.profiles if p.get("radio")}) or (1 if self.profiles else 0)

    def add_rssi(self, payload):
        n = len(self.profiles)
        if not n:
            return
        fresh = {}
        for idx, dbm in payload.get("v", []):
            if 0 <= idx < n and dbm is not None and dbm > INVALID_RSSI:
                fresh[idx] = dbm
                self._recent[idx].append(dbm)
        row = []
        for i in range(n):
            if i in fresh:
                self.last[i] = fresh[i]
            row.append((self.last[i], i in fresh, self._pending_markers.pop(i, None)))
        self.rows.appendleft(row)
        cur = payload.get("cur")
        self.current = set(cur) if isinstance(cur, (list, tuple, set)) else ({cur} if cur is not None else set())
        self._rescale()

    def add_event(self, payload):
        idx, kind = payload.get("p"), payload.get("kind")
        if idx is None or not 0 <= idx < len(self.profiles) or kind not in MARKER_RANK:
            return
        prev = self._pending_markers.get(idx)
        if prev is None or MARKER_RANK[kind] > MARKER_RANK[prev]:
            self._pending_markers[idx] = kind
        if kind == "ok":
            self.counts[idx] += 1

    def _rescale(self):
        for i, recent in enumerate(self._recent):
            if len(recent) < 5:
                continue
            ordered = sorted(recent)
            noise = ordered[len(ordered) // 10]  # 10th percentile ~= noise floor
            target = noise - NOISE_HEADROOM_DB
            if self._low[i] is None:
                self._low[i] = target
            else:
                # Ease toward the target so colors don't flicker row to row.
                self._low[i] += (target - self._low[i]) * 0.1

    def level(self, i, dbm):
        """0..1 position of dbm on column i's scale."""
        low = self._low[i] if self._low[i] is not None else dbm - NOISE_HEADROOM_DB
        return min(1.0, max(0.0, (dbm - low) / SCALE_SPAN_DB))

    def merged_rows(self, per_line):
        """Rows grouped `per_line` at a time (for coarse views): per column
        the strongest live value (or the stale one), any-live, and the
        highest-ranked marker."""
        out = []
        rows = list(self.rows)
        for start in range(0, len(rows), per_line):
            chunk = rows[start:start + per_line]
            merged = []
            for i in range(len(self.profiles)):
                cells = [r[i] for r in chunk if i < len(r)]
                live_vals = [c[0] for c in cells if c[1] and c[0] is not None]
                stale = next((c[0] for c in cells if c[0] is not None), None)
                markers = [c[2] for c in cells if c[2]]
                marker = max(markers, key=MARKER_RANK.get) if markers else None
                if live_vals:
                    merged.append((max(live_vals), True, marker))
                else:
                    merged.append((stale, False, marker))
            out.append(merged)
        return out

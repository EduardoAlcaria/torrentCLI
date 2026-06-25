"""Textual TUI for BitMetal. The only module that touches the screen.

Layout: BITMETAL banner on top, torrent table on the left, an info sidebar for the
selected torrent on the right. Holds a TorrentManager (libtorrent) and a TorrentStore
(sqlite) and orchestrates between them: a tick loop polls, updates widgets, persists
state; quit pauses everything and saves fast-resume blobs.
"""

import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Static

RESUME_PERSIST_INTERVAL = 5.0  # seconds between resume-blob snapshots

BANNER = r"""
 ____  _ _   __  __      _        _
| __ )(_) |_|  \/  | ___| |_ __ _| |
|  _ \| | __| |\/| |/ _ \ __/ _` | |
| |_) | | |_| |  | |  __/ || (_| | |
|____/|_|\__|_|  |_|\___|\__\__,_|_|
"""


def _fmt_speed(bps):
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    v = float(bps)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f} {u}"
        v /= 1024


def _fmt_size(num):
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(num)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f} {u}"
        v /= 1024


def _fmt_eta(remaining, rate):
    if rate <= 0:
        return "--:--"
    secs = int(remaining / rate)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _bar(progress, width):
    filled = int(progress * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {progress * 100:5.1f}%"


class TorrentApp(App):
    CSS = """
    Screen { background: #06121f; }
    #banner {
        color: #4aa3ff;
        text-style: bold;
        height: auto;
        content-align: center middle;
        padding: 0 1;
    }
    #body { height: 1fr; }
    DataTable {
        width: 2fr;
        border: round #1e6fff;
        background: #07182b;
    }
    DataTable > .datatable--cursor { background: #1e6fff; color: #ffffff; }
    DataTable > .datatable--header { color: #7fc0ff; text-style: bold; }
    #sidebar {
        width: 1fr;
        border: round #1e6fff;
        background: #07182b;
        color: #cfe6ff;
        padding: 1 2;
    }
    Footer { background: #0a2540; color: #7fc0ff; }
    """

    BINDINGS = [
        ("q", "quit", "Quit (pause all)"),
        ("p", "toggle_pause", "Pause / Resume"),
    ]

    def __init__(self, manager, store):
        super().__init__()
        self.manager = manager
        self.store = store
        self._row_order = []  # infohash list, parallel to table rows
        self._last_resume_persist = 0.0
        self._latest = {}     # infohash -> last poll row
        self._selected = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(BANNER, id="banner")
        with Horizontal(id="body"):
            yield DataTable(id="torrents")
            yield Static("Select a torrent", id="sidebar")
        yield Footer()

    def on_mount(self):
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_column("Name", key="name")
        table.add_column("Progress", key="progress", width=44)
        table.add_column("Speed", key="speed")
        table.add_column("Peers", key="peers")
        table.add_column("Status", key="status")
        for infohash in self.manager.handles:
            table.add_row("(fetching metadata)", _bar(0.0, 34), "0.0 B/s", "0",
                          "queued", key=infohash)
            self._row_order.append(infohash)
        if self._row_order:
            self._selected = self._row_order[0]
        self.set_interval(0.25, self.tick)

    def tick(self):
        table = self.query_one(DataTable)
        now = time.time()
        persist_resume = now - self._last_resume_persist >= RESUME_PERSIST_INTERVAL

        self._latest = {}
        for r in self.manager.poll():
            ih = r["infohash"]
            if ih not in self._row_order:
                continue
            self._latest[ih] = r
            status = self._derive_status(r)
            table.update_cell(ih, "name", r["name"])
            table.update_cell(ih, "progress", _bar(r["progress"], 34))
            table.update_cell(ih, "speed", _fmt_speed(r["download_rate"]))
            table.update_cell(ih, "peers", str(r["num_peers"]))
            table.update_cell(ih, "status", status)

            if r["has_metadata"] and r["name"] != "(fetching metadata)":
                self.store.set_name(ih, r["name"])
            if r["is_finished"]:
                self.store.mark_done(ih)

        self._render_sidebar()

        # Persist any resume blobs that arrived since last tick (non-blocking).
        for ih, blob in self.manager.drain_resume_alerts():
            r = self._latest.get(ih)
            if r and not r["is_finished"]:
                self.store.save_resume(ih, blob, r["progress"], self._derive_status(r))

        # Periodically ask libtorrent for fresh resume data (arrives next ticks).
        if persist_resume:
            for ih, r in self._latest.items():
                if not r["is_finished"]:
                    self.manager.request_resume(ih)
            self._last_resume_persist = now

    def _render_sidebar(self):
        sidebar = self.query_one("#sidebar", Static)
        ih = self._selected
        r = self._latest.get(ih)
        if not r:
            sidebar.update("[#7fc0ff]Select a torrent[/]")
            return
        remaining = max(0, r["total_wanted"] - r["total_wanted_done"])
        lines = [
            "[b #4aa3ff]TORRENT[/]",
            f"[#7fc0ff]{r['name']}[/]",
            "",
            _bar(r["progress"], 22),
            "",
            f"[#7fc0ff]Status[/]    {self._derive_status(r)}",
            f"[#7fc0ff]State[/]     {r['state']}",
            f"[#7fc0ff]Down[/]      {_fmt_speed(r['download_rate'])}",
            f"[#7fc0ff]Up[/]        {_fmt_speed(r['upload_rate'])}",
            f"[#7fc0ff]Peers[/]     {r['num_peers']}  ([#7fc0ff]seeds[/] {r['num_seeds']})",
            f"[#7fc0ff]Size[/]      {_fmt_size(r['total_wanted_done'])} / {_fmt_size(r['total_wanted'])}",
            f"[#7fc0ff]ETA[/]       {_fmt_eta(remaining, r['download_rate'])}",
            "",
            f"[dim]{ih}[/]",
        ]
        if r["error"]:
            lines.append(f"[red]Error: {r['error']}[/]")
        sidebar.update("\n".join(lines))

    def _derive_status(self, row):
        if row["error"]:
            return "error"
        if row["is_finished"]:
            return "completed"
        if row["paused"]:
            return "paused"
        return "downloading" if row["has_metadata"] else "queued"

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        self._selected = event.row_key.value
        self._render_sidebar()

    def action_toggle_pause(self):
        ih = self._selected
        if not ih:
            return
        r = self._latest.get(ih)
        if r and r["paused"]:
            self.manager.resume(ih)
        else:
            self.manager.pause(ih)

    def action_quit(self):
        """Pause all, snapshot resume blobs, persist, then exit."""
        self.manager.pause_all()
        for r in self.manager.poll():
            ih = r["infohash"]
            if r["is_finished"]:
                self.store.mark_done(ih)
                continue
            blob = self.manager.snapshot_resume(ih)
            self.store.save_resume(ih, blob, r["progress"], "paused")
        self.store.close()
        self.exit()

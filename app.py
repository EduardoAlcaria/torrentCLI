"""Textual TUI. The only module that touches the screen.

Holds a TorrentManager (libtorrent) and a TorrentStore (sqlite) and orchestrates
between them: a tick loop polls the manager, updates the table, and persists state;
quit pauses everything and saves fast-resume blobs.
"""

import time

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Footer, Header

RESUME_PERSIST_INTERVAL = 5.0  # seconds between resume-blob snapshots


def _fmt_speed(bps):
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    v = float(bps)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f} {u}"
        v /= 1024


def _bar(progress, width=20):
    filled = int(progress * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {progress * 100:5.1f}%"


class TorrentApp(App):
    CSS = """
    DataTable { height: 1fr; }
    #controls { height: auto; padding: 0 1; }
    """

    BINDINGS = [
        ("q", "quit", "Quit (pause all)"),
        ("p", "toggle_pause", "Pause/Resume selected"),
    ]

    def __init__(self, manager, store):
        super().__init__()
        self.manager = manager
        self.store = store
        self._row_order = []  # infohash list, parallel to table rows
        self._last_resume_persist = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="controls"):
            yield Button("Pause / Resume", id="toggle", variant="warning")
        yield DataTable(id="torrents")
        yield Footer()

    def on_mount(self):
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_column("Name", key="name")
        table.add_column("Progress", key="progress")
        table.add_column("Speed", key="speed")
        table.add_column("Peers", key="peers")
        table.add_column("Status", key="status")
        for infohash in self.manager.handles:
            table.add_row("(fetching metadata)", _bar(0.0), "0.0 B/s", "0", "queued",
                          key=infohash)
            self._row_order.append(infohash)
        self.set_interval(0.25, self.tick)

    def tick(self):
        table = self.query_one(DataTable)
        now = time.time()
        persist_resume = now - self._last_resume_persist >= RESUME_PERSIST_INTERVAL
        for r in self.manager.poll():
            ih = r["infohash"]
            if ih not in self._row_order:
                continue
            status = self._derive_status(r)
            table.update_cell(ih, "name", r["name"])
            table.update_cell(ih, "progress", _bar(r["progress"]))
            table.update_cell(ih, "speed", _fmt_speed(r["download_rate"]))
            table.update_cell(ih, "peers", str(r["num_peers"]))
            table.update_cell(ih, "status", status)

            if r["has_metadata"] and r["name"] != "(fetching metadata)":
                self.store.set_name(ih, r["name"])

            if r["is_finished"]:
                self.store.mark_done(ih)
            elif persist_resume:
                blob = self.manager.snapshot_resume(ih)
                self.store.save_resume(ih, blob, r["progress"], status)
        if persist_resume:
            self._last_resume_persist = now

    def _derive_status(self, row):
        if row["error"]:
            return "error"
        if row["is_finished"]:
            return "completed"
        if row["paused"]:
            return "paused"
        return "downloading" if row["has_metadata"] else "queued"

    def _selected_infohash(self):
        table = self.query_one(DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._row_order):
            return None
        return self._row_order[table.cursor_row]

    def action_toggle_pause(self):
        ih = self._selected_infohash()
        if not ih:
            return
        rows = {r["infohash"]: r for r in self.manager.poll()}
        if rows.get(ih, {}).get("paused"):
            self.manager.resume(ih)
        else:
            self.manager.pause(ih)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "toggle":
            self.action_toggle_pause()

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

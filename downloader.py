"""libtorrent wrapper. No UI, no sqlite.

TorrentManager owns one lt.session and a dict of infohash -> handle. It exposes
torrent state via poll() and lets the caller pause/resume and snapshot fast-resume
blobs. Policy (what to persist, what to show) lives in the app, not here.
"""

import time
from pathlib import Path

import libtorrent as lt


def _infohash(atp):
    return str(atp.info_hashes.v1)


class TorrentManager:
    def __init__(self, save_path="downloads"):
        self.save_path = str(Path(save_path).resolve())
        Path(self.save_path).mkdir(parents=True, exist_ok=True)
        self.session = lt.session()
        self.session.listen_on(6881, 6891)
        self.session.start_dht()
        self.handles = {}  # infohash -> torrent_handle

    def add_magnet(self, magnet, resume_blob=None):
        """Add a magnet, optionally seeded with a fast-resume blob.

        Returns the v1 infohash hex. Does not block on metadata.
        """
        if resume_blob:
            atp = lt.read_resume_data(resume_blob)
        else:
            atp = lt.parse_magnet_uri(magnet)
        atp.save_path = self.save_path
        infohash = _infohash(atp)
        handle = self.session.add_torrent(atp)
        self.handles[infohash] = handle
        return infohash

    def poll(self):
        """Non-blocking snapshot of all torrents as a list of dicts."""
        rows = []
        for infohash, handle in self.handles.items():
            if not handle.is_valid():
                continue
            s = handle.status()
            rows.append(
                {
                    "infohash": infohash,
                    "name": s.name or "(fetching metadata)",
                    "progress": s.progress,
                    "download_rate": s.download_rate,
                    "num_peers": s.num_peers,
                    "state": str(s.state),
                    "is_finished": s.is_finished,
                    "has_metadata": s.has_metadata,
                    "paused": handle.flags() & lt.torrent_flags.paused != 0,
                    "error": str(s.error) if s.error else "",
                }
            )
        return rows

    def pause(self, infohash):
        h = self.handles.get(infohash)
        if h and h.is_valid():
            h.pause()

    def resume(self, infohash):
        h = self.handles.get(infohash)
        if h and h.is_valid():
            h.resume()

    def pause_all(self):
        for h in self.handles.values():
            if h.is_valid():
                h.pause()

    def snapshot_resume(self, infohash, timeout=2.0):
        """Request and return a fast-resume blob, or None if unavailable."""
        h = self.handles.get(infohash)
        if not h or not h.is_valid():
            return None
        h.save_resume_data(lt.torrent_handle.save_info_dict)
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.session.wait_for_alert(200)
            for a in self.session.pop_alerts():
                if isinstance(a, lt.save_resume_data_alert) and a.handle == h:
                    return lt.write_resume_data_buf(a.params)
                if isinstance(a, lt.save_resume_data_failed_alert) and a.handle == h:
                    return None
        return None

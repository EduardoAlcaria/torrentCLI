"""libtorrent wrapper. No UI, no sqlite.

TorrentManager owns one lt.session and a dict of infohash -> handle. It exposes
torrent state via poll() and lets the caller pause/resume and snapshot fast-resume
blobs. Policy (what to persist, what to show) lives in the app, not here.
"""

from pathlib import Path

import libtorrent as lt

# DHT bootstrap nodes — needed to find peers for magnets quickly.
DHT_ROUTERS = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.libtorrent.org", 25401),
]

# Public trackers appended to every magnet so peers are found even when DHT is slow.
DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]

# Session tuning: enable peer-discovery on every channel + generous limits.
SESSION_SETTINGS = {
    "listen_interfaces": "0.0.0.0:6881,[::]:6881",
    "enable_dht": True,
    "enable_lsd": True,        # local peer discovery
    "enable_upnp": True,       # auto port-forward via router
    "enable_natpmp": True,
    "announce_to_all_trackers": True,
    "announce_to_all_tiers": True,
    "connections_limit": 500,
    "active_downloads": -1,
    "active_seeds": -1,
    "active_limit": -1,
    "download_rate_limit": 0,   # unlimited
    "upload_rate_limit": 0,
    "alert_mask": lt.alert.category_t.status_notification
    | lt.alert.category_t.error_notification,
}


def _infohash(atp):
    return str(atp.info_hashes.v1)


class TorrentManager:
    def __init__(self, save_path="downloads"):
        self.save_path = str(Path(save_path).resolve())
        Path(self.save_path).mkdir(parents=True, exist_ok=True)
        self.session = lt.session(SESSION_SETTINGS)
        for host, port in DHT_ROUTERS:
            self.session.add_dht_router(host, port)
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
        # Merge default trackers in (dedupe against any already in the magnet).
        existing = set(atp.trackers)
        atp.trackers = list(atp.trackers) + [
            t for t in DEFAULT_TRACKERS if t not in existing
        ]
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
                    "upload_rate": s.upload_rate,
                    "num_peers": s.num_peers,
                    "num_seeds": s.num_seeds,
                    "total_wanted": s.total_wanted,
                    "total_wanted_done": s.total_wanted_done,
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

    def request_resume(self, infohash):
        """Ask libtorrent to produce resume data; non-blocking.

        The blob arrives later as a save_resume_data_alert, collected by
        drain_resume_alerts(). Use this on the hot UI path.
        """
        h = self.handles.get(infohash)
        if h and h.is_valid():
            h.save_resume_data(lt.torrent_handle.save_info_dict)

    def drain_resume_alerts(self):
        """Non-blocking: return [(infohash, blob)] for resume alerts since last call."""
        out = []
        by_handle = {h: ih for ih, h in self.handles.items()}
        for a in self.session.pop_alerts():
            if isinstance(a, lt.save_resume_data_alert):
                ih = by_handle.get(a.handle)
                if ih:
                    out.append((ih, lt.write_resume_data_buf(a.params)))
        return out

    def snapshot_resume(self, infohash, timeout=2.0):
        """Request and return a fast-resume blob, or None. Blocking; quit-only."""
        import time

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

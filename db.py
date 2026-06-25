"""SQLite persistence for tracked torrents.

TorrentStore owns the connection to torrents.db and touches nothing but sqlite.
Infohash (v1 hex) is the primary key and the dedupe key across the app.
"""

import sqlite3
from datetime import datetime, timezone

UNFINISHED_STATES = ("queued", "downloading", "paused", "error")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS torrents (
    infohash    TEXT PRIMARY KEY,
    magnet      TEXT NOT NULL,
    name        TEXT,
    save_path   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    progress    REAL NOT NULL DEFAULT 0.0,
    resume_data BLOB,
    added_at    TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


class TorrentStore:
    def __init__(self, path="torrents.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def upsert(self, infohash, magnet, save_path, name=None, status="queued"):
        """Insert a new torrent or refresh magnet/save_path/name.

        Never clobbers an existing resume_data blob or progress.
        """
        now = _now()
        self.conn.execute(
            """
            INSERT INTO torrents (infohash, magnet, name, save_path, status,
                                  progress, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0.0, ?, ?)
            ON CONFLICT(infohash) DO UPDATE SET
                magnet     = excluded.magnet,
                save_path  = excluded.save_path,
                name       = COALESCE(excluded.name, torrents.name),
                updated_at = excluded.updated_at
            """,
            (infohash, magnet, name, save_path, status, now, now),
        )
        self.conn.commit()

    def list_unfinished(self):
        """Rows that are not completed, as a list of dicts."""
        cur = self.conn.execute(
            "SELECT * FROM torrents WHERE status != 'completed' ORDER BY added_at"
        )
        return [dict(r) for r in cur.fetchall()]

    def save_resume(self, infohash, blob, progress, status):
        self.conn.execute(
            """
            UPDATE torrents
               SET resume_data = ?, progress = ?, status = ?, updated_at = ?
             WHERE infohash = ?
            """,
            (blob, progress, status, _now(), infohash),
        )
        self.conn.commit()

    def set_name(self, infohash, name):
        self.conn.execute(
            "UPDATE torrents SET name = ?, updated_at = ? WHERE infohash = ?",
            (name, _now(), infohash),
        )
        self.conn.commit()

    def mark_done(self, infohash):
        self.conn.execute(
            """
            UPDATE torrents
               SET status = 'completed', progress = 1.0, updated_at = ?
             WHERE infohash = ?
            """,
            (_now(), infohash),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()

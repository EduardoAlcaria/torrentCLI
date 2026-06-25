"""torrentCLI entry point.

  python main.py "<magnet>"   -> add that single magnet
  python main.py              -> read magnets.txt + resume DB unfinished torrents

Wires together TorrentStore (sqlite), TorrentManager (libtorrent), and TorrentApp
(textual). No business logic beyond source collection and dedupe.
"""

import sys
from pathlib import Path

import libtorrent as lt

from app import TorrentApp
from db import TorrentStore
from downloader import TorrentManager

MAGNETS_FILE = "magnets.txt"
SAVE_PATH = "downloads"


def read_magnets_file(path=MAGNETS_FILE):
    """One magnet per line; blank lines and #comments skipped."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def magnet_infohash(magnet):
    """v1 infohash hex for a magnet, or None if it can't be parsed."""
    try:
        return str(lt.parse_magnet_uri(magnet).info_hashes.v1)
    except Exception:
        return None


def collect_sources(argv, store):
    """Build a deduped (infohash, magnet, resume_blob) plan.

    DB unfinished torrents come first (with their resume blobs); new magnets from
    the CLI arg or magnets.txt are appended and registered in the store.
    """
    seen = set()
    plan = []
    save_path = str(Path(SAVE_PATH).resolve())

    for row in store.list_unfinished():
        ih = row["infohash"]
        if ih in seen:
            continue
        seen.add(ih)
        plan.append((ih, row["magnet"], row["resume_data"]))

    magnets = [argv[1]] if len(argv) > 1 else read_magnets_file()
    for m in magnets:
        ih = magnet_infohash(m)
        if not ih:
            print(f"Skipping unparseable magnet: {m[:60]}...")
            continue
        if ih in seen:
            continue
        seen.add(ih)
        store.upsert(ih, m, save_path=save_path)
        plan.append((ih, m, None))

    return plan


def main():
    store = TorrentStore()
    manager = TorrentManager(SAVE_PATH)

    for infohash, magnet, blob in collect_sources(sys.argv, store):
        manager.add_magnet(magnet, resume_blob=blob)

    if not manager.handles:
        print("No torrents. Add magnets to magnets.txt or pass one as an argument.")
        store.close()
        return

    TorrentApp(manager, store).run()


if __name__ == "__main__":
    main()

# torrentCLI v2 — Design

Date: 2026-06-25
Status: Approved (pending spec review)

## Goal

Evolve the single-file `main.py` torrent client into a small modular app that:

1. Launches with no CLI args by reading magnets from `magnets.txt`.
2. Replaces the `rich` progress UI with a `textual` TUI showing all torrents in a live table.
3. Persists unfinished torrents in a local SQLite DB so they resume on the next launch.
4. Lets the user pause/resume an individual torrent (keybind + button).
5. On close, pauses **all** torrents and persists their resume state.

Single-magnet CLI usage (`python main.py "<magnet>"`) is preserved.

## Runtime constraint

libtorrent ships wheels only up to Python 3.13 (cp313). The global interpreter is
Python 3.14, which has no wheel. The project runs in a Python 3.11 venv at `.venv/`
where `libtorrent==2.0.13` and `textual` are installed. All run/test commands use
`.venv\Scripts\python.exe`.

## Modules

Split `main.py` into four focused modules, each with one responsibility and a clean
interface. Each is testable in isolation.

### `db.py` — `TorrentStore`

SQLite persistence. Owns the connection to `torrents.db`. Touches nothing but sqlite.

Schema (table `torrents`):

| column       | type    | notes                                            |
|--------------|---------|--------------------------------------------------|
| infohash     | TEXT PK | torrent v1 infohash hex                          |
| magnet       | TEXT    | original magnet URI                              |
| name         | TEXT    | torrent display name (may be null until metadata)|
| save_path    | TEXT    | absolute download dir                            |
| status       | TEXT    | `queued`/`downloading`/`paused`/`completed`/`error` |
| progress     | REAL    | 0.0–1.0                                           |
| resume_data  | BLOB    | libtorrent fast-resume blob, nullable            |
| added_at     | TEXT    | ISO timestamp                                    |
| updated_at   | TEXT    | ISO timestamp                                    |

Methods:

- `__init__(path="torrents.db")` — connect, create table if missing.
- `upsert(infohash, magnet, save_path, name=None, status="queued")` — insert or update row; preserves existing resume_data/progress on conflict.
- `list_unfinished()` — rows where `status != 'completed'`. Returns list of dicts.
- `save_resume(infohash, blob, progress, status)` — store fast-resume blob + progress + status.
- `set_name(infohash, name)` — fill name once metadata arrives.
- `mark_done(infohash)` — `status='completed'`, `progress=1.0`.
- `close()`.

Infohash is the dedupe key. `upsert` on an existing infohash must not clobber a saved `resume_data` blob.

### `downloader.py` — `TorrentManager`

libtorrent wrapper. No UI, no sqlite. Owns one `lt.session`.

- `__init__(save_path)` — create session, `listen_on(6881,6891)`, `start_dht()`, ensure save dir.
- `add_magnet(magnet, resume_blob=None)` — `parse_magnet_uri`, set `save_path`, attach `resume_blob` via `params.resume_data` when present, `session.add_torrent`. Returns infohash hex. Does NOT block on metadata.
- `poll()` — return list of per-torrent status dicts: `{infohash, name, progress, download_rate, num_peers, state, is_finished, has_metadata}`. Non-blocking; rows without metadata yet report `has_metadata=False`.
- `pause(infohash)` / `resume(infohash)` — pause/resume that handle.
- `pause_all()` — pause every handle.
- `snapshot_resume(infohash)` — call `handle.save_resume_data()`, pump alerts for `save_resume_data_alert`, return the bencoded blob (or `None` if unavailable). Used on quit and periodic persistence.
- Completion is detected in `poll()` via `is_finished`; the app reacts (calls `store.mark_done`). The manager exposes state, the app owns the policy.

### `app.py` — `TorrentApp(textual.app.App)`

The only module that touches the UI. Holds references to a `TorrentManager` and a `TorrentStore`.

- A `DataTable` with columns: Name | Progress | Speed | Peers | Status. One row per torrent, keyed by infohash.
- `on_mount`: add initial torrents (passed in from `main`), `set_interval(0.25, self.tick)`.
- `tick()`: `manager.poll()` → update each row; persist progress and (periodically, e.g. every ~5s) resume blob + status to the store; when a torrent flips to finished, `store.mark_done` and set row status `completed`.
- Bindings: `q` quit, `p` pause/resume the selected row.
- A **Pause/Resume button** in the footer area acts on the currently selected row (same action as `p`).
- Quit action (`q`, button, or window close via `on_unmount`/`action_quit`):
  1. `manager.pause_all()`
  2. for each torrent: `blob = manager.snapshot_resume(ih)`
  3. store: unfinished rows → `save_resume(ih, blob, progress, status='paused')`; completed rows untouched
  4. `store.close()`, exit app.

### `main.py` — entry point

Thin wiring. No business logic.

- Parse args:
  - One arg → treat as a single magnet; seed list = `[that magnet]`.
  - No arg → read `magnets.txt` (one magnet per line; skip blank lines and lines starting with `#`).
- Build `TorrentStore` and `TorrentManager(save_path="downloads")`.
- Merge sources by infohash:
  - From `store.list_unfinished()`: re-add each with its `resume_data` blob.
  - From the magnet list: parse infohash; if not already present, `store.upsert(...)` and add.
  - Dedupe by infohash so a magnet already tracked is not added twice.
- Launch `TorrentApp(manager, store, initial_rows).run()`.

## Data flow

```
main.py
  ├─ builds TorrentStore  (sqlite only)
  ├─ builds TorrentManager (libtorrent only)
  ├─ merges magnets.txt + store.list_unfinished()  (dedupe by infohash)
  └─ TorrentApp(manager, store).run()   (UI only)
         tick loop: manager.poll() → update table → persist to store
         quit:      manager.pause_all() → snapshot_resume → store.save_resume(paused)
```

Each module depends only on its own domain. The app orchestrates; it is the single
place where libtorrent state meets sqlite persistence meets the screen.

## Resume flow

- Persist: on quit (always) and periodically during the tick loop, call
  `manager.snapshot_resume(ih)` and write the blob via `store.save_resume`.
- Restore: on launch, `store.list_unfinished()` rows are re-added through
  `manager.add_magnet(magnet, resume_blob=blob)`. libtorrent uses the blob to skip a
  full recheck. Restored torrents start paused (status was `paused` at close); the user
  resumes via `p`/button.

## Error handling

- Invalid/empty magnet line → skip with a logged warning, do not crash.
- Missing `magnets.txt` on no-arg launch → start with only DB unfinished torrents; if both empty, show an empty table with a hint.
- libtorrent `status.error` → set row status `error`, persist `status='error'` to db, keep other torrents running.
- DB write failure → surface in the UI status line; never lose the running session over a persistence error.

## Testing

- `TorrentStore`: unit tests against a temp sqlite file — upsert/dedupe, resume blob not clobbered on re-upsert, list_unfinished filtering, mark_done.
- `main.py` magnet parsing: blank/comment/dedupe handling (pure function, no network).
- `TorrentManager` and `TorrentApp` involve live libtorrent/TUI; cover with a thin smoke test (construct, add a magnet, one poll) rather than full integration, since real downloads need network.

## Out of scope (YAGNI)

- No per-torrent speed limits, no scheduling, no seeding management.
- No config file beyond `magnets.txt`.
- No multi-screen TUI; one table screen.
- Auto-resume on launch is deliberately off — restored torrents start paused.

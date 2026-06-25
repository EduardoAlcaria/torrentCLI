"""Unit tests for the no-network parts: TorrentStore and magnet collection.

Run: .venv\\Scripts\\python.exe -m pytest test_torrentcli.py
"""

import os

import pytest

from db import TorrentStore
import main

IH1 = "0123456789abcdef0123456789abcdef01234567"
IH2 = "89abcdef0123456789abcdef0123456789abcdef"
MAGNET1 = f"magnet:?xt=urn:btih:{IH1}&dn=one"
MAGNET2 = f"magnet:?xt=urn:btih:{IH2}&dn=two"


@pytest.fixture
def store(tmp_path):
    s = TorrentStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def test_upsert_and_list_unfinished(store):
    store.upsert(IH1, MAGNET1, "/dl")
    rows = store.list_unfinished()
    assert len(rows) == 1
    assert rows[0]["infohash"] == IH1
    assert rows[0]["status"] == "queued"


def test_mark_done_excluded_from_unfinished(store):
    store.upsert(IH1, MAGNET1, "/dl")
    store.mark_done(IH1)
    assert store.list_unfinished() == []


def test_upsert_does_not_clobber_resume_blob(store):
    store.upsert(IH1, MAGNET1, "/dl")
    store.save_resume(IH1, b"BLOB", 0.5, "paused")
    # re-upsert (e.g. magnet reappears in magnets.txt) must keep blob + progress
    store.upsert(IH1, MAGNET1, "/dl")
    row = store.list_unfinished()[0]
    assert row["resume_data"] == b"BLOB"
    assert row["progress"] == 0.5
    assert row["status"] == "paused"


def test_save_resume_updates_state(store):
    store.upsert(IH1, MAGNET1, "/dl")
    store.save_resume(IH1, b"X", 0.25, "paused")
    row = store.list_unfinished()[0]
    assert row["progress"] == 0.25
    assert row["status"] == "paused"


def test_set_name(store):
    store.upsert(IH1, MAGNET1, "/dl")
    store.set_name(IH1, "My Torrent")
    assert store.list_unfinished()[0]["name"] == "My Torrent"


def test_read_magnets_file(tmp_path):
    f = tmp_path / "magnets.txt"
    f.write_text("\n".join([MAGNET1, "  # comment", "", "  " + MAGNET2]))
    out = main.read_magnets_file(str(f))
    assert out == [MAGNET1, MAGNET2]


def test_read_magnets_file_missing(tmp_path):
    assert main.read_magnets_file(str(tmp_path / "nope.txt")) == []


def test_magnet_infohash():
    assert main.magnet_infohash(MAGNET1) == IH1
    assert main.magnet_infohash("not a magnet") is None


def test_collect_sources_dedupes_db_and_file(store, tmp_path, monkeypatch):
    # IH1 already tracked (unfinished) in DB; CLI passes IH1 again + new IH2
    store.upsert(IH1, MAGNET1, "/dl")
    store.save_resume(IH1, b"RESUME", 0.4, "paused")

    plan = main.collect_sources(["main.py", MAGNET2], store)
    by_ih = {ih: (m, blob) for ih, m, blob in plan}

    assert set(by_ih) == {IH1, IH2}
    assert by_ih[IH1][1] == b"RESUME"   # DB row keeps resume blob
    assert by_ih[IH2][1] is None        # new magnet, no blob
    # IH1 listed once only
    assert [ih for ih, _, _ in plan].count(IH1) == 1


def test_collect_sources_reads_file_when_no_arg(store, tmp_path, monkeypatch):
    f = tmp_path / "magnets.txt"
    f.write_text(MAGNET2)
    monkeypatch.setattr(main, "read_magnets_file", lambda path=str(f): [MAGNET2])

    plan = main.collect_sources(["main.py"], store)
    assert [ih for ih, _, _ in plan] == [IH2]

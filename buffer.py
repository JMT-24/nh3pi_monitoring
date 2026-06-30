"""
Offline-first local buffer (stdlib sqlite3, no extra deps).

Every sensor frame is written here FIRST, then we try to ship it. Rows that
reach the backend are marked synced; rows captured during an internet outage
stay pending and are replayed (oldest first) once connectivity returns. This
guarantees no readings are lost across sudden outages.

Note: only history is replayed. Live control always acts on the CURRENT frame
(see controller.py) — stale buffered frames are backfilled for the charts but
must never drive the pump.
"""

import json
import sqlite3

import nh3config as cfg

_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(cfg.BUFFER_DB)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS frames (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts     TEXT    NOT NULL,
                frame  TEXT    NOT NULL,   -- JSON ingest frame
                synced INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_frames_synced ON frames (synced, id)")
        _conn.commit()
    return _conn


def enqueue(frame, ts):
    """Persist a frame and return its row id."""
    cur = _db().execute(
        "INSERT INTO frames (ts, frame, synced) VALUES (?, ?, 0)",
        (ts, json.dumps(frame)),
    )
    _db().commit()
    return cur.lastrowid


def mark_synced(ids):
    if not ids:
        return
    qs = ",".join("?" * len(ids))
    _db().execute(f"UPDATE frames SET synced = 1 WHERE id IN ({qs})", list(ids))
    _db().commit()


def pending(limit=None, exclude_id=None):
    """Oldest-first pending rows as [{id, ts, frame(dict)}], for backfill."""
    limit = limit or cfg.BACKFILL_BATCH
    sql = "SELECT id, ts, frame FROM frames WHERE synced = 0"
    params = []
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    sql += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    rows = _db().execute(sql, params).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "frame": json.loads(r["frame"])} for r in rows]


def count_pending():
    return _db().execute("SELECT COUNT(*) AS n FROM frames WHERE synced = 0").fetchone()["n"]


def prune(keep_synced=5000):
    """Optional housekeeping: drop the oldest already-synced rows."""
    _db().execute(
        """
        DELETE FROM frames WHERE synced = 1 AND id NOT IN (
            SELECT id FROM frames WHERE synced = 1 ORDER BY id DESC LIMIT ?
        )
        """,
        (keep_synced,),
    )
    _db().commit()


def close():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None

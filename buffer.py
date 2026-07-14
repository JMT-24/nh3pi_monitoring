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


def pending(limit=None):
    """Oldest-first pending rows as [{id, ts, frame(dict)}], for backfill."""
    limit = limit or cfg.BACKFILL_BATCH
    rows = _db().execute(
        "SELECT id, ts, frame FROM frames WHERE synced = 0 ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "frame": json.loads(r["frame"])} for r in rows]


def count_pending():
    return _db().execute("SELECT COUNT(*) AS n FROM frames WHERE synced = 0").fetchone()["n"]


def drop(ids):
    """
    Discard rows outright. Used for frames the backend has permanently REJECTED (e.g. an
    implausible timestamp): keeping them would block the head of the backlog forever,
    since pending() always returns the oldest rows first.
    """
    if not ids:
        return
    qs = ",".join("?" * len(ids))
    _db().execute(f"DELETE FROM frames WHERE id IN ({qs})", list(ids))
    _db().commit()


def prune(keep_synced=None, max_pending=None):
    """
    Housekeeping — MUST be called periodically (controller.py does, once per cycle).

    This existed but was never called, so the buffer grew ~2,880 rows/day forever. The
    end state was a full SD card: enqueue() then raised OperationalError, which escaped
    the control loop and killed the process — and nothing restarts it, so the tank went
    unmonitored.

    Two ceilings:
      * keep_synced  — already-shipped rows are pure history; keep a recent window.
      * max_pending  — a backend that never accepts (e.g. a wrong API key) means rows are
                       never marked synced and never become prunable. Dropping the oldest
                       loses some history; filling the disk loses monitoring entirely.
    """
    keep_synced = cfg.BUFFER_KEEP_SYNCED if keep_synced is None else keep_synced
    max_pending = cfg.BUFFER_MAX_PENDING if max_pending is None else max_pending
    conn = _db()
    conn.execute(
        """
        DELETE FROM frames WHERE synced = 1 AND id NOT IN (
            SELECT id FROM frames WHERE synced = 1 ORDER BY id DESC LIMIT ?
        )
        """,
        (keep_synced,),
    )
    dropped = conn.execute(
        """
        DELETE FROM frames WHERE synced = 0 AND id NOT IN (
            SELECT id FROM frames WHERE synced = 0 ORDER BY id DESC LIMIT ?
        )
        """,
        (max_pending,),
    ).rowcount
    conn.commit()
    if dropped and dropped > 0:
        print(f"[buffer] WARNING: dropped {dropped} unsent frame(s) - backlog exceeded "
              f"{max_pending}. Is the backend rejecting our frames (check INGEST_API_KEY)?")
    return dropped or 0


def close():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None

"""
HTTP client for the backend ingest contract.

  send_live(frame)   -> POST /api/ingest        (drives control; returns command)
  backfill(items)    -> POST /api/ingest/batch  (history only; no control/SMS)

Both send the x-api-key header.

Failures never raise: the control loop must degrade gracefully (buffer + local
safety watchdog) rather than die. Note this module catches `requests.RequestException`
broadly, not just ConnectionError/Timeout — a malformed BACKEND_URL raises MissingSchema
(a ValueError subclass), which would otherwise escape and kill the loop on the very
first cycle, with nothing to restart it.

The result types distinguish OFFLINE from REJECTED. Collapsing both into None meant a
permanently-rejected frame (e.g. a wrong API key -> 401) looked like a network outage:
the log blamed the network, and the frame was retried forever at the head of the
backlog, blocking every newer frame behind it.
"""

import requests

import nh3config as cfg

_HEADERS = {"x-api-key": cfg.INGEST_API_KEY}

# send_live outcomes
OFFLINE = "offline"    # unreachable / timed out -> buffer it, retry later
REJECTED = "rejected"  # backend said no (4xx) -> retrying unchanged won't help


class Result:
    """send_live outcome: either a command dict, or a status explaining why not."""

    __slots__ = ("command", "status", "detail")

    def __init__(self, command=None, status=None, detail=""):
        self.command = command
        self.status = status
        self.detail = detail

    @property
    def ok(self):
        return self.command is not None


def send_live(frame):
    """
    Post the current frame. Returns a Result: `.command` on success, else `.status` of
    OFFLINE (transient — keep buffering) or REJECTED (the backend refused this frame).
    """
    try:
        r = requests.post(cfg.ingest_url(), json=frame, headers=_HEADERS, timeout=cfg.HTTP_TIMEOUT)
    except requests.RequestException as e:
        return Result(status=OFFLINE, detail=type(e).__name__)

    if r.status_code == 200:
        try:
            command = r.json().get("command")
        except ValueError:
            return Result(status=REJECTED, detail="malformed JSON response")
        if command is None:
            return Result(status=REJECTED, detail="response carried no command")
        return Result(command=command)

    detail = f"HTTP {r.status_code} {r.text[:120]}"
    if r.status_code == 401:
        detail += "  <-- INGEST_API_KEY does not match the backend"
    print(f"[client] live ingest rejected: {detail}")
    # 4xx won't succeed on retry; 5xx might, so treat it as a transient outage.
    return Result(status=REJECTED if 400 <= r.status_code < 500 else OFFLINE, detail=detail)


def backfill(items):
    """
    Replay buffered frames as history. `items` is a list of {ts, frame}.

    Returns (accepted, skipped): `accepted` is True if the backend took the batch;
    `skipped` is how many frames it discarded as implausible (an unsynced Pi clock).
    Skipped frames are counted as done — they will never be accepted, and retrying them
    forever would block the whole backlog.
    """
    payload = {"frames": [{"ts": it["ts"], **it["frame"]} for it in items]}
    try:
        r = requests.post(cfg.batch_url(), json=payload, headers=_HEADERS, timeout=cfg.HTTP_TIMEOUT)
    except requests.RequestException as e:
        print(f"[client] backfill unreachable ({type(e).__name__}) - keeping frames buffered")
        return False, 0

    if r.status_code != 200:
        print(f"[client] backfill rejected: HTTP {r.status_code} {r.text[:120]}")
        return False, 0

    try:
        body = r.json()
        skipped = int(body.get("skipped") or 0)
    except (ValueError, TypeError):
        skipped = 0
    if skipped:
        print(f"[client] backend skipped {skipped} backfilled frame(s) - check the Pi clock (NTP).")
    return True, skipped

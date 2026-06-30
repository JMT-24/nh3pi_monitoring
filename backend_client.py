"""
HTTP client for the backend ingest contract.

  send_live(frame)   -> POST /api/ingest        (drives control; returns command)
  backfill(items)    -> POST /api/ingest/batch  (history only; no control/SMS)

Both send the x-api-key header. Network failures return a sentinel instead of
raising, so the control loop can degrade gracefully (buffer + local safety).
"""

import requests

import nh3config as cfg

_HEADERS = {"x-api-key": cfg.INGEST_API_KEY}


class OfflineError(Exception):
    """Raised internally when the backend is unreachable."""


def send_live(frame):
    """
    Post the current frame. Returns the backend's `command` dict on success,
    or None if the backend was unreachable / rejected the frame.
    """
    try:
        r = requests.post(cfg.ingest_url(), json=frame, headers=_HEADERS, timeout=cfg.HTTP_TIMEOUT)
    except (requests.ConnectionError, requests.Timeout):
        return None
    if r.status_code != 200:
        print(f"[client] live ingest rejected: HTTP {r.status_code} {r.text[:120]}")
        return None
    try:
        return r.json().get("command")
    except ValueError:
        return None


def backfill(items):
    """
    Replay buffered frames as history. `items` is a list of {ts, frame}.
    Returns True if the backend accepted them, False if unreachable/rejected.
    """
    payload = {"frames": [{"ts": it["ts"], **it["frame"]} for it in items]}
    try:
        r = requests.post(cfg.batch_url(), json=payload, headers=_HEADERS, timeout=cfg.HTTP_TIMEOUT)
    except (requests.ConnectionError, requests.Timeout):
        return False
    if r.status_code != 200:
        print(f"[client] backfill rejected: HTTP {r.status_code} {r.text[:120]}")
        return False
    return True

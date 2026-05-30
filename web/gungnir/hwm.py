"""High-water-mark (last-successful-upload) tracking.

Writes a small JSON file to the per-tool config dir after each successful
upload so external monitoring (e.g. a lab-doctor scanner) can read it and
tell at a glance how stale the feeder is.

File layout::

    {
        "last_upload_iso": "2026-05-28T12:34:56Z",
        "last_upload_ts": 1716902096.0,
        "counters": {           # every counter the server returned
            "aircraft_imported": 4,
            "aircraft_already_seen": 6,
            ...
        },
        "last_upload_payload": {...}  # full server response for debugging
    }

The ``counters`` field is the agreed source of truth — consumers should
read from it rather than parsing ``last_upload_payload``. We intentionally
do NOT extract one number to a top-level field; "imported" means different
things to different slots (aircraft vs networks vs meshcore) and exposing
a single scalar mis-states reality for the slots that aren't aircraft.

Ported from wigle-to-wdgwars v1.0 with the single-scalar mistake corrected.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .diagnostics import KNOWN_COUNTERS
from .keys import config_dir

log = logging.getLogger(__name__)


def _path(tool: str) -> Path:
    return config_dir(tool) / "hwm.json"


def _extract_counters(payload: dict) -> dict:
    """Pull every known counter from the response into its own dict.

    Anything the server returned that we don't recognize is preserved
    in `last_upload_payload` — only known counters land in the structured
    `counters` field so consumers have a stable schema to read."""
    return {k: int(payload.get(k, 0) or 0) for k in KNOWN_COUNTERS if k in payload}


def record(tool: str, payload: dict) -> None:
    """Persist last-successful-upload watermark for visibility/monitoring."""
    p = _path(tool)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        d = {
            "last_upload_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_upload_ts": time.time(),
            "counters": _extract_counters(payload),
            "last_upload_payload": payload,
        }
        p.write_text(json.dumps(d, indent=2))
    except Exception as e:
        log.warning("HWM persist failed: %s", e)


def read(tool: str) -> dict | None:
    """Read the current HWM, or None if it doesn't exist / is unreadable.
    Useful for external monitoring scanners that don't want to parse it
    themselves."""
    p = _path(tool)
    try:
        return json.loads(p.read_text())
    except Exception:
        return None

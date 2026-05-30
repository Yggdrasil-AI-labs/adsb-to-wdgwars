"""Server-cooldown persistence.

When wdgwars.pl returns a 429 (rate limit) with a `retry_after` field, the
caller should not just sleep — it should persist the deadline so the next
cron invocation also respects it. Otherwise a per-minute cron will hammer
the server while a previous upload is still being queued/processed.

Ported from wigle-to-wdgwars v1.0. State lives in the per-tool config dir
under `cooldown.json` to keep it next to the API key.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .keys import config_dir

log = logging.getLogger(__name__)


def _path(tool: str) -> Path:
    return config_dir(tool) / "cooldown.json"


def check_and_sleep(tool: str, *, cap_seconds: float = 900) -> None:
    """If a prior 429 set a cooldown deadline, sleep until it passes.

    Capped at `cap_seconds` (default 15 minutes) so a stuck deadline can't
    deadlock an automated runner. Silently no-ops if the cooldown file is
    missing or unreadable.
    """
    p = _path(tool)
    try:
        d = json.loads(p.read_text())
        deadline = float(d.get("until", 0))
    except Exception:
        return
    delta = deadline - time.time()
    if delta > 0:
        wait = min(delta, cap_seconds)
        log.info("respecting server cooldown, sleeping %ds", int(wait))
        time.sleep(wait)


def record(tool: str, seconds: float) -> None:
    """Persist a cooldown deadline `seconds` from now. Passing 0 or
    negative removes the file (clears the cooldown)."""
    p = _path(tool)
    if not seconds or seconds <= 0:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"until": time.time() + float(seconds)}))
    except Exception as e:
        log.warning("cooldown persist failed: %s", e)

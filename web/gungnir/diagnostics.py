"""Failure-mode detectors for wdgwars.pl uploads.

The big one: **silent drop**. The server can return `HTTP 200 ok:true` while
having rejected every record server-side. This shows up as zero across all
known counters on a non-empty chunk. Shipped originally in Muninn v1.11.1
after locosp's v4 server patch tightened type validation and silently
dropped every aircraft with an unrecognized emitter category.

Lesson learned the hard way: any future tool that POSTs to wdgwars.pl
without checking this is *guaranteed* to ship a silent regression eventually,
because server-side validation is opaque and changes without notice.
"""
from __future__ import annotations

from dataclasses import dataclass


# Every counter the server has ever been observed to return. New counters
# can be added here without code changes elsewhere — the check is "did
# ANY counter come back non-zero", so unknown new counters are forward-
# compatible.
KNOWN_COUNTERS = (
    "aircraft_imported",
    "aircraft_already_seen",
    "imported",
    "captured",
    "updated",
    "duplicates",
    "merged_samples",
    "already_seen",
    "no_gps",
    "bad_rows",
)


@dataclass
class SilentDrop:
    """Returned by `check_silent_drop()` when the failure pattern is detected.

    Attributes:
        sent_count: how many records the caller sent in this chunk
        response: the parsed JSON response from the server (or raw dict)
        raw_text_excerpt: the raw response body, truncated to the limit
            passed to `check_silent_drop()` (typically 800 chars). The
            "excerpt" suffix is load-bearing — do not use this field as
            if it were the full body.
    """
    sent_count: int
    response: dict
    raw_text_excerpt: str = ""


def check_silent_drop(
    status: int,
    response: dict,
    sent_count: int,
    *,
    raw_text_excerpt: str = "",
) -> SilentDrop | None:
    """Detect the HTTP-200-ok-true-zero-counters silent-drop pattern.

    Returns a `SilentDrop` if the pattern matches, else None. Callers
    should treat a SilentDrop as a soft failure: log loudly (with the raw
    response so the cause is visible) and consider returning a non-zero
    exit code so cron jobs surface the problem.

    The ``raw_text_excerpt`` parameter is the caller's already-truncated
    snippet of the response body; gungnir does not truncate further. The
    name is load-bearing — keep it explicit at the call site.
    """
    if status != 200:
        return None
    if not response.get("ok"):
        return None
    if sent_count <= 0:
        return None
    if any(response.get(k) for k in KNOWN_COUNTERS):
        return None
    return SilentDrop(sent_count=sent_count, response=response,
                      raw_text_excerpt=raw_text_excerpt)

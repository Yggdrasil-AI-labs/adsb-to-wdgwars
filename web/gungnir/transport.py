"""HTTPS transport to wdgwars.pl.

Responsibilities (in order of importance):

1. **Signed POST** to ``/api/upload/`` using the HMAC envelope from
   :mod:`gungnir.envelope`.
2. **Retry transient failures** (5xx and network errors) with exponential
   backoff — 3 attempts by default, starting at 2s.
3. **Bail the whole batch on 429** by raising :class:`BatchAborted`. Cron
   jobs that ignored a rate-limit and kept pushing more chunks would only
   deepen the cooldown.
4. **Silent-drop detection** on every accepted response (the v1.11.1
   lesson — see :mod:`gungnir.diagnostics`).
5. **Inter-chunk cooldown** to keep the server from drowning under a
   30-chunk batch hitting it back-to-back.
6. **Key redaction** in every log line via :func:`gungnir.keys.scrub`.

The library never configures logging handlers. Consumers wire that up
themselves; gungnir just emits to ``gungnir.transport`` and the other
module-level loggers.

Tests that need to skip backoff sleeps should ``mock.patch`` the module's
``time.sleep`` attribute (e.g.
``mock.patch("gungnir.transport.time.sleep")``); the library has no
test-only hook in its public signature.
"""
from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.request

from . import cooldown, diagnostics, hwm
from .envelope import build_envelope, build_payload
from .keys import scrub

log = logging.getLogger(__name__)

# Explicit SSL context. urllib.request defaults to system trust + full cert
# verification since Python 3.4.3 (PEP 476), but being explicit makes the
# security posture obvious in code review.
SSL_CTX = ssl.create_default_context()

# Defaults exposed at module level so consumers can override globally if
# they ever need to (e.g. a test that wants near-instant backoff).
DEFAULT_TIMEOUT = 120.0
DEFAULT_WHOAMI_TIMEOUT = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE = 2.0
DEFAULT_CHUNK_COOLDOWN = 1.0


def _user_agent(tool: str, version: str, extra: str | None = None) -> str:
    """Build the User-Agent header value.

    Bare form: ``<tool>/<version>``. With ``extra`` (typically a repo URL),
    appends ``(+<extra>)`` per common bot-UA convention so server admins
    can trace traffic back to a source.
    """
    base = f"{tool}/{version}"
    if extra:
        return f"{base} (+{extra})"
    return base


class BatchAborted(Exception):
    """Raised by :func:`send_chunk` when the whole batch should stop, not
    just this chunk. Primarily 429 — continuing would deepen the cooldown.

    Attributes:
        reason: short human-readable cause (e.g. "rate limited (429)")
        retry_after: seconds the server asked us to wait, if known
    """

    def __init__(self, reason: str, *, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


def whoami(
    tool: str,
    version: str,
    me_url: str,
    key: str,
    *,
    timeout: float = DEFAULT_WHOAMI_TIMEOUT,
    user_agent_extra: str | None = None,
) -> int:
    """GET the ``/api/me`` endpoint to validate ``key``.

    Returns shell exit code: 0 on success, 1 on any failure. Never echoes
    the API key, even in error paths.

    ``timeout`` defaults to 30s (whoami should be fast). Pass an explicit
    value to override; the function does NOT silently clamp the caller's
    choice.
    """
    ua = _user_agent(tool, version, user_agent_extra)
    req = urllib.request.Request(
        me_url,
        headers={
            "X-API-Key": key,
            "User-Agent": ua,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
            if not data.get("ok"):
                err = data.get("error", "unknown")
                log.error("[%s] key rejected: %s", tool, scrub(err, key))
                return 1
            log.info("[%s] key OK — user=%s", tool, data.get("username"))
            log.info("[%s]   wifi=%s ble=%s aircraft=%s total=%s",
                     tool,
                     data.get("wifi", 0), data.get("ble", 0),
                     data.get("aircraft", 0), data.get("total", 0))
            return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        log.error("[%s] HTTP %d: %s", tool, e.code, scrub(body, key))
        return 1
    except Exception as e:
        log.error("[%s] whoami failed: %s", tool, scrub(str(e), key))
        return 1


def send_chunk(
    tool: str,
    version: str,
    api_url: str,
    key: str,
    payload: dict,
    *,
    sent_count: int,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    user_agent_extra: str | None = None,
) -> tuple[int, dict]:
    """POST one HMAC-wrapped payload to the signed endpoint.

    Retries transient failures (5xx and network errors) with exponential
    backoff. Returns ``(rc, parsed_response)`` where rc==0 on success, 1
    on permanent failure.

    Raises :class:`BatchAborted` on 429 — the caller's batch loop should
    stop, not just skip this chunk.

    The silent-drop pattern (HTTP 200 ok:true with every counter zero)
    surfaces as rc=1, not BatchAborted — only this chunk is suspect, not
    the whole batch.

    To skip backoff sleeps in tests, mock ``gungnir.transport.time.sleep``.
    """
    if sent_count < 0:
        raise ValueError(f"sent_count must be >= 0, got {sent_count}")
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    envelope = build_envelope(payload, key)
    body = json.dumps(envelope).encode()
    ua = _user_agent(tool, version, user_agent_extra)

    if dry_run:
        log.info("DRY-RUN, would POST %d B to %s", len(body), api_url)
        return 0, {"ok": True, "dry_run": True}

    last_response: dict = {}

    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            api_url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": key,
                "User-Agent": ua,
                "Accept": "application/json",
            },
        )
        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
                txt = resp.read().decode("utf-8", "replace")
                data = json.loads(txt) if txt else {}
                elapsed = time.monotonic() - t0
                last_response = data

                sd = diagnostics.check_silent_drop(
                    resp.status, data, sent_count, raw_text_excerpt=txt[:800],
                )
                if sd is not None:
                    log.warning(
                        "HTTP 200 ok:true but every counter zero — %d records "
                        "sent. Raw response: %s",
                        sent_count, scrub(sd.raw_text_excerpt, key),
                    )
                    return 1, data

                log.debug("accepted in %.2fs", elapsed)
                return 0, data

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")[:400]
            try:
                last_response = json.loads(err_body) if err_body else {}
            except Exception:
                last_response = {}

            if e.code == 429:
                wait = float(last_response.get("retry_after") or 60)
                log.warning("[%s] 429 rate limited, recording cooldown of %ds",
                            tool, int(wait))
                cooldown.record(tool, wait)
                raise BatchAborted("rate limited (429)", retry_after=wait) from None

            if 500 <= e.code < 600 and attempt < max_attempts:
                wait = backoff_base * (2 ** (attempt - 1))
                log.warning("[%s] HTTP %d, retrying in %.1fs (attempt %d/%d): %s",
                            tool, e.code, wait, attempt, max_attempts,
                            scrub(err_body, key))
                time.sleep(wait)
                continue

            log.error("[%s] rejected by wdgwars.pl (HTTP %d): %s",
                      tool, e.code, scrub(err_body, key))
            return 1, last_response

        except urllib.error.URLError as e:
            if attempt < max_attempts:
                wait = backoff_base * (2 ** (attempt - 1))
                log.warning("[%s] network error, retrying in %.1fs (attempt %d/%d): %s",
                            tool, wait, attempt, max_attempts, scrub(str(e), key))
                time.sleep(wait)
                continue
            log.error("[%s] network error after %d attempts: %s",
                      tool, max_attempts, scrub(str(e), key))
            return 1, {}

        except Exception as e:
            log.error("[%s] upload error: %s", tool, scrub(str(e), key))
            return 1, {}

    return 1, last_response


def send(
    tool: str,
    version: str,
    api_url: str,
    key: str,
    *,
    aircraft: list[dict] | None = None,
    networks: list[dict] | None = None,
    meshcore_nodes: list[dict] | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    chunk_cooldown: float = DEFAULT_CHUNK_COOLDOWN,
    user_agent_extra: str | None = None,
) -> int:
    """High-level send: take records, batch them, POST each batch, record HWM.

    **Contract:** caller must supply exactly one of ``aircraft``,
    ``networks``, or ``meshcore_nodes``. Zero or more than one raises
    ``ValueError`` — the wire shape allows mixed payloads but no real
    feeder needs that, so the API forbids it to keep the contract clear.

    Returns 0 on success, 1 if any chunk failed (including silent drops).
    A 429 from the server stops the whole batch immediately and returns 1
    after recording the cooldown.

    An empty list for the supplied slot is a no-op (logs and returns 0)
    rather than an error — cron jobs that have nothing to upload are
    expected, not exceptional.

    ``chunk_cooldown`` is the sleep between chunks (default 1s). Set to 0
    to disable. No sleep is added after the final chunk.
    """
    slots = {
        "aircraft": aircraft,
        "networks": networks,
        "meshcore_nodes": meshcore_nodes,
    }
    provided = {name: lst for name, lst in slots.items() if lst is not None}

    if not provided:
        raise ValueError(
            "send() requires exactly one of aircraft, networks, or meshcore_nodes"
        )
    if len(provided) > 1:
        raise ValueError(
            f"send() accepts exactly one slot; got {sorted(provided)}"
        )

    slot_name, records = next(iter(provided.items()))

    if not records:
        log.info("nothing to upload")
        return 0

    cooldown.check_and_sleep(tool)

    rc = 0
    total_sent = 0
    total_imported = 0
    total_seen = 0
    n_chunks = (len(records) - 1) // batch_size + 1

    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        chunk_no = i // batch_size + 1
        is_last = (i + batch_size >= len(records))
        payload = build_payload(**{slot_name: chunk})
        log.info("chunk %d/%d: %d %s", chunk_no, n_chunks, len(chunk), slot_name)

        try:
            chunk_rc, data = send_chunk(
                tool, version, api_url, key, payload,
                sent_count=len(chunk), dry_run=dry_run,
                timeout=timeout, max_attempts=max_attempts,
                user_agent_extra=user_agent_extra,
            )
        except BatchAborted as e:
            log.error("[%s] batch aborted: %s (chunk %d/%d, %d records not sent)",
                      tool, e.reason, chunk_no, n_chunks,
                      len(records) - i)
            return 1

        rc |= chunk_rc

        if chunk_rc == 0 and not dry_run:
            imp = int(data.get("aircraft_imported", data.get("imported", 0)) or 0)
            seen = int(data.get("aircraft_already_seen", data.get("already_seen", 0)) or 0)
            total_sent += len(chunk)
            total_imported += imp
            total_seen += seen
            hwm.record(tool, data)
            badges = data.get("new_badges") or []
            if badges:
                log.info("new badges: %s", badges)

        # Polite inter-chunk delay. No sleep after the final chunk —
        # the cron job is done and shouldn't wait.
        if not is_last and chunk_cooldown > 0 and not dry_run:
            time.sleep(chunk_cooldown)

    if not dry_run and rc == 0:
        log.info("upload accepted by wdgwars.pl. sent %d %s. "
                 "%d added to your score, %d already on file.",
                 total_sent, slot_name, total_imported, total_seen)
    return rc

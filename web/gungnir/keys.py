"""API-key resolution, persistence, and redaction for the WDGoWars feeders.

Key lookup precedence (highest first):
    1. cli_key argument (typically populated from --key)
    2. $WDGWARS_API_KEY env var
    3. The per-tool config file at <config_dir>/api.key

The config dir is OS-appropriate and per-tool:
    Windows:  %APPDATA%/<tool>/
    Linux:    $XDG_CONFIG_HOME/<tool>/  (falls back to ~/.config/<tool>/)
    macOS:    ~/.config/<tool>/

This file also exposes `scrub()` — a tiny helper that redacts the API key
from any string before it gets logged. Defensive: if the server ever
echoes the key in an error message, we don't want to spill it.

Extracted from muninn.py v1.11.1 (lines 105-283, 371-405) with one
deliberate tightening: scrub() now redacts on any non-empty match,
where Muninn required key length > 8. The threshold protected against
nothing real.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def config_dir(tool: str) -> Path:
    """Per-tool config directory. OS-appropriate location."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / tool
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / tool


def key_path(tool: str) -> Path:
    """Where the API key is stored on disk for `tool`."""
    return config_dir(tool) / "api.key"


def load_key(tool: str, cli_key: str | None = None) -> str:
    """Resolve API key per documented precedence. Returns "" if not found
    (callers decide whether that's fatal)."""
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("WDGWARS_API_KEY", "").strip()
    if env:
        return env
    p = key_path(tool)
    if p.exists():
        try:
            return p.read_text().strip()
        except Exception as e:
            log.warning("could not read %s: %s", p, e)
    return ""


def save_key(tool: str, key: str) -> None:
    """Persist ``key`` to the per-tool key file safely.

    Defenses (extracted from Muninn v1.11.1's hardened save_key):

    - **Refuse to write through a symlink.** If ``api.key`` already
      exists as a symlink, raise ``KeyFileSymlinkError`` rather than
      follow it. Closes a redirect-to-arbitrary-file attack vector for
      anyone who can plant a symlink in the config dir.
    - **Create with 0o600 atomically.** Open with ``O_WRONLY|O_CREAT|
      O_TRUNC`` and mode 0o600 *before* writing the secret, so the file
      is never world-readable — not even for the microseconds between
      ``write_text`` and a subsequent ``chmod``.
    - **Trailing newline.** POSIX convention; mirrors Muninn 1.x's file
      shape so existing keys round-trip identically.

    Windows uses NTFS ACLs and ignores the mode bits; the user profile
    dir is already not world-readable by default.
    """
    p = key_path(tool)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink():
        raise KeyFileSymlinkError(
            f"refusing to write through symlink: {p} -> {os.readlink(p)}"
        )
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (key.strip() + "\n").encode())
    finally:
        os.close(fd)
    # Belt-and-suspenders chmod for the case where the file already
    # existed with looser perms before this call (O_CREAT mode only
    # applies on creation, not truncation).
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass


class KeyFileSymlinkError(OSError):
    """Raised by :func:`save_key` if the target key file is a symlink.
    Surfaces what Muninn 1.x previously called ``sys.exit`` with — now
    callers can catch and decide how to surface it (CLI exit, log, etc.)."""


def scrub(text: str, key: str) -> str:
    """Redact `key` from `text` before logging.

    Redacts on any non-empty key that appears in the text. The redaction
    shape is "<first-4>…<last-4>" for keys longer than 8 chars, otherwise
    the literal string "…" — short keys can't be partially revealed
    without exposing most of the secret.

    No-op if key is empty or not present in the text (cheap to call
    defensively before every log line).
    """
    if not key or key not in text:
        return text
    if len(key) > 8:
        return text.replace(key, key[:4] + "…" + key[-4:])
    return text.replace(key, "…")

"""gungnir — shared transport client for the WDGoWars (wdgwars.pl) feeders.

    *Odin's spear. Always hits its target.*

Public API:

    import gungnir, logging
    logging.basicConfig(level=logging.INFO)  # consumer-controlled

    client = gungnir.Client(
        tool="my-feeder",
        version="1.0.0",
        user_agent_extra="https://github.com/me/my-feeder",  # optional
    )
    key = client.load_key(cli_key=None)
    client.whoami(key)
    client.send(key, aircraft=records)

The library uses the standard ``logging`` module; it never configures
handlers itself. Consumers route logs however they like.

Used by Muninn (ADS-B), Heimdall (meshcore), and wigle-to-wdgwars (WiFi/BLE).
"""
from __future__ import annotations

from .__version__ import __version__
from . import cooldown, diagnostics, envelope, hwm, keys, transport
from .diagnostics import SilentDrop, check_silent_drop
from .envelope import build_envelope, build_payload
from .keys import KeyFileSymlinkError
from .transport import (
    BatchAborted,
    DEFAULT_BACKOFF_BASE,
    DEFAULT_CHUNK_COOLDOWN,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT,
    DEFAULT_WHOAMI_TIMEOUT,
)

DEFAULT_API_URL = "https://wdgwars.pl/api/upload/"
ME_API_URL = "https://wdgwars.pl/api/me"

# Characters that must not appear in a tool name — they'd let a caller
# escape the config dir or create invalid paths on Windows.
_TOOL_NAME_FORBIDDEN = ("/", "\\", "\x00", "..")

__all__ = [
    "BatchAborted",
    "Client",
    "DEFAULT_API_URL",
    "DEFAULT_BACKOFF_BASE",
    "DEFAULT_CHUNK_COOLDOWN",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_TIMEOUT",
    "DEFAULT_WHOAMI_TIMEOUT",
    "KeyFileSymlinkError",
    "ME_API_URL",
    "SilentDrop",
    "__version__",
    "build_envelope",
    "build_payload",
    "check_silent_drop",
    "cooldown",
    "diagnostics",
    "envelope",
    "hwm",
    "keys",
    "transport",
]


def _validate_tool_name(tool: str) -> None:
    """Reject tool names that would let a caller escape the config dir or
    create an invalid path. Defensive: tools self-select their name, so
    there's no real attack vector, but the check is free and the error
    is clearer than whatever the OS would raise later.
    """
    if not tool:
        raise ValueError("tool name is required")
    if not isinstance(tool, str):
        raise TypeError(f"tool must be str, got {type(tool).__name__}")
    for bad in _TOOL_NAME_FORBIDDEN:
        if bad in tool:
            raise ValueError(
                f"tool name {tool!r} contains forbidden sequence {bad!r}"
            )


class Client:
    """Bundles per-tool identity (name + version) and transport defaults
    so callers don't have to thread them through every method. Stateless
    beyond construction args — safe to create one per program and reuse.

    :param tool: short tool identifier (e.g. ``"muninn"``). Used in the
        User-Agent header, log lines, and the per-tool config dir. Must
        not contain ``/``, ``\\``, ``..``, or null bytes.
    :param version: tool version string (e.g. ``"1.11.1"``).
    :param api_url: signed-JSON upload endpoint. Defaults to the wdgwars.pl
        production URL; override for tests/staging.
    :param me_url: identity endpoint. Defaults to the wdgwars.pl prod URL.
    :param timeout: per-request timeout in seconds for ``send()``.
        Default 120.
    :param whoami_timeout: per-request timeout for ``whoami()``. Default
        30s — whoami should be fast.
    :param max_attempts: retry attempts on transient errors (5xx + network).
        Default 3. 4xx errors are not retried; 429 raises BatchAborted.
    :param chunk_cooldown: seconds to sleep between chunks in a batched
        ``send()`` call. Default 1.0. Set to 0 to disable.
    :param user_agent_extra: optional string (typically a repo URL)
        appended to the User-Agent header as ``(+<extra>)``. Lets server
        admins trace your traffic.
    """

    def __init__(
        self,
        tool: str,
        version: str,
        *,
        api_url: str = DEFAULT_API_URL,
        me_url: str = ME_API_URL,
        timeout: float = DEFAULT_TIMEOUT,
        whoami_timeout: float = DEFAULT_WHOAMI_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        chunk_cooldown: float = DEFAULT_CHUNK_COOLDOWN,
        user_agent_extra: str | None = None,
    ) -> None:
        _validate_tool_name(tool)
        if not version:
            raise ValueError("version is required")
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")
        if whoami_timeout <= 0:
            raise ValueError(f"whoami_timeout must be > 0, got {whoami_timeout}")
        if chunk_cooldown < 0:
            raise ValueError(f"chunk_cooldown must be >= 0, got {chunk_cooldown}")

        self.tool = tool
        self.version = version
        self.api_url = api_url
        self.me_url = me_url
        self.timeout = float(timeout)
        self.whoami_timeout = float(whoami_timeout)
        self.max_attempts = int(max_attempts)
        self.chunk_cooldown = float(chunk_cooldown)
        self.user_agent_extra = user_agent_extra

    def __repr__(self) -> str:
        return (f"Client(tool={self.tool!r}, version={self.version!r}, "
                f"timeout={self.timeout}, max_attempts={self.max_attempts})")

    # ── Key management ─────────────────────────────────────────────────
    def load_key(self, cli_key: str | None = None) -> str:
        """Resolve API key (CLI → env → file). Returns ``""`` if not found."""
        return keys.load_key(self.tool, cli_key)

    def save_key(self, key: str) -> None:
        """Persist API key to the per-tool config file."""
        keys.save_key(self.tool, key)

    # ── Server interaction ─────────────────────────────────────────────
    def whoami(self, key: str, *, timeout: float | None = None) -> int:
        """GET ``/api/me`` to validate ``key``. Returns shell exit code.

        ``timeout`` defaults to the Client's ``whoami_timeout`` (30s). Pass
        an explicit value to override — no silent clamping.
        """
        return transport.whoami(
            self.tool, self.version, self.me_url, key,
            timeout=self.whoami_timeout if timeout is None else float(timeout),
            user_agent_extra=self.user_agent_extra,
        )

    def send(
        self,
        key: str,
        *,
        aircraft: list[dict] | None = None,
        networks: list[dict] | None = None,
        meshcore_nodes: list[dict] | None = None,
        batch_size: int = 500,
        dry_run: bool = False,
        timeout: float | None = None,
        max_attempts: int | None = None,
        chunk_cooldown: float | None = None,
    ) -> int:
        """POST records to the signed endpoint.

        Caller must supply exactly one of ``aircraft``/``networks``/
        ``meshcore_nodes`` — see :func:`transport.send` for details.

        Returns shell exit code (0 ok, 1 fail).
        """
        return transport.send(
            self.tool, self.version, self.api_url, key,
            aircraft=aircraft, networks=networks, meshcore_nodes=meshcore_nodes,
            batch_size=batch_size, dry_run=dry_run,
            timeout=self.timeout if timeout is None else float(timeout),
            max_attempts=self.max_attempts if max_attempts is None else int(max_attempts),
            chunk_cooldown=(self.chunk_cooldown if chunk_cooldown is None
                            else float(chunk_cooldown)),
            user_agent_extra=self.user_agent_extra,
        )

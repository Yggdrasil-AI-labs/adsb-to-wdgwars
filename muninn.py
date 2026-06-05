#!/usr/bin/env python3
"""muninn.py — convert ADS-B capture files to the WDGoWars aircraft
upload JSON, and optionally POST directly to the server.

Linked by the WDGoWars portal as the recommended advanced converter
for input formats outside the built-in importer's scope (AVR raw
Mode-S, Mode-S Beast binary, GDL-90 binary, gzipped tar1090, NDJSON,
BaseStation .sqb, and the HMAC-signed /api/upload/ route).

Auto-detects the input format from the first non-empty line:

  AVR raw Mode-S         lines like '*8D4840D6202CC371C32CE0576098;'
                         needs pyModeS to decode position frames (DF17)

  SBS-1 / BaseStation    CSV starting with 'MSG,' (dump1090 --net or readsb)

  dump1090 aircraft.json a JSON object with an 'aircraft' array (one snapshot
                         per file) OR a stream of JSON objects, one per line

  Generic CSV (h4m etc.) columns like icao,lat,lon,alt,callsign,timestamp
                         in some order — configure with --csv-format if needed

Each unique ICAO is kept once with its **most recent** position. Records
missing lat/lon are dropped (WDGoWars rejects them server-side anyway).

Output is a JSON array of:
  {"icao": "<UPPER 24-bit hex>",
   "callsign": "<flight string, may be empty>",
   "lat": <float>, "lon": <float>,
   "alt_ft": <int>, "speed_kt": <int>, "heading": <int>,
   "first_seen": "YYYY-MM-DD HH:MM:SS",  # UTC
   "type": "ADSB"}

If --upload is given, the array is wrapped in the documented HMAC-SHA256
envelope and POSTed to https://wdgwars.pl/endpoint/upload/ (the trailing
slash is required, without it the server rejects every payload as a
replay). The /endpoint/* prefix is a server-side alias of /api/* that
bypasses Cloudflare's per-IP L7 DDoS protection — at batch scale, /api/*
intermittently 429s before the request reaches the origin. Override with
--api-url if you need to force /api/upload/ for any reason.

Examples
--------
  # Just convert to JSON
  python3 muninn.py mycapture.txt --out vessels.json

  # Convert and upload (API key from env or --key)
  WDGWARS_API_KEY=YOURKEY python3 muninn.py mycapture.txt --upload

  # Show what would be sent without sending
  python3 muninn.py mycapture.txt --upload --dry-run

License: MIT
"""
from __future__ import annotations

__version__ = "2.0.12"
GITHUB_REPO = "HiroAlleyCat/adsb-to-wdgwars"
GITHUB_URL = f"https://github.com/{GITHUB_REPO}"

# Set by main() when --quiet is passed. Module-level so helpers can read it
# without plumbing the flag through every call site.
_QUIET = False

# Populated by _process_one_file() with the parent directory of every JSON
# file actually written. `--open` reads this at end-of-run to pop them open.
_OUT_DIRS_WRITTEN: set = set()


def _open_folder(p) -> bool:
    """Open `p` in the OS file manager. Best-effort, returns True on success."""
    from pathlib import Path as _P
    import subprocess as _sp
    p = _P(p)
    if not p.exists():
        return False
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            _sp.run(["open", str(p)], check=False)
        else:
            _sp.run(["xdg-open", str(p)], check=False)
        return True
    except Exception:
        return False

import argparse
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import gungnir
except ModuleNotFoundError:
    # Post-v2.0.8 footgun: deps live in `.venv/` next to this script.
    # `python3 muninn.py ...` from system Python won't find them. Detect
    # that case and point the user at the venv / run.sh instead of the
    # bare traceback that just says "No module named 'gungnir'".
    import sys as _sys
    from pathlib import Path as _Path
    _script_dir = _Path(__file__).resolve().parent
    _venv_py = _script_dir / ".venv" / "bin" / "python"
    if not _venv_py.exists():
        _venv_py = _script_dir / ".venv" / "Scripts" / "python.exe"
    if _venv_py.exists():
        print(
            "\n[muninn] missing dependency: gungnir.\n"
            "[muninn] deps live in the project venv, not system Python.\n"
            "[muninn] re-run with the venv interpreter, or use the run.sh "
            "wrapper:\n"
            f"[muninn]   {_venv_py} muninn.py ...\n"
            "[muninn]   ./run.sh ...\n",
            file=_sys.stderr,
        )
    else:
        print(
            "\n[muninn] missing dependency: gungnir.\n"
            "[muninn] run ./setup.sh (or python3 -m pip install -r "
            "requirements.txt in a venv) to install it.\n",
            file=_sys.stderr,
        )
    _sys.exit(1)

# Muninn is a CLI tool — configure logging so cron logs look like they
# did in v1.x (plain-message-per-line to stderr). Library users who set
# up their own root logger before calling into muninn override this.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

# Single Client instance for the lifetime of the process. Stateless;
# bundles per-tool identity (User-Agent, config dir) so we don't have to
# thread it through every call site.
_client = gungnir.Client(
    tool="muninn",
    version=__version__,
    user_agent_extra=GITHUB_URL,
)

# Re-exported from gungnir so existing call-sites (notably the argparse
# default for --api-url) keep working without change. Source of truth
# lives in gungnir; touch it there if the server ever moves. As of
# gungnir v0.1.2 (pinned in requirements.txt), DEFAULT_API_URL points
# at /endpoint/upload/ — a server-side alias of /api/upload/ that
# bypasses Cloudflare's per-IP L7 DDoS rate-limit. ME_API_URL stays
# on /api/me (single-call, not affected by burst limits).
DEFAULT_API_URL = gungnir.DEFAULT_API_URL
ME_API_URL = gungnir.ME_API_URL

# Persistent API key location — XDG-style on Linux/Mac, %APPDATA% on Windows.
def _config_dir() -> Path:
    """Per-tool config dir. Delegates to gungnir so the path matches
    every other tool in the family. For ``tool="muninn"`` this is
    ``~/.config/muninn/`` on POSIX and ``%APPDATA%/muninn/`` on Windows
    — byte-identical to Muninn 1.x's path."""
    return gungnir.keys.config_dir("muninn")


def _key_path() -> Path:
    return gungnir.keys.key_path("muninn")


def _folders_config_path() -> Path:
    return _config_dir() / "folders.json"


def _desktop_path() -> Path | None:
    """Find the user's Desktop folder if it exists. Returns None otherwise."""
    candidates = [
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",   # common on Windows w/ OneDrive
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _load_folder_prefs() -> dict | None:
    """Returns saved folder prefs ({'input': str, 'output': str}) or None."""
    p = _folders_config_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _save_folder_prefs(input_dir: Path, output_dir: Path) -> None:
    _config_dir().mkdir(parents=True, exist_ok=True)
    _folders_config_path().write_text(json.dumps({
        "input":  str(input_dir),
        "output": str(output_dir),
    }, indent=2))


def _create_desktop_shortcut(muninn_folder: Path) -> bool:
    """Create a desktop shortcut on Windows that opens a terminal in the
    repo and runs muninn.py. Uses PowerShell's COM bridge (no deps).
    Returns True on success, False if not supported or failed."""
    if os.name != "nt":
        return False
    desktop = _desktop_path()
    if not desktop:
        return False
    script_dir = Path(__file__).resolve().parent
    ico = script_dir / "assets" / "muninn.ico"
    lnk = desktop / "Muninn.lnk"
    # Find python launcher — prefer pythonw-free py launcher, fall back to python
    py = sys.executable
    ps_script = f'''
$WshShell = New-Object -comObject WScript.Shell
$s = $WshShell.CreateShortcut("{lnk}")
$s.TargetPath = "cmd.exe"
$s.Arguments = '/k "cd /d ""{script_dir}"" && ""{py}"" muninn.py & pause"'
$s.WorkingDirectory = "{script_dir}"
$s.IconLocation = "{ico}"
$s.Description = "Muninn — ADS-B to WDGoWars converter"
$s.Save()
'''
    import subprocess
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and lnk.exists()
    except Exception:
        return False


def _prompt_yes_no_simple(question: str, default: bool = True) -> bool:
    """Mini y/n prompt for use outside interactive_setup().

    Always emits a newline after the answer when stdin is piped so the
    next line of output doesn't collide with the prompt. Interactive
    TTY input gets its own newline from the terminal; piped input
    doesn't, which glues section headers onto the prompt line.
    """
    suffix = " [Y/n] " if default else " [y/N] "
    piped = not sys.stdin.isatty()
    try:
        print(question + suffix, end="", flush=True, file=sys.stderr)
        ans = sys.stdin.readline().strip().lower()
        if piped:
            print("", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("", file=sys.stderr)
        return default
    if ans == "":
        return default
    return ans in ("y", "yes")


def _prompt_folder_choice(script_dir: Path) -> tuple[Path, Path]:
    """Ask the user where they want input/output folders. Returns
    (input_dir, output_dir). Saves the choice for next time. If they pick
    Desktop, also offers to create a desktop shortcut with the raven icon."""
    print("", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    print(" Muninn — first-time folder setup", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    print("", file=sys.stderr)
    print(" Where would you like your input/output folders?", file=sys.stderr)
    print("", file=sys.stderr)
    print(f"   1) Right here:  {script_dir / 'input'}", file=sys.stderr)
    print(f"                   {script_dir / 'output'}", file=sys.stderr)

    desktop = _desktop_path()
    if desktop:
        muninn_folder = desktop / "Muninn"
        print(f"   2) On Desktop:  {muninn_folder}  (with input/ and output/ inside)",
              file=sys.stderr)
        choices = "[1/2]"
    else:
        choices = "[1]"

    print("", file=sys.stderr)
    chose_desktop = False
    while True:
        try:
            print(f" Choose {choices} (default: 1): ", end="", flush=True,
                  file=sys.stderr)
            ans = sys.stdin.readline().strip()
        except (KeyboardInterrupt, EOFError):
            ans = ""
        if ans in ("", "1"):
            in_dir, out_dir = script_dir / "input", script_dir / "output"
            break
        if ans == "2" and desktop:
            muninn_folder = desktop / "Muninn"
            in_dir = muninn_folder / "input"
            out_dir = muninn_folder / "output"
            chose_desktop = True
            break
        print(" (please answer 1 or 2)", file=sys.stderr)

    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    _save_folder_prefs(in_dir, out_dir)
    print("", file=sys.stderr)
    print(f" ✓ Saved. Drop ADS-B files in: {in_dir}", file=sys.stderr)
    print(f"          Results will land in: {out_dir}", file=sys.stderr)
    print("", file=sys.stderr)

    # Offer desktop shortcut on Windows
    if chose_desktop and os.name == "nt":
        if _prompt_yes_no_simple(
                " Also create a desktop shortcut (raven icon, "
                "double-click to run)?", default=True):
            if _create_desktop_shortcut(muninn_folder):
                print(f" ✓ Created Muninn.lnk on your Desktop. Double-click "
                      f"to convert anything in input/.", file=sys.stderr)
            else:
                print(" (couldn't create shortcut — no big deal, "
                      "command-line still works)", file=sys.stderr)
            print("", file=sys.stderr)

    return in_dir, out_dir


def load_key(cli_key: str | None) -> str:
    """Resolve API key in priority order (delegates to gungnir):
    1. --key CLI flag
    2. $WDGWARS_API_KEY env var
    3. ~/.config/muninn/api.key (saved via --save-key)
    """
    return _client.load_key(cli_key)


def _prompt_yes_no(question: str, default: bool = True) -> bool:
    """Ask a y/n question on stderr. Returns True for yes, False for no.

    On EOF / Ctrl+C, returns the default so non-interactive runs don't
    hang. Always emits a newline after the answer when stdin is piped
    so the next section header doesn't collide with the prompt line —
    interactive TTY input gets its newline from the terminal, piped
    input doesn't.
    """
    suffix = " [Y/n] " if default else " [y/N] "
    piped = not sys.stdin.isatty()
    while True:
        try:
            print(question + suffix, end="", flush=True, file=sys.stderr)
            line = sys.stdin.readline()
            if not line:  # EOF
                print("", file=sys.stderr)
                return default
            ans = line.strip().lower()
            if piped:
                print("", file=sys.stderr)
        except (KeyboardInterrupt, EOFError):
            print("", file=sys.stderr)
            return default
        if ans == "":
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print(" (please answer y or n)", file=sys.stderr)


def interactive_setup() -> int:
    """First-run setup. Asks yes/no whether to configure an API key, then
    prompts for it, validates it against /api/me, and saves it on success.
    Returns 0 on success or if the user declined, 1 on cancel."""
    print("", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    print(" muninn — API key setup", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    print("", file=sys.stderr)
    print(" An API key is ONLY needed if you want to upload to WDGoWars.", file=sys.stderr)
    print(" Local conversion to JSON works without one.", file=sys.stderr)
    print("", file=sys.stderr)
    print(" Get your key from: https://wdgwars.pl/  →  profile  →  API Key", file=sys.stderr)
    print(f" It will be saved to: {_key_path()}", file=sys.stderr)
    print("", file=sys.stderr)

    if not _prompt_yes_no(" Set up your WDGoWars API key now?", default=True):
        print("", file=sys.stderr)
        print(" Skipped. You can run setup later with:", file=sys.stderr)
        print("   python3 muninn.py --setup", file=sys.stderr)
        print("", file=sys.stderr)
        return 0

    while True:
        try:
            # Interactive TTY -> hidden input via getpass
            # Piped stdin (CI, testing) -> regular input (visible but works)
            if sys.stdin.isatty():
                import getpass
                key = getpass.getpass(" Paste your WDGoWars API key (hidden): ").strip()
            else:
                # Non-interactive: don't hang on getpass, just read a line
                print(" Paste your WDGoWars API key: ", end="", flush=True,
                      file=sys.stderr)
                key = sys.stdin.readline().strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[muninn] setup cancelled — no key saved", file=sys.stderr)
            return 1

        if not key:
            print(" (empty input — try again, or Ctrl+C to cancel)\n",
                  file=sys.stderr)
            continue

        print(" Validating key against wdgwars.pl/api/me ...", file=sys.stderr)
        rc = check_whoami(key)
        if rc != 0:
            print(" That key was rejected. Try again, or Ctrl+C to cancel.\n",
                  file=sys.stderr)
            continue

        save_key(key)
        print("", file=sys.stderr)
        print(" ✓ API key saved. You can now run uploads without --key:",
              file=sys.stderr)
        print("   python3 muninn.py yourfile.txt --upload",
              file=sys.stderr)
        # Offer to install a scheduled task as a second optional step.
        # Skipped silently on errors so the API-key setup is never
        # held hostage by a scheduler hiccup.
        try:
            interactive_schedule_setup()
        except (KeyboardInterrupt, EOFError):
            print("\n[muninn] schedule setup skipped", file=sys.stderr)
        except Exception as e:
            print(f"\n[muninn] schedule setup error (skipped): {e}",
                  file=sys.stderr)
        return 0


def save_key(key: str) -> None:
    """Save the API key to user config. Refuses to write through a symlink
    and creates the file with mode 0o600 atomically (anti-symlink-attack
    and anti-create-mode-race — both defenses live in gungnir as of
    0.1.1)."""
    try:
        _client.save_key(key)
    except gungnir.KeyFileSymlinkError as e:
        sys.exit(f"{e}\nremove the symlink and re-run --save-key")
    p = _key_path()
    print(f"[muninn] saved API key to {p}", file=sys.stderr)
    print(f"[muninn] (file mode 600 — only your user can read it)", file=sys.stderr)
    print(f"[muninn] you can now run uploads without --key or env var",
          file=sys.stderr)


def _scrub(text: str, key: str) -> str:
    """Defensive: if the API key ever leaks into a server error message or
    exception trace, redact it before we print to the terminal.

    Delegates to gungnir.keys.scrub() which redacts on any non-empty
    match (gungnir dropped Muninn 1.x's `len(key) > 8` threshold that
    silently leaked short keys)."""
    return gungnir.keys.scrub(text, key)


def check_whoami(key: str) -> int:
    """Hit /api/me to validate the key. Logs username + counts on success.
    Never echoes the API key in any output, even on failure. Delegates
    to gungnir.transport.whoami() — the prefixed log lines come from
    there."""
    return _client.whoami(key)


# ── Format detection ────────────────────────────────────────────────────────
def detect_format(path: Path) -> str:
    """Sniff the first non-empty, non-comment line and decide the format.

    Transparently handles gzip — .gz / .json.gz files (tar1090 history
    chunks) are decompressed on the fly for sniffing. The full parser does
    the same on read."""
    # Binary GDL-90 stream: starts with 0x7E (flag byte) followed by a
    # known msg ID. Sniff before any text-mode open to avoid decoding
    # garbage. Restricted msg-ID list keeps a text file that happens to
    # start with `~` from being misdetected.
    try:
        with path.open("rb") as fh:
            head16 = fh.read(16)
        head4 = head16[:4]
        if len(head4) >= 2 and head4[0] == 0x7E and head4[1] in (
            0x00, 0x07, 0x0A, 0x0B, 0x14, 0x4D, 0x65,
        ):
            return "gdl90"
        # Mode-S Beast binary: 0x1A start, then type byte 0x31/0x32/0x33.
        # Like GDL-90 sniff, the message-type whitelist avoids matching a
        # text file that happens to start with the Ctrl+Z character.
        if len(head4) >= 2 and head4[0] == 0x1A and head4[1] in (0x31, 0x32, 0x33):
            return "beast"
        # RTL1090 / Kinetic BaseStation SQLite database. Every SQLite file
        # begins with this exact 16-byte header; checking it (rather than
        # just the extension) keeps a misnamed file from being treated as
        # a database and lets a correctly-formatted .sqlite/.db with the
        # BaseStation schema be detected too.
        if head16 == b"SQLite format 3\x00":
            return "sqb"
    except OSError:
        pass
    # Extension hint when the magic-byte read failed (network mounts,
    # permission quirks). Only triggers on .sqb specifically — generic
    # SQLite files without that extension still need the magic-byte path.
    if path.suffix.lower() == ".sqb":
        return "sqb"

    # Gzip-aware open. tar1090 emits chunk_*.json.gz; some users hand-gzip
    # captures to keep them small. Detect by extension or magic bytes.
    is_gz = False
    if path.suffix == ".gz" or path.name.endswith(".json.gz"):
        is_gz = True
    else:
        try:
            with path.open("rb") as fh:
                is_gz = fh.read(2) == b"\x1f\x8b"
        except OSError:
            pass

    if is_gz:
        import gzip
        opener = lambda: gzip.open(path, "rt", encoding="utf-8", errors="replace")
    else:
        opener = lambda: path.open("r", encoding="utf-8", errors="replace")

    with opener() as f:
        for raw in f:
            s = raw.strip()
            # Skip blanks and comment lines. AVR captures from pyModeS-style
            # tooling traditionally use `;` for block comments; the frame
            # terminator is also `;` but always preceded by `*<hex>`, so a
            # line that STARTS with `;` is unambiguously a comment.
            if not s or s.startswith("#") or s.startswith(";"):
                continue
            if s.startswith("*") and s.endswith(";"):
                return "avr"
            if s.startswith("MSG,") or s.startswith("SEL,") or s.startswith("ID,"):
                return "sbs1"
            if s.startswith("{") or s.startswith("["):
                return "json"
            # PortaPack Mayhem ADSB.TXT format — raw hex prefix + labeled fields:
            # "8DA4... ICAO:A41144 [Squawk:NNNN] [CALLSIGN] [Alt:N] [Lat:F Lon:F] ..."
            if " ICAO:" in s and s[:14].replace(" ", "").isalnum():
                return "mayhem"
            # Tab-separated AVR variants (some receivers prefix with timestamp)
            if "*" in s and s.endswith(";"):
                return "avr-tagged"
            # Otherwise treat as a generic CSV — caller can hint via --csv-format
            return "csv"
    return "empty"


# ── Helpers ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _to_dump1090_fa(records: list[dict]) -> dict:
    """Wrap muninn's flat record list into dump1090-fa / readsb aircraft.json
    shape. This is what the WDGoWars *web-form* upload accepts (drag-and-drop
    of the .json file). The HMAC --upload path uses the flat list directly
    against /endpoint/upload/ (or /api/upload/) and does NOT use this format."""
    import time as _t
    out = []
    for r in records:
        a = {
            "hex":      r["icao"].lower(),
            "flight":   (r.get("callsign") or "").strip(),
            "lat":      r["lat"],
            "lon":      r["lon"],
            "alt_baro": r.get("alt_ft", 0),
            "gs":       r.get("speed_kt", 0),
            "track":    r.get("heading", 0),
            "seen":     0,
            "seen_pos": 0,
            "messages": 1,
        }
        # drop empty flight to match readsb behavior
        if not a["flight"]:
            a.pop("flight")
        out.append(a)
    return {
        "now":      _t.time(),
        "messages": len(out),
        "aircraft": out,
    }


def _coerce_int(v) -> int:
    """dump1090/readsb encode on-ground aircraft as alt_baro="ground". Treat
    that and any other non-numeric value as 0 instead of crashing."""
    if v is None:
        return 0
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _norm_record(icao: str, *, callsign: str = "", lat: float | None = None,
                 lon: float | None = None, alt_ft: int = 0, speed_kt: int = 0,
                 heading: int = 0, first_seen: str | None = None) -> dict | None:
    """Build a record matching the WDGoWars aircraft schema. Drops the record
    if it lacks position."""
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    # ICAO is a 24-bit Mode-S address: always exactly 6 hex chars. Do NOT
    # strip leading zeros — the server validates ^[0-9A-F]{6}$ and a stripped
    # ICAO ("0DB36A" -> "DB36A") is silently dropped on import. Empty input
    # falls back to "000000" so the caller can still emit a record.
    icao = (icao or "").upper().strip() or "000000"
    return {
        "icao": icao,
        "callsign": (callsign or "").strip(),
        "lat": round(float(lat), 6),
        "lon": round(float(lon), 6),
        "alt_ft": int(alt_ft) if alt_ft else 0,
        "speed_kt": int(speed_kt) if speed_kt else 0,
        "heading": int(heading) if heading else 0,
        "first_seen": first_seen or _now_iso(),
        "type": "ADSB",
    }


import math as _math

# ── Range sanity check ───────────────────────────────────────────────────────

_ADSB_MAX_REALISTIC_KM = 500  # radio horizon at cruise altitude; hard physics limit


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = _math.radians(lat1), _math.radians(lat2)
    dphi = _math.radians(lat2 - lat1)
    dlambda = _math.radians(lon2 - lon1)
    a = _math.sin(dphi / 2) ** 2 + _math.cos(phi1) * _math.cos(phi2) * _math.sin(dlambda / 2) ** 2
    return R * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1 - a))


def _warn_range(records: list[dict]) -> None:
    """Warn if aircraft positions suggest mixed local + remote data.

    Computes the geographic centroid of all aircraft, then flags any that are
    beyond _ADSB_MAX_REALISTIC_KM from it.  No records are removed.  The most
    common cause is dump1090 running with --net (Beast input open) while a
    remote feeder like piaware or FlightAware is also active on the same
    machine, silently mixing distant aircraft into the local session.
    """
    if _QUIET or len(records) < 2:
        return
    lats = [r["lat"] for r in records]
    lons = [r["lon"] for r in records]
    lats.sort(); lons.sort(); mid = len(lats) // 2
    clat = lats[mid]; clon = lons[mid]  # median: robust against remote outliers pulling the mean
    outliers = [
        r for r in records
        if _haversine_km(clat, clon, r["lat"], r["lon"]) > _ADSB_MAX_REALISTIC_KM
    ]
    if not outliers:
        return
    pct = 100 * len(outliers) / len(records)
    import sys as _sys
    print(
        f"[muninn] WARNING: {len(outliers)} of {len(records)} aircraft "
        f"({pct:.0f}%) are >{_ADSB_MAX_REALISTIC_KM} km from the position "
        f"centroid — possible network-fed remote data mixed with local reception.",
        file=_sys.stderr,
    )
    print(f"[muninn]   Centroid: {clat:.4f}, {clon:.4f}", file=_sys.stderr)
    for r in outliers[:3]:
        d = _haversine_km(clat, clon, r["lat"], r["lon"])
        label = r["callsign"] or "(no callsign)"
        print(
            f"[muninn]   outlier: {r['icao']} {label} "
            f"@ {r['lat']:.4f},{r['lon']:.4f} — {d:.0f} km from centroid",
            file=_sys.stderr,
        )
    if len(outliers) > 3:
        print(f"[muninn]   ... and {len(outliers) - 3} more", file=_sys.stderr)
    print(
        "[muninn]   If unexpected, check whether dump1090 has --net enabled "
        "with a remote Beast/piaware feed active on the same machine.",
        file=_sys.stderr,
    )

# ── SBS-1 / BaseStation CSV ─────────────────────────────────────────────────
# Field reference: http://woodair.net/sbs/article/barebones42_socket_data.htm
# MSG,<type>,<sess>,<aircraft>,<hex>,<flightID>,<gen_date>,<gen_time>,
# <log_date>,<log_time>,<callsign>,<altitude>,<speed>,<heading>,<lat>,<lon>,
# <vrate>,<squawk>,<alert>,<emerg>,<spi>,<onground>
def parse_sbs1(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for r in csv.reader(f):
            if len(r) < 22 or r[0] != "MSG":
                continue
            icao = (r[4] or "").upper()
            if not icao:
                continue
            try:
                # MSG type 3 = airborne position (lat/lon present)
                # MSG type 4 = airborne velocity (speed, heading, vrate)
                # MSG type 1 = identification (callsign)
                mtype = r[1]
                lat = float(r[14]) if r[14] else None
                lon = float(r[15]) if r[15] else None
                alt = int(r[11]) if r[11] else 0
                speed = int(float(r[12])) if r[12] else 0
                heading = int(float(r[13])) if r[13] else 0
                callsign = (r[10] or "").strip()
            except (ValueError, IndexError):
                continue
            # Timestamp from columns 6/7 (msg gen date/time)
            ts_str = _now_iso()
            try:
                if r[6] and r[7]:
                    d = r[6].replace("/", "-")
                    t = r[7].split(".")[0]
                    ts_str = f"{d} {t}"
            except Exception:
                pass

            entry = rows.setdefault(icao, {"icao": icao, "callsign": "",
                                          "lat": None, "lon": None,
                                          "alt_ft": 0, "speed_kt": 0,
                                          "heading": 0, "first_seen": ts_str})
            entry["first_seen"] = ts_str
            if callsign:
                entry["callsign"] = callsign
            if lat is not None and lon is not None:
                entry["lat"] = lat
                entry["lon"] = lon
            if alt:
                entry["alt_ft"] = alt
            if speed:
                entry["speed_kt"] = speed
            if heading:
                entry["heading"] = heading

    out: dict[str, dict] = {}
    for icao, e in rows.items():
        rec = _norm_record(icao=icao, callsign=e["callsign"],
                          lat=e["lat"], lon=e["lon"],
                          alt_ft=e["alt_ft"], speed_kt=e["speed_kt"],
                          heading=e["heading"], first_seen=e["first_seen"])
        if rec:
            out[icao] = rec
    return out


# ── RTL1090 / BaseStation SQLite (.sqb) ─────────────────────────────────────
# Kinetic Avionics BaseStation.sqb schema, as written by RTL1090's SQLite
# logging plugin, PlanePlotter, and stock BaseStation. Two tables matter:
#
#   Aircraft  (AircraftID PK, ModeS, Registration, ...)
#   Flights   (AircraftID FK, StartTime, EndTime, Callsign,
#              FirstLat/Lon/Altitude/GroundSpeed/Track,
#              LastLat/Lon/Altitude, ...)
#
# Quirks worth knowing about:
#   * One row per FLIGHT, not per position report. We emit up to two
#     records per flight — one at StartTime/First* and one at EndTime/Last*
#     — whichever sides have valid coordinates.
#   * Timestamps are local-time strings like "2024-08-15 14:32:11.123"
#     with no timezone information. Default behaviour is to treat them as
#     UTC, since muninn's output is UTC throughout. Pass --sqb-tz <IANA
#     zone> (e.g. "America/New_York") to interpret them as local time in
#     that zone and convert to UTC on the fly.
#   * Schema drift — RTL1090 / Kinetic BaseStation / PlanePlotter all
#     ship slightly different column sets. We use PRAGMA table_info to
#     discover which columns are present and substitute NULL for any
#     missing optional column, rather than failing the SELECT.
#   * Some installs only populate Aircraft, not Flights (the logger was
#     never enabled). In that case we exit nonzero with a clear message
#     rather than emit an empty JSON.
def parse_sqb(path: Path, tz_override: str | None = None) -> dict[str, dict]:
    import sqlite3

    # Read-only URI form is the right call on CPython — keeps the read
    # safe even if the .sqb is shared with a live BaseStation process.
    # The journal file isn't touched.
    #
    # In Pyodide (the browser build), the URI form silently hangs against
    # the WASM sqlite3's virtual filesystem — connect() never returns and
    # never raises, freezing the web UI on "Parsing BaseStation.sqb..."
    # forever. Detect the emscripten platform and fall back to a plain
    # path connect there. The web build drops files into a private MEMFS
    # path that no other process can touch, so the URI-mode safety is
    # academic anyway.
    try:
        if sys.platform == "emscripten":
            conn = sqlite3.connect(str(path))
        else:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        sys.exit(f"[muninn] could not open {path.name} as SQLite: {e}")

    try:
        cur = conn.cursor()
        tables = {row[0] for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "Aircraft" not in tables:
            sys.exit(f"[muninn] {path.name}: no Aircraft table — file is "
                     f"not a BaseStation-schema SQLite database.")
        if "Flights" not in tables:
            sys.exit(f"[muninn] {path.name}: no Flights table — this looks "
                     f"like an Aircraft-only BaseStation install (logger "
                     f"never enabled), so there is nothing to upload.")

        flight_cols = {row[1] for row in cur.execute("PRAGMA table_info(Flights)")}
        ac_cols = {row[1] for row in cur.execute("PRAGMA table_info(Aircraft)")}

        if "AircraftID" not in flight_cols:
            sys.exit(f"[muninn] {path.name}: Flights table missing "
                     f"AircraftID column — cannot join to Aircraft.ModeS "
                     f"for ICAO.")
        if "ModeS" not in ac_cols:
            sys.exit(f"[muninn] {path.name}: Aircraft table missing ModeS "
                     f"column — cannot extract ICAO addresses.")

        # Build the projection dynamically so optional missing columns
        # become NULL rather than raising. Order MUST match the unpack
        # below.
        wanted = [
            ("a", "ModeS"),
            ("f", "Callsign"),
            ("f", "StartTime"),
            ("f", "EndTime"),
            ("f", "FirstLat"),
            ("f", "FirstLon"),
            ("f", "LastLat"),
            ("f", "LastLon"),
            ("f", "FirstAltitude"),
            ("f", "LastAltitude"),
            ("f", "FirstGroundSpeed"),
            ("f", "FirstTrack"),
        ]
        select_parts = []
        for tbl, col in wanted:
            avail = ac_cols if tbl == "a" else flight_cols
            if col in avail:
                select_parts.append(f"{tbl}.{col}")
            else:
                select_parts.append("NULL")

        sql = (
            "SELECT " + ", ".join(select_parts) +
            " FROM Flights f JOIN Aircraft a ON f.AircraftID = a.AircraftID"
        )
        flights = list(cur.execute(sql))
    finally:
        conn.close()

    if not flights:
        sys.exit(f"[muninn] {path.name}: Flights table is empty — nothing "
                 f"to upload. (If you just enabled logging, give the "
                 f"receiver time to record some flights first.)")

    tz = None
    if tz_override:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz_override)
        except Exception as e:
            sys.exit(f"[muninn] --sqb-tz {tz_override!r} is not a "
                     f"recognised IANA zone: {e}")

    def _ts_to_utc_iso(s):
        if not s:
            return None
        s = str(s).strip()
        if not s:
            return None
        # BaseStation writes fractional seconds; muninn output is
        # whole-second resolution, so drop them.
        if "." in s:
            s = s.split(".", 1)[0]
        s = s.replace("/", "-").replace("T", " ")
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        if tz is not None:
            dt = dt.replace(tzinfo=tz).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    out: dict[str, dict] = {}
    for idx, row in enumerate(flights):
        (modes, callsign, st, et, flat, flon, llat, llon,
         falt, lalt, fgs, ftrk) = row
        icao = (modes or "").upper().strip()
        if not icao:
            continue
        callsign = (callsign or "").strip()

        # BaseStation writes literal 0.0 / 0.0 when the receiver had no
        # position decoded at the point in question, so treat that as
        # "no fix" rather than a position on the equator south of Ghana.
        def _has_fix(la, lo):
            return (
                la is not None and lo is not None
                and (float(la) != 0.0 or float(lo) != 0.0)
            )

        if _has_fix(flat, flon):
            rec = _norm_record(
                icao=icao, callsign=callsign,
                lat=float(flat), lon=float(flon),
                alt_ft=_coerce_int(falt),
                speed_kt=_coerce_int(fgs),
                heading=_coerce_int(ftrk),
                first_seen=_ts_to_utc_iso(st) or _now_iso(),
            )
            if rec:
                # Composite key — unlike SBS-1 streaming we deliberately
                # keep both endpoints per flight, and may carry multiple
                # flights for the same ICAO. Downstream only consumes
                # rows.values().
                out[f"{icao}-{idx}-first"] = rec

        if _has_fix(llat, llon):
            rec = _norm_record(
                icao=icao, callsign=callsign,
                lat=float(llat), lon=float(llon),
                alt_ft=_coerce_int(lalt),
                # BaseStation schema does not store Last(GroundSpeed|Track),
                # so we surface 0 rather than carrying forward the First*
                # values (which would be misleading on a long flight).
                speed_kt=0, heading=0,
                first_seen=_ts_to_utc_iso(et) or _now_iso(),
            )
            if rec:
                out[f"{icao}-{idx}-last"] = rec

    return out


# ── AVR raw Mode-S ──────────────────────────────────────────────────────────
def parse_avr(path: Path) -> dict[str, dict]:
    try:
        import pyModeS as pms
    except ImportError:
        sys.exit("AVR raw input requires pyModeS — install with: pip install pyModeS")

    # pyModeS 3.x ships a PipeDecoder that maintains per-ICAO state across
    # frames — handles paired CPR position decoding, callsign merging, and
    # altitude/velocity tracking automatically. Way cleaner than rolling our
    # own even/odd CPR pairing.
    pd = pms.PipeDecoder()
    rows: dict[str, dict] = {}
    now = time.time()
    line_idx = 0

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if "*" in line:
                line = line[line.index("*"):]
            if not line.startswith("*") or not line.endswith(";"):
                continue
            hexmsg = line[1:-1]
            if len(hexmsg) not in (14, 28):
                continue
            line_idx += 1
            try:
                # Synthetic timestamps spaced by 10ms keep CPR-pairing happy
                # when the source file lacks per-line wallclock data.
                d = pd.decode(hexmsg, timestamp=now + line_idx * 0.01)
            except Exception:
                continue
            if not d:
                continue
            icao = (d.get("icao") or "").upper()
            if not icao:
                continue
            entry = rows.setdefault(icao, {"icao": icao, "callsign": "",
                                           "lat": None, "lon": None,
                                           "alt_ft": 0, "speed_kt": 0,
                                           "heading": 0,
                                           "first_seen": _now_iso()})
            # PipeDecoder fills in fields incrementally as frames arrive
            if d.get("callsign"):
                entry["callsign"] = d["callsign"].strip().rstrip("_")
            if d.get("latitude") is not None and d.get("longitude") is not None:
                entry["lat"] = d["latitude"]
                entry["lon"] = d["longitude"]
            if d.get("altitude"):
                entry["alt_ft"] = int(d["altitude"])
            if d.get("groundspeed"):
                entry["speed_kt"] = int(d["groundspeed"])
            if d.get("track") is not None:
                entry["heading"] = int(d["track"])

    out: dict[str, dict] = {}
    for icao, e in rows.items():
        rec = _norm_record(icao=icao, callsign=e["callsign"],
                          lat=e["lat"], lon=e["lon"],
                          alt_ft=e["alt_ft"], speed_kt=e["speed_kt"],
                          heading=e["heading"], first_seen=e["first_seen"])
        if rec:
            out[icao] = rec
    return out


# ── dump1090 / readsb JSON ──────────────────────────────────────────────────
def parse_json(path: Path) -> dict[str, dict]:
    """Handle multiple ADS-B JSON dialects:
      1) dump1090 / readsb aircraft.json snapshot:
         {"now":..., "aircraft":[{"hex": ...}, ...]}
      2) VRS (VirtualRadarServer) AircraftList.json:
         {"acList":[{"Icao": ..., "Lat": ..., "Long": ...}, ...]}
      3) tar1090 history chunks (gzipped dump1090 snapshots — chunk_*.json.gz)
      4) NDJSON / JSON-lines — one aircraft object per line; field names from
         either dialect work transparently
      5) Bare JSON arrays of aircraft objects
    """
    rows: dict[str, dict] = {}

    # tar1090 history chunks ship as gzipped JSON. Detect by file extension or
    # by the 1f 8b magic bytes at the head; readers are otherwise identical.
    if path.suffix == ".gz" or path.name.endswith(".json.gz"):
        import gzip
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        with path.open("rb") as f:
            head = f.read(2)
        if head == b"\x1f\x8b":
            import gzip
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            text = path.read_text(encoding="utf-8", errors="replace")

    def _ingest(ac: dict, now_ts: float | None = None):
        # Field aliases span dump1090-fa / readsb / VRS / Stratux / generic.
        # Stratux fields are CapitalCase with underscores: Icao_addr, Tail,
        # Lat, Lng, Alt, Speed, Track, Position_valid. Bring them under the
        # same _ingest so NDJSON files mixing sources work transparently.
        icao = (ac.get("hex") or ac.get("icao") or ac.get("Icao")
                or ac.get("ICAO") or "").upper()
        if not icao and ac.get("Icao_addr") is not None:
            # Stratux ships the ICAO as an int. Convert to 6-hex.
            try:
                icao = f"{int(ac['Icao_addr']):06X}"
            except (TypeError, ValueError):
                pass
        if not icao:
            return
        # Stratux signals position-validity explicitly — skip aircraft with
        # known-invalid positions even if Lat/Lng happen to be present.
        if ac.get("Position_valid") is False:
            return
        lat = ac.get("lat") if ac.get("lat") is not None else ac.get("Lat")
        # VRS uses "Long" (with the g) for longitude; Stratux uses "Lng".
        lon = ac.get("lon") if ac.get("lon") is not None else (
            ac.get("Long") if ac.get("Long") is not None else ac.get("Lng"))
        if lat is None or lon is None:
            return
        ts_str = _now_iso()
        if now_ts:
            ts_str = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rec = _norm_record(
            icao=icao,
            callsign=(ac.get("flight") or ac.get("callsign")
                      or ac.get("Call") or ac.get("Tail")
                      or ac.get("Reg") or "").strip(),
            lat=lat, lon=lon,
            alt_ft=_coerce_int(ac.get("alt_baro") or ac.get("altitude")
                               or ac.get("alt") or ac.get("Alt") or 0),
            speed_kt=_coerce_int(ac.get("gs") or ac.get("speed")
                                 or ac.get("Spd") or ac.get("Speed") or 0),
            heading=_coerce_int(ac.get("track") or ac.get("heading")
                                or ac.get("Trak") or ac.get("Track") or 0),
            first_seen=ts_str,
        )
        if rec:
            rows[icao] = rec

    # Try whole-file JSON snapshot
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            # dump1090 / readsb / tar1090 chunk shape
            if "aircraft" in obj:
                now_ts = obj.get("now")
                for ac in obj["aircraft"]:
                    _ingest(ac, now_ts=now_ts)
                return rows
            # VRS AircraftList shape (`acList`)
            if "acList" in obj:
                for ac in obj["acList"]:
                    _ingest(ac)
                return rows
            # Stratux `/traffic` shape — top-level dict whose values are the
            # aircraft entries (keyed by ICAO hex string). Tell apart from
            # other dict shapes by sniffing the first value for the Stratux
            # signature fields.
            first_val = next(iter(obj.values()), None) if obj else None
            if isinstance(first_val, dict) and (
                "Icao_addr" in first_val or "Position_valid" in first_val
            ):
                for ac in obj.values():
                    if isinstance(ac, dict):
                        _ingest(ac)
                return rows
        if isinstance(obj, list):
            for ac in obj:
                _ingest(ac)
            return rows
    except json.JSONDecodeError:
        pass

    # Fall back to JSON-lines (NDJSON)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ac = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ac, dict):
            _ingest(ac)
    return rows


# ── PortaPack Mayhem ADSB.TXT ───────────────────────────────────────────────
# Format: <raw_hex> ICAO:<hex6> [Squawk:NNNN] [<CALLSIGN>] [Alt:N] [Lat:F Lon:F]
#         [Type:N Hdg:N (GS|TAS|IAS):N Vrate:N] [Sil:N]
# Source: portapack-mayhem firmware/application/apps/ui_adsb_rx.cpp::ADSBLogger
# Each line is one decoded frame; data accumulates per ICAO across lines.
import re as _re

_MAYHEM_ICAO   = _re.compile(r"\bICAO:([0-9A-Fa-f]{6})\b")
_MAYHEM_ALT    = _re.compile(r"\bAlt:(-?\d+)\b")
_MAYHEM_LAT    = _re.compile(r"\bLat:(-?\d+\.\d+)\b")
_MAYHEM_LON    = _re.compile(r"\bLon:(-?\d+\.\d+)\b")
_MAYHEM_HDG    = _re.compile(r"\bHdg:(\d+)\b")
_MAYHEM_SPEED  = _re.compile(r"\b(?:GS|TAS|IAS):(\-?\d+)\b")
_MAYHEM_SQUAWK = _re.compile(r"\bSquawk:(\d{4})\b")
# Callsign is a bare token (no Key: prefix), 3-8 chars of letters/digits,
# usually between Squawk/ICAO and Alt. We extract it positionally below.

def parse_mayhem(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = _MAYHEM_ICAO.search(s)
            if not m:
                continue
            icao = m.group(1).upper()
            entry = rows.setdefault(icao, {"icao": icao, "callsign": "",
                                           "lat": None, "lon": None,
                                           "alt_ft": 0, "speed_kt": 0,
                                           "heading": 0,
                                           "first_seen": _now_iso()})
            lat_m = _MAYHEM_LAT.search(s)
            lon_m = _MAYHEM_LON.search(s)
            if lat_m and lon_m:
                try:
                    entry["lat"] = float(lat_m.group(1))
                    entry["lon"] = float(lon_m.group(1))
                except ValueError:
                    pass
            alt_m = _MAYHEM_ALT.search(s)
            if alt_m:
                try: entry["alt_ft"] = int(alt_m.group(1))
                except ValueError: pass
            spd_m = _MAYHEM_SPEED.search(s)
            if spd_m:
                try: entry["speed_kt"] = int(spd_m.group(1))
                except ValueError: pass
            hdg_m = _MAYHEM_HDG.search(s)
            if hdg_m:
                try: entry["heading"] = int(hdg_m.group(1))
                except ValueError: pass
            # Callsign: bare 3-8 char token of letters/digits between known
            # labeled fields. Mayhem inserts it after Squawk (if present) or
            # right after ICAO:HEX.
            # Strategy: strip all "Key:Val" tokens + the leading raw hex,
            # remaining tokens of length 3-8 alphanumeric is the callsign.
            tokens = s.split()
            for tok in tokens:
                if ":" in tok:
                    continue
                if len(tok) > 28 and all(c in "0123456789abcdefABCDEF" for c in tok):
                    continue  # raw hex prefix
                tu = tok.upper().rstrip("_")
                if 3 <= len(tu) <= 8 and tu.replace("-", "").isalnum() and not tu.isdigit():
                    if not entry["callsign"]:
                        entry["callsign"] = tu
                    break

    out: dict[str, dict] = {}
    for icao, e in rows.items():
        rec = _norm_record(icao=icao, callsign=e["callsign"],
                          lat=e["lat"], lon=e["lon"],
                          alt_ft=e["alt_ft"], speed_kt=e["speed_kt"],
                          heading=e["heading"], first_seen=e["first_seen"])
        if rec:
            out[icao] = rec
    return out


# ── GDL-90 binary stream ────────────────────────────────────────────────────
# Format: serial-style framing with 0x7E start/end flags, 0x7D escape byte,
# and CRC-16-CCITT FCS. Used by cockpit ADS-B receivers (Stratux,
# ForeFlight Sentry, Garmin GDL series). Spec: FAA GDL-90 Public ICD Rev A.
#
# Validated against gdl90py's authoritative Traffic Report test vector
# (NathanVaughn/gdl90py, tests/messages/test_traffic_report.py):
# ICAO, callsign, lat/lon (to 24-bit fixed-point precision), horizontal
# velocity, and track all decode to the expected values. CRC is still
# accepted-without-verification — if real-world captures show frame-aligned
# noise we'll wire up CRC-16-CCITT FCS validation.
GDL90_MSG_TRAFFIC = 0x14   # Traffic Report (other aircraft)
GDL90_MSG_OWNSHIP = 0x0A   # Ownship Report (your aircraft) — same payload shape

def _gdl90_traffic_record(payload: bytes) -> dict | None:
    """Decode one Traffic / Ownship Report payload (27 bytes after msg ID)."""
    if len(payload) < 27:
        return None
    # ICAO address: 3 bytes big-endian, offset 1 (skip alert/addr-type byte 0)
    icao_int = (payload[1] << 16) | (payload[2] << 8) | payload[3]
    icao = f"{icao_int:06X}"
    # Latitude: 24-bit two's complement, scale 180 / 2^23 deg per LSB
    lat_raw = (payload[4] << 16) | (payload[5] << 8) | payload[6]
    if lat_raw & 0x800000:
        lat_raw -= 0x1000000
    lat = lat_raw * (180.0 / (1 << 23))
    # Longitude: same encoding
    lon_raw = (payload[7] << 16) | (payload[8] << 8) | payload[9]
    if lon_raw & 0x800000:
        lon_raw -= 0x1000000
    lon = lon_raw * (180.0 / (1 << 23))
    # Altitude: 12 bits across byte 10 (8) + byte 11 high nibble (4).
    # Scale 25 ft/LSB, offset -1000 ft. 0xFFF = invalid.
    alt_raw = (payload[10] << 4) | (payload[11] >> 4)
    alt_ft = 0 if alt_raw == 0xFFF else (alt_raw * 25) - 1000
    # Horizontal velocity: 12 bits across byte 13 (8) + byte 14 high nibble (4).
    # Units: knots. 0xFFF = invalid.
    h_vel = (payload[13] << 4) | (payload[14] >> 4)
    speed_kt = 0 if h_vel == 0xFFF else h_vel
    # Track / heading: byte 16, scaled 0-255 -> 0-360 degrees.
    track = int(payload[16] * (360.0 / 256.0))
    # Callsign: 8 ASCII bytes, bytes 18-25. Trailing spaces / nulls stripped.
    callsign = payload[18:26].decode("ascii", errors="replace").strip().rstrip("\x00").strip()
    return _norm_record(
        icao=icao, callsign=callsign, lat=lat, lon=lon,
        alt_ft=alt_ft, speed_kt=speed_kt, heading=track,
        first_seen=_now_iso(),
    )


def parse_gdl90(path: Path) -> dict[str, dict]:
    """Parse a GDL-90 binary stream into the muninn record schema.

    Frame structure (per spec):
        0x7E <msg_id> <payload...> <crc_lo> <crc_hi> 0x7E
    Byte-stuffing inside the frame body:
        0x7D 0x5D  ->  literal 0x7D
        0x7D 0x5E  ->  literal 0x7E
    """
    rows: dict[str, dict] = {}
    data = path.read_bytes()
    if not data:
        return rows
    # Split on the flag byte. The interior fragments are escaped frame bodies.
    for raw_frame in data.split(b"\x7e"):
        if len(raw_frame) < 4:
            continue
        # Unescape byte-stuffing.
        unescaped = bytearray()
        i = 0
        while i < len(raw_frame):
            b = raw_frame[i]
            if b == 0x7D and i + 1 < len(raw_frame):
                unescaped.append(raw_frame[i + 1] ^ 0x20)
                i += 2
            else:
                unescaped.append(b)
                i += 1
        if len(unescaped) < 4:
            continue
        msg_id = unescaped[0]
        # NOTE: skipping CRC-16-CCITT validation for now. If real-world
        # captures show mis-aligned frames we will need to verify FCS to
        # discard noise. For now we accept any frame whose layout parses.
        payload = bytes(unescaped[1:-2])  # strip msg_id + 2 CRC bytes
        if msg_id in (GDL90_MSG_TRAFFIC, GDL90_MSG_OWNSHIP):
            rec = _gdl90_traffic_record(payload)
            if rec:
                rows[rec["icao"]] = rec
    return rows


# ── Mode-S Beast binary ─────────────────────────────────────────────────────
# dump1090's native wire protocol on TCP port 30005. Each message:
#     0x1A <type> <6B timestamp> <1B signal> <data>
# where type is:
#     0x31 -> Mode AC, data = 2 bytes
#     0x32 -> Mode S short, data = 7 bytes
#     0x33 -> Mode S long, data = 14 bytes
# Any literal 0x1A inside the message is escaped as 0x1A 0x1A.
#
# Decoding strategy: re-emit each Mode-S short/long message as a hex string
# and feed it into pyModeS via the same PipeDecoder path parse_avr already
# uses. That gives us position decoding for free — Beast is just a binary
# container around the same DF17 frames.
def parse_beast(path: Path) -> dict[str, dict]:
    try:
        import pyModeS as pms
    except ImportError:
        sys.exit("Beast binary input requires pyModeS — install with: pip install pyModeS")

    raw = path.read_bytes()
    if not raw:
        return {}
    # Unescape: every 0x1A 0x1A is a single literal 0x1A.
    buf = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == 0x1A and i + 1 < len(raw) and raw[i + 1] == 0x1A:
            buf.append(0x1A)
            i += 2
        else:
            buf.append(raw[i])
            i += 1

    # Walk and pull out each message. After unescape, ESC (0x1A) only appears
    # as a frame start byte; safe to split.
    hex_msgs: list[str] = []
    i = 0
    while i < len(buf):
        if buf[i] != 0x1A:
            i += 1
            continue
        if i + 8 >= len(buf):
            break
        typ = buf[i + 1]
        if typ == 0x32:
            data_len = 7
        elif typ == 0x33:
            data_len = 14
        else:
            # Mode-AC (0x31) and unknown types — skip, ADS-B position
            # decoding only fires on Mode-S short/long.
            i += 1
            continue
        end = i + 2 + 6 + 1 + data_len  # ESC + type + ts + sig + data
        if end > len(buf):
            break
        data = buf[i + 2 + 6 + 1 : end]
        hex_msgs.append(data.hex())
        i = end

    if not hex_msgs:
        return {}

    # Feed into pyModeS PipeDecoder — same path parse_avr uses, which
    # already handles paired CPR position decoding + callsign / altitude /
    # velocity merging per ICAO. Beast is just AVR in a binary container.
    # NOTE: pyModeS PipeDecoder emits "latitude"/"longitude"/"track" — NOT
    # "lat"/"lon"/"heading". Using the wrong keys silently drops every
    # position record.
    pd = pms.PipeDecoder()
    rows: dict[str, dict] = {}
    now = time.time()
    for idx, hexmsg in enumerate(hex_msgs):
        try:
            d = pd.decode(hexmsg, timestamp=now + idx * 0.01)
        except Exception:
            continue
        if not d:
            continue
        icao = (d.get("icao") or "").upper()
        if not icao:
            continue
        entry = rows.setdefault(icao, {"icao": icao, "callsign": "",
                                       "lat": None, "lon": None,
                                       "alt_ft": 0, "speed_kt": 0,
                                       "heading": 0, "first_seen": _now_iso()})
        if d.get("callsign"):
            entry["callsign"] = d["callsign"].strip().rstrip("_")
        if d.get("latitude") is not None and d.get("longitude") is not None:
            entry["lat"] = d["latitude"]
            entry["lon"] = d["longitude"]
        if d.get("altitude"):
            entry["alt_ft"] = int(d["altitude"])
        if d.get("groundspeed"):
            entry["speed_kt"] = int(d["groundspeed"])
        if d.get("track") is not None:
            entry["heading"] = int(float(d["track"]))

    out: dict[str, dict] = {}
    for icao, e in rows.items():
        rec = _norm_record(icao=icao, callsign=e["callsign"],
                           lat=e["lat"], lon=e["lon"], alt_ft=e["alt_ft"],
                           speed_kt=e["speed_kt"], heading=e["heading"],
                           first_seen=e["first_seen"])
        if rec:
            out[icao] = rec
    return out


# ── Generic CSV ─────────────────────────────────────────────────────────────
def parse_csv(path: Path, fmt: str | None = None) -> dict[str, dict]:
    """Try heuristic column mapping; allow override via --csv-format header.

    --csv-format is a comma-separated list of column names matching the data,
    using these recognised names: icao, callsign, lat, lon, alt_ft, speed_kt,
    heading, first_seen, _ (skip).
    Example: --csv-format _,icao,callsign,lat,lon,alt_ft
    """
    fields = None
    if fmt:
        fields = [f.strip() for f in fmt.split(",")]

    rows: dict[str, dict] = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        first = next(reader, None)
        if not first:
            return rows
        # Detect header row
        looks_like_header = any(c.lower() in {"icao", "hex", "lat", "callsign"} for c in first)
        if looks_like_header and not fields:
            fields = [c.lower().strip() for c in first]
        elif fields:
            # First row is data — process it
            if not looks_like_header:
                _ingest_csv_row(first, fields, rows)
        else:
            sys.exit(f"Could not detect CSV columns. Pass --csv-format with the column order.\n"
                     f"First row: {first}")

        for r in reader:
            _ingest_csv_row(r, fields, rows)
    return rows


def _ingest_csv_row(r: list[str], fields: list[str], rows: dict[str, dict]):
    if not r or len(r) < 3:
        return
    d: dict = {}
    for i, name in enumerate(fields):
        if i >= len(r):
            break
        n = name.lower()
        if n in ("_", "skip", ""):
            continue
        v = r[i].strip()
        if not v:
            continue
        d[n] = v
    icao = (d.get("icao") or d.get("hex") or "").upper()
    if not icao:
        return
    try:
        lat = float(d["lat"]) if "lat" in d else None
        lon = float(d["lon"]) if "lon" in d else None
    except ValueError:
        return
    rec = _norm_record(
        icao=icao,
        callsign=d.get("callsign", ""),
        lat=lat, lon=lon,
        alt_ft=_coerce_int(d.get("alt_ft") or d.get("alt") or d.get("altitude") or 0),
        speed_kt=_coerce_int(d.get("speed_kt") or d.get("speed") or d.get("gs") or 0),
        heading=_coerce_int(d.get("heading") or d.get("track") or d.get("cog") or 0),
        first_seen=d.get("first_seen") or d.get("timestamp"),
    )
    if rec:
        rows[icao] = rec


# ── WDGoWars uploader ───────────────────────────────────────────────────────
_USE_COLOR = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None

def _tag(label: str, code: str) -> str:
    if _USE_COLOR:
        return f"[{code}m{label}[0m"
    return label

def _OK() -> str:    return _tag("[OK]", "1;32")     # bold green
def _FAIL() -> str:  return _tag("[FAIL]", "1;31")   # bold red
def _INFO() -> str:  return _tag("[..]", "1;36")     # bold cyan

def upload(records: list[dict], api_key: str, api_url: str = DEFAULT_API_URL,
           batch_size: int = 500, dry_run: bool = False) -> int:
    """POST ``records`` to the wdgwars.pl signed-JSON endpoint.

    Behavior comes from gungnir as of v2.0:

    - Retries 5xx and network errors with exponential backoff (3 attempts).
    - 429 stops the whole batch and persists a cooldown that the next
      cron tick respects (vs v1.x which kept trying chunks).
    - The silent-drop check (HTTP 200 ok:true with every counter zero)
      now returns rc=1 instead of just warning (vs v1.x which returned 0).
    - Inter-chunk cooldown of 1s between chunks (vs v1.x which was
      back-to-back).

    Wire shape (HMAC envelope) is byte-identical to v1.11.1 — verified
    by ``gungnir/tests/test_muninn_parity.py``.
    """
    return gungnir.transport.send(
        "muninn", __version__, api_url, api_key,
        aircraft=records,
        batch_size=batch_size,
        dry_run=dry_run,
        user_agent_extra=GITHUB_URL,
    )


# ── Watch mode ──────────────────────────────────────────────────────────────
def _file_signature(p: Path) -> str:
    """Cheap signature: size + mtime. Catches new files + edits without
    needing a full hash."""
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def _convert_one(path: Path, fmt_override: str | None, csv_format: str | None,
                 sqb_tz: str | None = None) -> list[dict]:
    fmt = fmt_override if fmt_override and fmt_override != "auto" else detect_format(path)
    if fmt == "avr" or fmt == "avr-tagged":
        rows = parse_avr(path)
    elif fmt == "sbs1":
        rows = parse_sbs1(path)
    elif fmt == "json":
        rows = parse_json(path)
    elif fmt == "mayhem":
        rows = parse_mayhem(path)
    elif fmt == "csv":
        rows = parse_csv(path, fmt=csv_format)
    elif fmt == "sqb":
        rows = parse_sqb(path, tz_override=sqb_tz)
    else:
        return []
    return list(rows.values())


def watch_dir(watch_dir: Path, args) -> int:
    """Poll the directory for new/changed files matching --watch-glob.
    For each new file: convert → write JSON next to it → optionally upload.
    State (signatures of processed files) is kept in .adsb-state.json in the
    watched dir so restarts don't re-process everything."""
    if not watch_dir.is_dir():
        sys.exit(f"--watch requires a directory, got: {watch_dir}")
    state_path = watch_dir / ".adsb-state.json"
    seen: dict[str, str] = {}
    try:
        seen = json.loads(state_path.read_text())
    except Exception:
        seen = {}

    api_key = None
    if args.upload:
        api_key = load_key(args.key)
        if not api_key:
            print("\n[muninn] --upload was passed but no API key is configured.",
                  file=sys.stderr)
            rc = interactive_setup()
            if rc != 0:
                return rc
            api_key = load_key(args.key)
            if not api_key:
                sys.exit("--upload requires an API key. Run with --setup, or "
                         "drop --upload to just convert files locally.")

    print(f"[watch] watching {watch_dir.resolve()} every {args.watch_interval}s "
          f"for {args.watch_glob!r} (Ctrl+C to stop)", file=sys.stderr)
    print(f"[watch] {len(seen)} files already processed", file=sys.stderr)

    try:
        while True:
            cycle_t0 = time.monotonic()
            new_files = []
            for f in sorted(watch_dir.glob(args.watch_glob)):
                if f.name.startswith("."):
                    continue
                if f.name.endswith(".wdgwars.json"):
                    continue  # don't re-process our own outputs
                sig = _file_signature(f)
                if not sig or seen.get(str(f.name)) == sig:
                    continue
                new_files.append((f, sig))

            for f, sig in new_files:
                try:
                    print(f"\n[watch] processing {f.name}", file=sys.stderr)
                    records = _convert_one(f, args.format if args.format != "auto" else None,
                                          args.csv_format, sqb_tz=args.sqb_tz)
                    print(f"[watch]   decoded {len(records)} aircraft", file=sys.stderr)
                    out_path = f.parent / f"{f.stem}.wdgwars.json"
                    out_path.write_text(json.dumps(_to_dump1090_fa(records), indent=2))
                    print(f"[watch]   wrote {out_path}", file=sys.stderr)
                    if args.upload and records:
                        rc = upload(records, api_key, args.api_url,
                                   batch_size=args.batch_size,
                                   dry_run=args.dry_run)
                        if rc != 0:
                            print(f"[watch]   upload failed — will retry next cycle",
                                  file=sys.stderr)
                            continue  # don't mark as seen if upload failed
                    seen[str(f.name)] = sig
                    # Persist after every successful file so we don't lose
                    # progress on crash / Ctrl+C
                    state_path.write_text(json.dumps(seen, indent=2))
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(f"[watch]   ERROR on {f.name}: {e}", file=sys.stderr)

            elapsed = time.monotonic() - cycle_t0
            sleep_for = max(1.0, args.watch_interval - elapsed)
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("\n[watch] stopped by user", file=sys.stderr)
        return 0


# ── Scheduling (--schedule / --unschedule) ─────────────────────────────────
#
# Two modes:
#   watch    — long-running daemon that watches a directory and uploads new
#              files as they appear. Best for decoders that write one file
#              per capture session (tar1090 chunks, NDJSON sessions).
#   periodic — runs every N minutes against the current state of a folder.
#              Best for decoders that rewrite a single rolling file in place
#              (dump1090-fa, readsb, VRS).
#
# Mechanism per platform:
#   Linux with systemd  — user systemd units in ~/.config/systemd/user/
#                         (no sudo). Default on Pi OS / Debian / Ubuntu.
#   Linux without systemd, macOS — user crontab. Periodic-mode only;
#                                  watch-mode users get a copy-paste hint.
#   Windows             — schtasks /Create at user scope.
#
# Everything is idempotent — re-running setup detects an existing install
# and replaces it. The marker comment `managed-by-muninn` flags entries
# Muninn owns so uninstall is exact.

SCHEDULE_MARKER = "managed-by-muninn"
SYSTEMD_SERVICE_NAME = "muninn-upload"
WINDOWS_TASK_WATCH = "Muninn-Watch"
WINDOWS_TASK_PERIODIC = "Muninn-Upload"


def _python_exe() -> str:
    """Absolute path to the Python that's running us. Used in scheduler
    units so PATH changes (or systemd's minimal environment) can't pick
    a different interpreter."""
    return sys.executable


def _muninn_script() -> Path:
    """Absolute path to this muninn.py file."""
    return Path(__file__).resolve()


def _guess_decoder_dirs() -> list[Path]:
    """Common decoder output directories that exist on this system.
    Ordered best-guess first. Used as default suggestions in the
    interactive scheduler prompt."""
    candidates = []
    if sys.platform.startswith("linux"):
        candidates = [
            Path("/run/dump1090-fa"),
            Path("/run/readsb"),
            Path("/run/adsbfi-feed"),
            Path("/run/dump1090"),
            Path("/var/run/dump1090-fa"),
            Path("/var/run/readsb"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path("/tmp/dump1090"),
            Path("/usr/local/var/dump1090"),
        ]
    elif sys.platform == "win32":
        candidates = [
            Path(r"C:\Tools\dump1090-win"),
            Path(r"C:\dump1090"),
            Path.home() / "dump1090",
        ]
    return [c for c in candidates if c.is_dir()]


def _guess_glob_for_dir(d: Path) -> str:
    """Best-guess file pattern for a decoder output dir."""
    try:
        if (d / "aircraft.json").exists():
            return "aircraft.json"
        if any(d.glob("chunk_*.json.gz")):
            return "chunk_*.json.gz"
        if any(d.glob("*.ndjson.gz")):
            return "*.ndjson.gz"
        if any(d.glob("*.json")):
            return "*.json"
    except (OSError, PermissionError):
        pass
    return "aircraft.json"


def _systemd_user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def _has_systemd() -> bool:
    """True if this Linux system runs systemd and has systemctl on PATH."""
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("systemctl") is None:
        return False
    return Path("/run/systemd/system").exists()


def _schedule_mechanism() -> str:
    """Decide which scheduler to use on this platform."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux") and _has_systemd():
        return "systemd"
    # macOS + Linux-without-systemd both fall back to cron
    return "cron"


# ── Pure renderers (no side effects, tested in isolation) ──────────────────

def render_systemd_units(mode: str, input_dir: Path, glob: str,
                         interval_min: int, python_exe: str,
                         muninn_py: Path,
                         dry_run: bool = False) -> dict[str, str | None]:
    """Pure: render systemd unit text for the chosen mode.

    Returns {"service": str, "timer": str | None}. Timer is None for
    watch mode (long-running service needs no timer).

    When dry_run=True, --dry-run is baked into ExecStart so the
    installed unit decodes + writes JSON but never POSTs to wdgwars.pl.
    Lets a user verify the install end-to-end before flipping to live.
    """
    dry = " --dry-run" if dry_run else ""
    desc_suffix = " [DRY-RUN]" if dry_run else ""
    if mode == "watch":
        service = (
            "[Unit]\n"
            f"Description=Muninn ADS-B watch+upload{desc_suffix}\n"
            f"# {SCHEDULE_MARKER}\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={python_exe} {muninn_py} --watch {input_dir} "
            f"--watch-glob {glob!r} --upload{dry}\n"
            "Restart=on-failure\n"
            "RestartSec=10s\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        return {"service": service, "timer": None}
    if mode == "periodic":
        service = (
            "[Unit]\n"
            f"Description=Muninn ADS-B upload (one-shot){desc_suffix}\n"
            f"# {SCHEDULE_MARKER}\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart={python_exe} {muninn_py} {input_dir} --upload{dry}\n"
        )
        timer = (
            "[Unit]\n"
            f"Description=Run Muninn every {interval_min} minutes\n"
            f"# {SCHEDULE_MARKER}\n"
            "\n"
            "[Timer]\n"
            "OnBootSec=2min\n"
            f"OnUnitActiveSec={interval_min}min\n"
            "Persistent=true\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        )
        return {"service": service, "timer": timer}
    raise ValueError(f"unknown mode: {mode!r}")


def render_cron_line(input_dir: Path, interval_min: int,
                     python_exe: str, muninn_py: Path,
                     dry_run: bool = False) -> str:
    """Pure: render a cron line for periodic mode. Watch mode isn't
    supported on cron (cron can't run daemons)."""
    cron_min = "*" if interval_min == 1 else f"*/{interval_min}"
    log = "$HOME/.muninn-cron.log"
    dry = " --dry-run" if dry_run else ""
    return (f"{cron_min} * * * * {python_exe} {muninn_py} {input_dir} "
            f"--upload{dry} >> {log} 2>&1  # {SCHEDULE_MARKER}\n")


def render_schtasks_create(mode: str, input_dir: Path, glob: str,
                           interval_min: int, python_exe: str,
                           muninn_py: Path,
                           dry_run: bool = False) -> list[str]:
    """Pure: render the schtasks /Create argv for Windows."""
    dry = " --dry-run" if dry_run else ""
    if mode == "watch":
        action = (f'"{python_exe}" "{muninn_py}" --watch "{input_dir}" '
                  f'--watch-glob "{glob}" --upload{dry}')
        return ["schtasks", "/Create", "/TN", WINDOWS_TASK_WATCH,
                "/TR", action, "/SC", "ONSTART", "/RL", "LIMITED", "/F"]
    if mode == "periodic":
        action = (f'"{python_exe}" "{muninn_py}" "{input_dir}" '
                  f'--upload{dry}')
        return ["schtasks", "/Create", "/TN", WINDOWS_TASK_PERIODIC,
                "/TR", action, "/SC", "MINUTE", "/MO", str(interval_min),
                "/RL", "LIMITED", "/F"]
    raise ValueError(f"unknown mode: {mode!r}")


# ── Installers (write files, run system commands) ──────────────────────────

def install_systemd_user(mode: str, input_dir: Path, glob: str,
                         interval_min: int, dry_run: bool = False) -> int:
    units = render_systemd_units(mode, input_dir, glob, interval_min,
                                 _python_exe(), _muninn_script(),
                                 dry_run=dry_run)
    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    # Mode switch hygiene: tear down the unit-type we're NOT using this
    # round, so switching periodic -> watch doesn't leave an orphan timer
    # firing into the long-lived watch service (which would interrupt it
    # every N minutes). Symmetric on watch -> periodic — but in that case
    # the .timer didn't exist before, so the stop/disable on .service
    # below is the only one that matters.
    if mode == "watch":
        stale = f"{SYSTEMD_SERVICE_NAME}.timer"
        stale_path = unit_dir / stale
        if stale_path.exists():
            subprocess.call(["systemctl", "--user", "stop", stale],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL)
            subprocess.call(["systemctl", "--user", "disable", stale],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL)
            stale_path.unlink()
            print(f"[schedule] removed orphan {stale_path}",
                  file=sys.stderr)
    service_path = unit_dir / f"{SYSTEMD_SERVICE_NAME}.service"
    service_path.write_text(units["service"])
    print(f"[schedule] wrote {service_path}", file=sys.stderr)
    if units["timer"] is not None:
        timer_path = unit_dir / f"{SYSTEMD_SERVICE_NAME}.timer"
        timer_path.write_text(units["timer"])
        print(f"[schedule] wrote {timer_path}", file=sys.stderr)
        target = f"{SYSTEMD_SERVICE_NAME}.timer"
        # When switching to periodic mode, the .service shouldn't be
        # directly activated by default.target (it should be triggered
        # by the timer instead). Disable the .service's WantedBy=default.target
        # symlink if a previous watch install enabled it.
        subprocess.call(["systemctl", "--user", "disable",
                         f"{SYSTEMD_SERVICE_NAME}.service"],
                        stderr=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL)
    else:
        target = f"{SYSTEMD_SERVICE_NAME}.service"
    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", target]):
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"[schedule] '{' '.join(cmd)}' returned {rc}", file=sys.stderr)
            return rc
    print(f"[schedule] enabled and started {target}", file=sys.stderr)
    print(f"[schedule] status: systemctl --user status {target}",
          file=sys.stderr)
    print(f"[schedule] logs:   journalctl --user -u {target} -f",
          file=sys.stderr)
    return 0


def uninstall_systemd_user() -> int:
    unit_dir = _systemd_user_dir()
    found = False
    for name in (f"{SYSTEMD_SERVICE_NAME}.timer",
                 f"{SYSTEMD_SERVICE_NAME}.service"):
        unit = unit_dir / name
        if unit.exists():
            found = True
            subprocess.call(["systemctl", "--user", "stop", name],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL)
            subprocess.call(["systemctl", "--user", "disable", name],
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL)
            unit.unlink()
            print(f"[schedule] removed {unit}", file=sys.stderr)
    if found:
        subprocess.call(["systemctl", "--user", "daemon-reload"])
    else:
        print("[schedule] no Muninn systemd units found", file=sys.stderr)
    return 0


def install_cron(input_dir: Path, interval_min: int,
                 dry_run: bool = False) -> int:
    if shutil.which("crontab") is None:
        print("[schedule] crontab not found on PATH", file=sys.stderr)
        return 1
    new_line = render_cron_line(input_dir, interval_min,
                                _python_exe(), _muninn_script(),
                                dry_run=dry_run)
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = r.stdout if r.returncode == 0 else ""
    except FileNotFoundError:
        return 1
    cleaned = "\n".join(l for l in current.splitlines()
                        if SCHEDULE_MARKER not in l)
    combined = (cleaned.rstrip() + "\n" + new_line) if cleaned.strip() else new_line
    proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE,
                            text=True)
    proc.communicate(combined)
    if proc.returncode != 0:
        print(f"[schedule] crontab write failed (rc={proc.returncode})",
              file=sys.stderr)
        return proc.returncode
    print(f"[schedule] added cron entry (marker: {SCHEDULE_MARKER})",
          file=sys.stderr)
    print(f"[schedule] view: crontab -l", file=sys.stderr)
    print(f"[schedule] log:  tail -f ~/.muninn-cron.log", file=sys.stderr)
    return 0


def uninstall_cron() -> int:
    if shutil.which("crontab") is None:
        return 0
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if r.returncode != 0:
            return 0  # no crontab at all — nothing to remove
        current = r.stdout
    except FileNotFoundError:
        return 0
    cleaned = "\n".join(l for l in current.splitlines()
                        if SCHEDULE_MARKER not in l)
    if cleaned == current.rstrip("\n"):
        print("[schedule] no Muninn cron entries found", file=sys.stderr)
        return 0
    proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE,
                            text=True)
    proc.communicate(cleaned)
    print("[schedule] removed Muninn cron entries", file=sys.stderr)
    return 0


def install_windows_task(mode: str, input_dir: Path, glob: str,
                         interval_min: int,
                         dry_run: bool = False) -> int:
    cmd = render_schtasks_create(mode, input_dir, glob, interval_min,
                                 _python_exe(), _muninn_script(),
                                 dry_run=dry_run)
    rc = subprocess.call(cmd)
    if rc != 0:
        return rc
    name = WINDOWS_TASK_WATCH if mode == "watch" else WINDOWS_TASK_PERIODIC
    print(f"[schedule] created task: {name}", file=sys.stderr)
    print(f"[schedule] view: schtasks /Query /TN {name}", file=sys.stderr)
    print(f"[schedule] run now: schtasks /Run /TN {name}", file=sys.stderr)
    return 0


def uninstall_windows_task() -> int:
    found = False
    for name in (WINDOWS_TASK_WATCH, WINDOWS_TASK_PERIODIC):
        rc = subprocess.call(["schtasks", "/Delete", "/TN", name, "/F"],
                             stderr=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL)
        if rc == 0:
            found = True
            print(f"[schedule] removed scheduled task: {name}",
                  file=sys.stderr)
    if not found:
        print("[schedule] no Muninn scheduled tasks found", file=sys.stderr)
    return 0


# ── Interactive + headless entry points ────────────────────────────────────

def _prompt_int(label: str, default: int, *, min_val: int = 1,
                max_val: int = 60) -> int:
    while True:
        ans = input(f"{label} [{default}]: ").strip()
        if not ans:
            return default
        try:
            n = int(ans)
            if min_val <= n <= max_val:
                return n
        except ValueError:
            pass
        print(f" enter a number between {min_val} and {max_val}",
              file=sys.stderr)


def _prompt_str(label: str, default: str) -> str:
    ans = input(f"{label} [{default}]: ").strip()
    return ans or default


def interactive_schedule_setup() -> int:
    """Walk the user through installing a scheduled Muninn task.
    Called at the end of --setup (after the API key is saved), and also
    directly via --schedule."""
    print("", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    print(" muninn — schedule setup", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    print("", file=sys.stderr)
    print(" Muninn can run on a schedule and upload new captures",
          file=sys.stderr)
    print(" automatically. Point it at your decoder's output folder.",
          file=sys.stderr)
    print("", file=sys.stderr)
    if not _prompt_yes_no(" Set up a schedule now?", default=False):
        print("", file=sys.stderr)
        print(" Skipped. You can configure later with:", file=sys.stderr)
        print("   python3 muninn.py --schedule", file=sys.stderr)
        print("", file=sys.stderr)
        return 0

    # Mode choice
    print("", file=sys.stderr)
    print(" Two scheduling modes:", file=sys.stderr)
    print("   1) Live watch — runs in the background, uploads new captures",
          file=sys.stderr)
    print("      as soon as they appear. Best for decoders that write a",
          file=sys.stderr)
    print("      new file per capture (tar1090 chunks, NDJSON sessions).",
          file=sys.stderr)
    print("   2) Periodic — runs every N minutes against the current state.",
          file=sys.stderr)
    print("      Best for decoders that rewrite one rolling file in place",
          file=sys.stderr)
    print("      (dump1090-fa, readsb, VRS).", file=sys.stderr)
    print("", file=sys.stderr)
    while True:
        ans = input(" Choose [1/2] (default: 1): ").strip()
        if ans == "" or ans == "1":
            mode = "watch"
            break
        if ans == "2":
            mode = "periodic"
            break
        print(" enter 1 or 2", file=sys.stderr)

    # Input dir
    candidates = _guess_decoder_dirs()
    print("", file=sys.stderr)
    if candidates:
        print(" Detected likely decoder output folders:", file=sys.stderr)
        for c in candidates:
            print(f"   {c}", file=sys.stderr)
    default_dir = str(candidates[0]) if candidates else ""
    ans = _prompt_str(" Decoder output folder", default_dir)
    if not ans:
        print(" no folder given — cancelling", file=sys.stderr)
        return 1
    input_dir = Path(ans).expanduser()

    # Glob
    default_glob = _guess_glob_for_dir(input_dir)
    glob = _prompt_str(" File pattern", default_glob)

    # Interval (periodic only)
    interval_min = 5
    if mode == "periodic":
        interval_min = _prompt_int(" How often (minutes)", 5,
                                   min_val=1, max_val=60)

    # Dry-run prompt — default Yes for safety. Dry-run installs the unit
    # with --dry-run baked into ExecStart so the user can verify the
    # decode/log pipeline before flipping to live uploads.
    print("", file=sys.stderr)
    print(" Install in dry-run first? (no uploads — decodes + logs only;",
          file=sys.stderr)
    print(" re-run --schedule later to flip to live)", file=sys.stderr)
    dry_run = _prompt_yes_no(" Dry-run mode?", default=True)

    # Show preview + confirm
    mech = _schedule_mechanism()
    if mech == "cron" and mode == "watch":
        print("", file=sys.stderr)
        print(" cron can't run a long-lived watch daemon. Either install",
              file=sys.stderr)
        print(" systemd on this host or pick periodic mode. For now,",
              file=sys.stderr)
        print(" the watch command you can run yourself is:", file=sys.stderr)
        print(f"   {_python_exe()} {_muninn_script()} --watch {input_dir} "
              f"--watch-glob {glob!r} --upload", file=sys.stderr)
        return 1

    print("", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    print(" The following will be installed:", file=sys.stderr)
    print("─" * 60, file=sys.stderr)
    if mech == "systemd":
        units = render_systemd_units(mode, input_dir, glob, interval_min,
                                     _python_exe(), _muninn_script(),
                                     dry_run=dry_run)
        unit_dir = _systemd_user_dir()
        print("", file=sys.stderr)
        print(f" {unit_dir}/{SYSTEMD_SERVICE_NAME}.service:", file=sys.stderr)
        print(textwrap.indent(units["service"], "   "), file=sys.stderr)
        if units["timer"] is not None:
            print(f" {unit_dir}/{SYSTEMD_SERVICE_NAME}.timer:",
                  file=sys.stderr)
            print(textwrap.indent(units["timer"], "   "), file=sys.stderr)
        print(" Plus: systemctl --user daemon-reload && enable --now",
              file=sys.stderr)
    elif mech == "cron":
        line = render_cron_line(input_dir, interval_min,
                                _python_exe(), _muninn_script(),
                                dry_run=dry_run)
        print("", file=sys.stderr)
        print(" Appended to your user crontab:", file=sys.stderr)
        print(textwrap.indent(line, "   "), file=sys.stderr)
    elif mech == "windows":
        cmd = render_schtasks_create(mode, input_dir, glob, interval_min,
                                     _python_exe(), _muninn_script(),
                                     dry_run=dry_run)
        print("", file=sys.stderr)
        print(" schtasks command:", file=sys.stderr)
        print(f"   {' '.join(cmd)}", file=sys.stderr)
    if dry_run:
        print("", file=sys.stderr)
        print(" *** DRY-RUN: --dry-run flag is in ExecStart, no real",
              file=sys.stderr)
        print(" *** uploads will happen. Re-run --schedule (answer No",
              file=sys.stderr)
        print(" *** to dry-run) to flip to live uploads.", file=sys.stderr)
    print("", file=sys.stderr)

    if not _prompt_yes_no(" Install now?", default=True):
        print("", file=sys.stderr)
        print(" Skipped. To install non-interactively later:",
              file=sys.stderr)
        dry_flag = " --schedule-dry-run" if dry_run else ""
        print(f"   python3 muninn.py --schedule --schedule-mode {mode} "
              f"--schedule-input {input_dir} "
              f"--schedule-glob {glob!r} "
              f"--schedule-interval {interval_min}{dry_flag}",
              file=sys.stderr)
        return 0

    if mech == "systemd":
        rc = install_systemd_user(mode, input_dir, glob, interval_min,
                                  dry_run=dry_run)
    elif mech == "cron":
        rc = install_cron(input_dir, interval_min, dry_run=dry_run)
    elif mech == "windows":
        rc = install_windows_task(mode, input_dir, glob, interval_min,
                                  dry_run=dry_run)
    else:
        rc = 1

    if rc == 0:
        print("", file=sys.stderr)
        if dry_run:
            print(" ✓ Schedule installed in DRY-RUN mode (no uploads).",
                  file=sys.stderr)
            print("   Verify it works, then re-run --schedule and",
                  file=sys.stderr)
            print("   answer 'no' to the dry-run prompt to go live.",
                  file=sys.stderr)
        else:
            print(" ✓ Schedule installed (live uploads enabled).",
                  file=sys.stderr)
        print(" To remove later: python3 muninn.py --unschedule",
              file=sys.stderr)
        print("", file=sys.stderr)
    return rc


def cmd_schedule_headless(args) -> int:
    """Headless --schedule path. Reads mode/input/glob/interval from args."""
    mode = args.schedule_mode or "watch"
    if mode not in ("watch", "periodic"):
        sys.exit(f"--schedule-mode must be 'watch' or 'periodic', got {mode!r}")
    if not args.schedule_input:
        sys.exit("--schedule requires --schedule-input <dir>")
    input_dir = Path(args.schedule_input).expanduser()
    glob = args.schedule_glob or _guess_glob_for_dir(input_dir)
    interval_min = args.schedule_interval or 5
    dry_run = bool(args.schedule_dry_run)
    mech = _schedule_mechanism()
    if mech == "cron" and mode == "watch":
        sys.exit("cron can't run watch mode. Either pick --schedule-mode "
                 "periodic, or run muninn.py --watch ... in a terminal.")
    if mech == "systemd":
        return install_systemd_user(mode, input_dir, glob, interval_min,
                                    dry_run=dry_run)
    if mech == "cron":
        return install_cron(input_dir, interval_min, dry_run=dry_run)
    if mech == "windows":
        return install_windows_task(mode, input_dir, glob, interval_min,
                                    dry_run=dry_run)
    sys.exit(f"unsupported platform: {sys.platform}")


def cmd_unschedule() -> int:
    """Remove every Muninn-managed schedule entry on this platform."""
    mech = _schedule_mechanism()
    rcs = []
    # Always try all three on Linux — user may have moved between cron
    # and systemd between installs.
    if sys.platform == "win32":
        rcs.append(uninstall_windows_task())
    else:
        if _has_systemd():
            rcs.append(uninstall_systemd_user())
        rcs.append(uninstall_cron())
    return 0 if all(rc == 0 for rc in rcs) else 1


# ── CLI ─────────────────────────────────────────────────────────────────────
def _process_one_file(path: Path, args) -> tuple[int, list[dict]]:
    """Decode a single capture file and write its JSON output.
    Returns (exit_code, records). Does NOT upload — caller decides."""
    fmt = args.format if args.format != "auto" else detect_format(path)
    if not _QUIET:
        print(f"[muninn] detected format: {fmt}", file=sys.stderr)

    if fmt == "avr" or fmt == "avr-tagged":
        rows = parse_avr(path)
    elif fmt == "sbs1":
        rows = parse_sbs1(path)
    elif fmt == "json":
        rows = parse_json(path)
    elif fmt == "mayhem":
        rows = parse_mayhem(path)
    elif fmt == "gdl90":
        rows = parse_gdl90(path)
    elif fmt == "beast":
        rows = parse_beast(path)
    elif fmt == "csv":
        rows = parse_csv(path, fmt=args.csv_format)
    elif fmt == "sqb":
        rows = parse_sqb(path, tz_override=args.sqb_tz)
    elif fmt == "empty":
        print(f"[muninn] {path.name}: empty file, skipping", file=sys.stderr)
        return 0, []
    else:
        print(f"[muninn] {path.name}: unknown format ({fmt}), skipping",
              file=sys.stderr)
        return 1, []

    records = list(rows.values())
    _warn_range(records)
    if not _QUIET:
        print(f"[muninn] decoded {len(records)} unique aircraft with positions",
              file=sys.stderr)

    # --preview: print the first 6 normalised records as JSON-lines and stop.
    # No file write, no upload. Mirrors Heimdall's --preview for muscle-memory
    # consistency across the feeder family. Caller (the main dispatcher) also
    # gates --upload behind `not args.preview` so `--preview --upload`
    # surfaces the parse without actually posting.
    if getattr(args, "preview", False):
        for rec in records[:6]:
            print(json.dumps(rec))
        return 0, records

    web_payload = _to_dump1090_fa(records)
    out_path: Path | None = None

    if args.stdout:
        print(json.dumps(web_payload, indent=2))
    elif args.out:
        out_path = Path(args.out).expanduser().resolve()
    elif args.out_dir:
        od = Path(args.out_dir).expanduser().resolve()
        out_path = od / f"{path.stem}.wdgwars.json"
    elif args.upload and args.no_save:
        out_path = None
    else:
        out_path = (path.parent / f"{path.stem}.wdgwars.json").resolve()

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(web_payload, indent=2))
        _OUT_DIRS_WRITTEN.add(out_path.parent.resolve())
        if not _QUIET:
            print(f"[muninn] OK -- wrote {len(records)} aircraft to:\n"
                  f"       {out_path}", file=sys.stderr)

    return 0, records


def _do_upload(records: list[dict], args) -> int:
    """Resolve API key (prompting if needed) and upload records."""
    key = load_key(args.key)
    if not key:
        print("\n[muninn] --upload was passed but no API key is configured.",
              file=sys.stderr)
        rc = interactive_setup()
        if rc != 0:
            return rc
        key = load_key(args.key)
        if not key:
            print("[muninn] no key saved — skipping upload. Your local JSON "
                  "file(s) were still written.", file=sys.stderr)
            return 0
    return upload(records, key, args.api_url,
                  batch_size=args.batch_size, dry_run=args.dry_run)



def _check_dump1090_net() -> None:
    if _QUIET:
        return
    import socket
    PORTS = {
        30104: 'Beast input (--net-bi-port) -- accepts remote aircraft feeds',
        30001: 'raw input (--net-ri-port) -- accepts remote raw Mode-S',
    }
    open_ports = []
    for port, desc in PORTS.items():
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                open_ports.append((port, desc))
        except OSError:
            pass
    if not open_ports:
        return
    print('[muninn] WARNING: dump1090 network input port(s) are open on localhost:', file=sys.stderr)
    for port, desc in open_ports:
        print(f'[muninn]   port {port}: {desc}', file=sys.stderr)
    print('[muninn]   Remote aircraft data may be mixing with locally received planes.', file=sys.stderr)
    print('[muninn]   If you see aircraft far outside your area, this is the likely cause.', file=sys.stderr)
    print('[muninn]   Fix: restart dump1090 with --net-bi-port 0 --net-ri-port 0 to block input while keeping output.', file=sys.stderr)


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse a dotted semver-ish string into a tuple of ints.

    Tolerates leading "v", trailing pre-release / build metadata
    ("2.0.0-rc1" → (2, 0, 0)), and missing trailing components
    ("2" → (2,)). Anything unparseable returns an empty tuple so the
    caller can decide what to do (we treat that as "skip the check").
    """
    s = (v or "").lstrip("v").strip()
    # Strip pre-release / build suffix before parsing
    for sep in ("-", "+", " "):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts: list[int] = []
    for chunk in s.split("."):
        if not chunk.isdigit():
            return ()
        parts.append(int(chunk))
    return tuple(parts)


def _check_for_update() -> str | None:
    """Quick non-blocking version check against the GitHub releases API.
    Cached for 24h in the user's config dir so we don't hammer the API.
    Returns the latest tag string IF it parses as strictly newer than
    __version__, else None.

    Comparing strictly newer (rather than not-equal) avoids the false
    positive where a user on a development version (e.g. 2.0.0) sees
    "v1.11.1 is available" because the GitHub release tag still lags
    the local version.
    """
    cache = _key_path().parent / "version-check.json"
    cur_v = _version_tuple(__version__)
    try:
        if cache.exists():
            blob = json.loads(cache.read_text())
            if time.time() - blob.get("checked_at", 0) < 86400:
                latest = blob.get("latest")
                if latest and _version_tuple(latest) > cur_v:
                    return latest
                return None
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": f"muninn/{__version__}"})
        with urllib.request.urlopen(req, timeout=3, context=gungnir.transport.SSL_CTX) as r:
            data = json.loads(r.read())
            latest = (data.get("tag_name") or "").lstrip("v")
    except Exception:
        return None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
    except Exception:
        pass
    if latest and _version_tuple(latest) > cur_v:
        return latest
    return None


def _run_update() -> int:
    """Try to update muninn in place. Uses `git pull` if we're in a git
    checkout; otherwise downloads muninn.py + requirements.txt from raw
    GitHub. Either path then `pip install`s requirements.txt so a release
    that bumps deps (e.g. a new gungnir pin) doesn't leave the user with
    an updated muninn.py importing a module they don't have."""
    import subprocess
    script_dir = Path(__file__).resolve().parent
    git_dir = script_dir / ".git"
    if git_dir.exists():
        print(f"[muninn] updating via git pull in {script_dir}", file=sys.stderr)
        try:
            r = subprocess.run(["git", "-C", str(script_dir), "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=30)
            print(r.stdout.strip(), file=sys.stderr)
            if r.returncode != 0:
                print(r.stderr.strip(), file=sys.stderr)
                return r.returncode
            _pip_install_requirements(script_dir)
            print(f"[muninn] now on muninn v{__version__} (re-run with --version "
                  f"to confirm latest)", file=sys.stderr)
            return 0
        except FileNotFoundError:
            print("[muninn] git not found in PATH. Install git, or download muninn.py manually.",
                  file=sys.stderr)
            return 1
    else:
        return _update_from_raw(script_dir)


def _fetch_raw(path: str, dest: Path) -> bool:
    """Fetch a file from the repo's main branch to dest atomically.
    Returns True on success, False on failure (logs the reason)."""
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{path}"
    print(f"[muninn] fetching {path} from {raw_url}", file=sys.stderr)
    try:
        req = urllib.request.Request(raw_url, headers={
            "User-Agent": f"muninn/{__version__}"})
        with urllib.request.urlopen(req, timeout=15, context=gungnir.transport.SSL_CTX) as r:
            body = r.read()
    except Exception as e:
        print(f"[muninn] download of {path} failed: {e}", file=sys.stderr)
        return False
    tmp = dest.with_suffix(dest.suffix + ".new")
    try:
        tmp.write_bytes(body)
        os.replace(tmp, dest)
    except OSError as e:
        print(f"[muninn] couldn't write {dest}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def _pip_install_requirements(script_dir: Path) -> None:
    """Best-effort `python -m pip install -r requirements.txt` against the
    interpreter currently running muninn. Never fails the caller — prints
    a clear hint if pip is missing or the install errors out, so the
    update return code still reflects the muninn.py update itself."""
    import subprocess
    req = script_dir / "requirements.txt"
    if not req.exists():
        print(f"[muninn] no requirements.txt at {req}, skipping deps install",
              file=sys.stderr)
        return
    print(f"[muninn] installing/refreshing deps from {req.name} "
          f"(python -m pip install --upgrade -r requirements.txt)", file=sys.stderr)
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                            "-r", str(req)], timeout=300)
    except FileNotFoundError:
        print("[muninn] python not found to invoke pip; run "
              "`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)
        return
    except subprocess.TimeoutExpired:
        print("[muninn] pip install timed out; run "
              "`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)
        return
    if r.returncode != 0:
        print(f"[muninn] pip install exited {r.returncode}; if the import "
              f"errors below mention a missing module, run "
              f"`python -m pip install -r requirements.txt` manually.",
              file=sys.stderr)


def _update_from_raw(script_dir: Path) -> int:
    """Non-git fallback for --update: fetch muninn.py + requirements.txt
    from raw GitHub and replace the local files atomically, then refresh
    deps. Works for ZIP-downloaded installs."""
    target = script_dir / "muninn.py"
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/muninn.py"
    print(f"[muninn] not a git checkout. Fetching latest muninn.py from "
          f"{raw_url}", file=sys.stderr)
    try:
        req = urllib.request.Request(raw_url, headers={
            "User-Agent": f"muninn/{__version__}"})
        with urllib.request.urlopen(req, timeout=15, context=gungnir.transport.SSL_CTX) as r:
            new_text = r.read().decode("utf-8")
    except Exception as e:
        print(f"[muninn] download failed: {e}", file=sys.stderr)
        print(f"[muninn] manual download: "
              f"https://github.com/{GITHUB_REPO}/releases/latest", file=sys.stderr)
        return 1
    try:
        import ast
        ast.parse(new_text)
    except SyntaxError as e:
        print(f"[muninn] downloaded file failed to parse, aborting: {e}",
              file=sys.stderr)
        return 1
    import re as _re
    m = _re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',
                   new_text, _re.MULTILINE)
    new_version = m.group(1) if m else "?"
    if new_version == __version__:
        print(f"[muninn] already on the latest (v{__version__}). Refreshing "
              f"requirements.txt in case a pinned dep moved.", file=sys.stderr)
        _fetch_raw("requirements.txt", script_dir / "requirements.txt")
        _pip_install_requirements(script_dir)
        return 0
    tmp = target.with_suffix(".py.new")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as e:
        print(f"[muninn] couldn't write {target}: {e}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return 1
    print(f"[muninn] updated v{__version__} to v{new_version}", file=sys.stderr)
    _fetch_raw("requirements.txt", script_dir / "requirements.txt")
    _pip_install_requirements(script_dir)
    print(f"[muninn] re-run muninn to pick up the new code "
          f"(the current process is still running the old version).",
          file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=f"Muninn v{__version__} — Convert ADS-B capture text files "
                    f"to WDGoWars aircraft JSON, and optionally upload to wdgwars.pl.",
        epilog="Format is auto-detected (AVR raw / SBS-1 / dump1090 JSON / "
               "generic CSV / PortaPack Mayhem / RTL1090 BaseStation .sqb). "
               "For generic CSV inputs, pass --csv-format to specify the "
               "column order.",
    )
    ap.add_argument("--version", action="version",
                    version=f"muninn {__version__}")
    ap.add_argument("--update", action="store_true",
                    help="pull the latest version of muninn (uses git pull "
                         "if you cloned the repo, otherwise downloads muninn.py from GitHub)")
    ap.add_argument("input", nargs="*",
                    help="ADS-B capture file (.txt, .csv, .json) "
                         "OR a directory when used with --watch. "
                         "Not required when using --save-key or --whoami. "
                         "Unquoted paths with spaces are auto-joined.")
    ap.add_argument("--setup", action="store_true",
                    help="interactive first-time setup — prompts for your "
                         "WDGoWars API key, validates it, saves it locally.")
    ap.add_argument("--save-key", metavar="KEY",
                    help="non-interactive: save the given API key to the user "
                         "config dir. Prefer --setup for first-time install.")
    ap.add_argument("--whoami", action="store_true",
                    help="validate your stored API key by hitting /api/me and "
                         "showing your account stats; exits after.")
    ap.add_argument("--schedule", action="store_true",
                    help="install or reconfigure a scheduled Muninn task. "
                         "Interactive when run alone; with --schedule-mode "
                         "+ --schedule-input + (--schedule-glob + "
                         "--schedule-interval) runs headless.")
    ap.add_argument("--unschedule", action="store_true",
                    help="remove every Muninn-managed scheduled task on "
                         "this host (systemd user units, cron entries, "
                         "Windows scheduled tasks).")
    ap.add_argument("--schedule-mode", choices=["watch", "periodic"],
                    help="for --schedule headless mode")
    ap.add_argument("--schedule-input", metavar="DIR",
                    help="decoder output dir (used by --schedule headless mode)")
    ap.add_argument("--schedule-glob", metavar="PATTERN",
                    help="file pattern in --schedule-input (default: "
                         "best-guess based on directory contents)")
    ap.add_argument("--schedule-interval", type=int, metavar="MINUTES",
                    help="for --schedule-mode periodic: minutes between "
                         "ticks (default: 5)")
    ap.add_argument("--schedule-dry-run", action="store_true",
                    help="install the schedule with --dry-run baked into "
                         "the unit/cron/task. Decodes + logs but never "
                         "uploads. Lets you verify the install before "
                         "flipping to live. Re-run --schedule without "
                         "this flag to go live.")
    ap.add_argument("--watch", action="store_true",
                    help="watch the input as a directory and process new "
                         "files as they appear (loops until Ctrl+C)")
    ap.add_argument("--watch-interval", type=int, default=30,
                    help="seconds between directory polls when --watch is set "
                         "(default: 30)")
    ap.add_argument("--watch-glob", default="*.txt",
                    help="glob pattern for files in the watched dir "
                         "(default: *.txt). Use '*' for everything.")
    ap.add_argument("--out", "-o", help="write JSON to this exact path "
                    "(default: <input>.wdgwars.json next to the input file)")
    ap.add_argument("--out-dir", metavar="DIR",
                    help="write all output JSON into this directory instead "
                         "of next to each input file. Created if missing.")
    ap.add_argument("--stdout", action="store_true",
                    help="print JSON to stdout instead of writing a file")
    ap.add_argument("--no-save", action="store_true",
                    help="with --upload: skip writing the local audit-trail JSON")
    ap.add_argument("--format", choices=["auto", "avr", "sbs1", "json", "csv", "mayhem", "sqb"],
                    default="auto", help="force input format (default: auto-detect)")
    ap.add_argument("--csv-format", help="comma-separated column names for "
                    "generic CSV: icao,callsign,lat,lon,alt_ft,...")
    ap.add_argument("--sqb-tz", dest="sqb_tz", metavar="ZONE", default=None,
                    help="IANA timezone (e.g. America/New_York) for "
                         "interpreting BaseStation .sqb timestamps. "
                         "BaseStation does not store TZ info, so muninn "
                         "defaults to treating those strings as UTC.")
    ap.add_argument("--upload", action="store_true",
                    help="POST to wdgwars.pl after conversion (default endpoint bypasses Cloudflare L7 rate-limit; see --api-url)")
    ap.add_argument("--preview", action="store_true",
                    help="parse the input and print the first 6 normalised "
                         "records as JSON-lines to stdout, then exit. No "
                         "file write, no upload. Useful for confirming the "
                         "parser understands your decoder's output before "
                         "wiring it into a watch loop or schedule. Mirrors "
                         "Heimdall's --preview for cross-tool consistency.")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --upload, build the request but don't send")
    ap.add_argument("--key", help="WDGoWars API key (overrides $WDGWARS_API_KEY)")
    ap.add_argument("--api-url", default=DEFAULT_API_URL,
                    help=f"override upload endpoint (default: {DEFAULT_API_URL})")
    ap.add_argument("--batch-size", type=int, default=1000,
                    help="aircraft per upload chunk (default: 1000)")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="suppress informational output (banners, format/decoded "
                         "notices, dump1090 + range warnings). Errors still print.")
    ap.add_argument("--no-version-check", action="store_true",
                    help="skip the daily GitHub release check entirely "
                         "(use for offline / privacy-conscious setups).")
    ap.add_argument("--open", dest="open_after", action="store_true",
                    help="after writing JSON, open the output folder in your "
                         "OS file manager (Explorer / Finder / xdg-open).")
    ap.add_argument("--config", dest="show_config", action="store_true",
                    help="show current Muninn config (saved folders, API key "
                         "status, version) and exit.")
    ap.add_argument("--reset", action="store_true",
                    help="forget the saved input/output folder choice. The next "
                         "run re-prompts. API key is NOT touched.")
    args = ap.parse_args()

    global _QUIET
    _QUIET = args.quiet

    # Self-update mode — handle first, doesn't need an input
    if args.update:
        return _run_update()

    # Show config — pure read, no side effects.
    if args.show_config:
        print(f"Muninn v{__version__}")
        print(f"Config dir:   {_config_dir()}")
        prefs = _load_folder_prefs()
        if prefs:
            print(f"Input folder: {prefs.get('input', '?')}")
            print(f"Output folder:{prefs.get('output', '?')}")
        else:
            print("Folders:      not set (next run will prompt)")
        kp = _key_path()
        print(f"API key:      {'set (' + str(kp) + ')' if kp.exists() else 'not set'}")
        return 0

    # Reset folder choice — does not touch the API key.
    if args.reset:
        p = _folders_config_path()
        if p.exists():
            p.unlink()
            print(f"[muninn] removed {p} — next run will re-prompt for folder choice.",
                  file=sys.stderr)
        else:
            print("[muninn] no saved folder choice to reset.", file=sys.stderr)
        return 0

    # Soft nudge: if a newer release is out, mention it (non-blocking, daily-cached).
    # Skipped under --quiet and --no-version-check.
    if not args.quiet and not args.no_version_check:
        newer = _check_for_update()
        if newer:
            print(f"[muninn] note: v{newer} is available "
                  f"(you're on v{__version__}). Run `--update` to upgrade.",
                  file=sys.stderr)

    _check_dump1090_net()

    # Key management modes — handle before requiring an input file
    if args.setup:
        return interactive_setup()
    if args.save_key:
        save_key(args.save_key)
        return 0
    if args.unschedule:
        return cmd_unschedule()
    if args.schedule:
        # If any headless arg is provided, run headless; else interactive.
        headless = (args.schedule_mode is not None
                    or args.schedule_input is not None
                    or args.schedule_glob is not None
                    or args.schedule_interval is not None
                    or args.schedule_dry_run)
        if headless:
            return cmd_schedule_headless(args)
        return interactive_schedule_setup()
    if args.whoami:
        key = load_key(args.key)
        if not key:
            sys.exit("no API key found — run `python3 muninn.py --setup` "
                     "for first-time setup")
        return check_whoami(key)

    # Zero-config mode: if user ran `python3 muninn.py` with no input,
    # use the saved input/output folders (prompts on first run to ask
    # whether they want them here or on the Desktop). Lets non-technical
    # users just drop files in a folder and run the script.
    if not args.input:
        script_dir = Path(__file__).resolve().parent
        prefs = _load_folder_prefs()
        if prefs:
            default_in  = Path(prefs["input"])
            default_out = Path(prefs["output"])
        else:
            default_in, default_out = _prompt_folder_choice(script_dir)

        if default_in.is_dir():
            captures = [p for p in default_in.iterdir()
                        if p.is_file() and not p.name.startswith(".")
                        and p.suffix.lower() in (".txt", ".csv", ".json", ".log")
                        and not p.name.lower().endswith("readme.md")]
            if captures:
                args.input = [str(default_in)]
                if not args.out_dir:
                    args.out_dir = str(default_out)
                print(f"[muninn] zero-config: processing {len(captures)} file(s) "
                      f"from {default_in} -> {default_out}",
                      file=sys.stderr)
            else:
                print(f"[muninn] {default_in} is empty — drop your ADS-B "
                      f"capture in there and re-run.", file=sys.stderr)
                print(f"[muninn]   Supported: .txt (AVR/SBS-1/Mayhem), "
                      f".csv (generic, --csv-format), .json (dump1090/readsb), .log",
                      file=sys.stderr)
                print(f"[muninn]   To change the folder choice, run "
                      f"`python muninn.py --reset`.", file=sys.stderr)
                if args.open_after:
                    _open_folder(default_in)
                return 0

    if not args.input:
        ap.error("input file/directory is required (unless using --save-key or --whoami)")

    # Be forgiving about unquoted paths with spaces on Windows.
    # `python muninn.py C:\foo bar\file.txt`  -> args.input = ["C:\\foo", "bar\\file.txt"]
    # Try the joined form first, fall back to first-arg-only.
    raw = args.input if isinstance(args.input, list) else [args.input]
    joined = " ".join(raw)
    if len(raw) > 1 and Path(joined).exists():
        path = Path(joined)
        print(f"[muninn] note: input path had unquoted spaces — interpreting as "
              f"{path.name!r}. (quote the path to silence this.)", file=sys.stderr)
    else:
        path = Path(raw[0])

    if not path.exists():
        sys.exit(f"input not found: {path}\n"
                 f"hint: on Windows, wrap paths with spaces in double quotes:\n"
                 f'  python3 muninn.py "C:\\path with spaces\\file.txt"')

    # Watch mode — directory, loop forever
    if args.watch:
        return watch_dir(path, args)

    # Directory in single-pass mode (not --watch): iterate over files once
    if path.is_dir():
        captures = sorted(p for p in path.iterdir()
                          if p.is_file() and not p.name.startswith(".")
                          and p.suffix.lower() in (".txt", ".csv", ".json", ".log")
                          and not p.name.lower().endswith("readme.md")
                          and not p.name.endswith(".wdgwars.json"))
        if not captures:
            print(f"[muninn] no capture files found in {path}", file=sys.stderr)
            return 0
        print(f"[muninn] processing {len(captures)} file(s) from {path}",
              file=sys.stderr)
        all_records: list[dict] = []
        for f in captures:
            print(f"\n[muninn] --- {f.name} ---", file=sys.stderr)
            rc, recs = _process_one_file(f, args)
            if rc != 0:
                print(f"[muninn] skipped {f.name} (rc={rc})", file=sys.stderr)
                continue
            all_records.extend(recs)
        upload_rc = (
            _do_upload(all_records, args)
            if (args.upload and all_records and not args.preview)
            else 0
        )
        if args.open_after:
            for d in _OUT_DIRS_WRITTEN:
                _open_folder(d)
        return upload_rc

    # Single file
    rc, records = _process_one_file(path, args)
    if rc != 0:
        return rc
    upload_rc = (
        _do_upload(records, args)
        if (args.upload and not args.preview)
        else 0
    )
    if args.open_after:
        for d in _OUT_DIRS_WRITTEN:
            _open_folder(d)
    return upload_rc

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""muninn.py — convert ADS-B capture files to the WDGoWars aircraft
upload JSON, and optionally POST directly to the server.

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
envelope and POSTed to https://wdgwars.pl/api/upload/ (the **trailing slash
is required** — without it the server rejects every payload as a replay).

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

__version__ = "1.10.0"
GITHUB_REPO = "HiroAlleyCat/adsb-to-wdgwars"

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
import base64
import csv
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import ssl
import urllib.error
import urllib.request

# Explicit SSL context — defense in depth. urllib.request defaults to system
# trust store and full cert verification since Python 3.4.3 (PEP 476), but
# being explicit makes this the obvious answer in code review.
_SSL_CTX = ssl.create_default_context()
# create_default_context() already enables: cert verification, hostname check,
# TLS 1.2 minimum, secure ciphers. Don't weaken any of these.
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_API_URL = "https://wdgwars.pl/api/upload/"
ME_API_URL = "https://wdgwars.pl/api/me"

# Persistent API key location — XDG-style on Linux/Mac, %APPDATA% on Windows.
def _config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "muninn"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "muninn"


def _key_path() -> Path:
    return _config_dir() / "api.key"


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
    """Mini y/n prompt for use outside interactive_setup()."""
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        print(question + suffix, end="", flush=True, file=sys.stderr)
        ans = sys.stdin.readline().strip().lower()
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
    """Resolve API key in priority order:
    1. --key CLI flag
    2. $WDGWARS_API_KEY env var
    3. ~/.config/muninn/api.key (saved via --save-key)
    """
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("WDGWARS_API_KEY", "").strip()
    if env:
        return env
    p = _key_path()
    if p.exists():
        try:
            return p.read_text().strip()
        except Exception as e:
            print(f"warn: could not read {p}: {e}", file=sys.stderr)
    return ""


def _prompt_yes_no(question: str, default: bool = True) -> bool:
    """Ask a y/n question on stderr. Returns True for yes, False for no.
    On EOF / Ctrl+C, returns the default so non-interactive runs don't hang."""
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            print(question + suffix, end="", flush=True, file=sys.stderr)
            line = sys.stdin.readline()
            if not line:  # EOF
                print("", file=sys.stderr)
                return default
            ans = line.strip().lower()
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
        print(" ✓ Setup complete. You can now run uploads without --key:",
              file=sys.stderr)
        print("   python3 muninn.py yourfile.txt --upload",
              file=sys.stderr)
        print("", file=sys.stderr)
        return 0


def save_key(key: str) -> None:
    """Save the API key to user config. Refuses to write through a symlink
    (anti-symlink-attack: prevents overwriting unrelated files if someone
    points api.key at e.g. ~/.ssh/id_rsa)."""
    p = _key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to follow a symlink — would let an attacker redirect the write
    if p.is_symlink():
        sys.exit(f"refusing to write through symlink: {p} -> {os.readlink(p)}\n"
                 f"remove the symlink and re-run --save-key")
    # Write with restrictive permissions atomically: chmod the empty file BEFORE
    # writing the secret, so it's never world-readable even briefly.
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (key.strip() + "\n").encode())
    finally:
        os.close(fd)
    # Belt+suspenders chmod on Unix (no-op on Windows)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    print(f"[muninn] saved API key to {p}", file=sys.stderr)
    print(f"[muninn] (file mode 600 — only your user can read it)", file=sys.stderr)
    print(f"[muninn] you can now run uploads without --key or env var",
          file=sys.stderr)


def _scrub(text: str, key: str) -> str:
    """Defensive: if the API key ever leaks into a server error message or
    exception trace, redact it before we print to the terminal."""
    if key and len(key) > 8 and key in text:
        return text.replace(key, key[:4] + "…" + key[-4:])
    return text


def check_whoami(key: str) -> int:
    """Hit /api/me to validate the key. Prints username + counts on success.
    Never echoes the API key in any output, even on failure."""
    req = urllib.request.Request(
        ME_API_URL,
        headers={"X-API-Key": key,
                 "User-Agent": "muninn/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
            if not data.get("ok"):
                # Only show the error field, not the whole response dict
                # (response shape is server-controlled — defensive)
                err = data.get("error", "unknown")
                print(f"[muninn] key rejected: {_scrub(err, key)}",
                      file=sys.stderr)
                return 1
            print(f"[muninn] key OK — user={data.get('username')}",
                  file=sys.stderr)
            print(f"[muninn]   wifi={data.get('wifi', 0)} "
                  f"ble={data.get('ble', 0)} aircraft={data.get('aircraft', 0)} "
                  f"total={data.get('total', 0)}", file=sys.stderr)
            return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(f"[muninn] HTTP {e.code}: {_scrub(body, key)}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[muninn] whoami failed: {_scrub(str(e), key)}", file=sys.stderr)
        return 1


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
            head4 = fh.read(4)
        if len(head4) >= 2 and head4[0] == 0x7E and head4[1] in (
            0x00, 0x07, 0x0A, 0x0B, 0x14, 0x4D, 0x65,
        ):
            return "gdl90"
        # Mode-S Beast binary: 0x1A start, then type byte 0x31/0x32/0x33.
        # Like GDL-90 sniff, the message-type whitelist avoids matching a
        # text file that happens to start with the Ctrl+Z character.
        if len(head4) >= 2 and head4[0] == 0x1A and head4[1] in (0x31, 0x32, 0x33):
            return "beast"
    except OSError:
        pass

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
    against /api/upload/ and does NOT use this format."""
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


def _norm_record(icao: str, *, callsign: str = "", lat: float | None = None,
                 lon: float | None = None, alt_ft: int = 0, speed_kt: int = 0,
                 heading: int = 0, first_seen: str | None = None) -> dict | None:
    """Build a record matching the WDGoWars aircraft schema. Drops the record
    if it lacks position."""
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    icao = (icao or "").upper().lstrip("0") or "000000"
    return {
        "icao": icao.upper(),
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
            alt_ft=int(ac.get("alt_baro") or ac.get("altitude")
                       or ac.get("alt") or ac.get("Alt") or 0),
            speed_kt=int(float(ac.get("gs") or ac.get("speed")
                               or ac.get("Spd") or ac.get("Speed") or 0)),
            heading=int(float(ac.get("track") or ac.get("heading")
                              or ac.get("Trak") or ac.get("Track") or 0)),
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
        alt_ft=int(float(d.get("alt_ft") or d.get("alt") or d.get("altitude") or 0)),
        speed_kt=int(float(d.get("speed_kt") or d.get("speed") or d.get("gs") or 0)),
        heading=int(float(d.get("heading") or d.get("track") or d.get("cog") or 0)),
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
           batch_size: int = 1000, dry_run: bool = False) -> int:
    if not records:
        print("nothing to upload", file=sys.stderr)
        return 0
    total_imported = 0
    total_seen = 0
    total_sent = 0
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        payload = {"networks": [], "aircraft": chunk, "meshcore_nodes": []}
        body_json = json.dumps(payload, separators=(",", ":"))
        data_b64 = base64.b64encode(body_json.encode()).decode()
        nonce = secrets.token_hex(8)
        sig = hmac.new(api_key.encode(), (nonce + data_b64).encode(),
                       hashlib.sha256).hexdigest()
        envelope = {"data": data_b64, "nonce": nonce, "sig": sig}
        body = json.dumps(envelope).encode()
        print(f"chunk {i // batch_size + 1}/{(len(records) - 1) // batch_size + 1}: "
              f"{len(chunk)} aircraft, {len(body)} B", file=sys.stderr)
        if dry_run:
            print(f"  DRY-RUN — would POST to {api_url}", file=sys.stderr)
            continue
        req = urllib.request.Request(
            api_url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": api_key,
                "User-Agent": "muninn/1.0",
                "Accept": "application/json",
            },
        )
        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=120, context=_SSL_CTX) as resp:
                txt = resp.read().decode("utf-8", "replace")
                data = json.loads(txt) if txt else {}
                imp = data.get("aircraft_imported", 0)
                seen = data.get("aircraft_already_seen", 0)
                total_imported += imp
                total_seen += seen
                total_sent += len(chunk)
                print(f"  {_OK()} accepted in {time.monotonic() - t0:.1f}s. "
                      f"{imp} new aircraft, {seen} already on your account.",
                      file=sys.stderr)
                badges = data.get("new_badges") or []
                if badges:
                    print(f"  new badges: {badges}", file=sys.stderr)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            print(f"  {_FAIL()} rejected by wdgwars.pl (HTTP {e.code}): {_scrub(body, api_key)}",
                  file=sys.stderr)
            return 1
        except Exception as e:
            print(f"  upload error: {_scrub(str(e), api_key)}", file=sys.stderr)
            return 1
    print(f"{_OK()} Upload accepted by wdgwars.pl. Sent {total_sent} aircraft. "
          f"{total_imported} added to your score, {total_seen} already on file.",
          file=sys.stderr)
    return 0


# ── Watch mode ──────────────────────────────────────────────────────────────
def _file_signature(p: Path) -> str:
    """Cheap signature: size + mtime. Catches new files + edits without
    needing a full hash."""
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return ""


def _convert_one(path: Path, fmt_override: str | None, csv_format: str | None) -> list[dict]:
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
                                          args.csv_format)
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


def _check_for_update() -> str | None:
    """Quick non-blocking version check against the GitHub releases API.
    Cached for 24h in the user's config dir so we don't hammer the API.
    Returns the latest tag if newer than __version__, else None."""
    cache = _key_path().parent / "version-check.json"
    try:
        if cache.exists():
            blob = json.loads(cache.read_text())
            if time.time() - blob.get("checked_at", 0) < 86400:
                latest = blob.get("latest")
                return latest if latest and latest != __version__ else None
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": f"muninn/{__version__}"})
        with urllib.request.urlopen(req, timeout=3, context=_SSL_CTX) as r:
            data = json.loads(r.read())
            latest = (data.get("tag_name") or "").lstrip("v")
    except Exception:
        return None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
    except Exception:
        pass
    return latest if latest and latest != __version__ else None


def _run_update() -> int:
    """Try to update muninn in place. Uses `git pull` if we're in a git
    checkout; otherwise prints the manual update instructions."""
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
            print(f"[muninn] now on muninn v{__version__} (re-run with --version "
                  f"to confirm latest)", file=sys.stderr)
            return 0
        except FileNotFoundError:
            print("[muninn] git not found in PATH. Install git, or download muninn.py manually.",
                  file=sys.stderr)
            return 1
    else:
        return _update_from_raw(script_dir)


def _update_from_raw(script_dir: Path) -> int:
    """Non-git fallback for --update: fetch muninn.py from raw GitHub and
    replace the local file atomically. Works for ZIP-downloaded installs."""
    target = script_dir / "muninn.py"
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/muninn.py"
    print(f"[muninn] not a git checkout. Fetching latest muninn.py from "
          f"{raw_url}", file=sys.stderr)
    try:
        req = urllib.request.Request(raw_url, headers={
            "User-Agent": f"muninn/{__version__}"})
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
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
        print(f"[muninn] already on the latest (v{__version__}). Nothing to do.",
              file=sys.stderr)
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
    print(f"[muninn] re-run muninn to pick up the new code "
          f"(the current process is still running the old version).",
          file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=f"Muninn v{__version__} — Convert ADS-B capture text files "
                    f"to WDGoWars aircraft JSON, and optionally upload to wdgwars.pl.",
        epilog="Format is auto-detected (AVR raw / SBS-1 / dump1090 JSON / "
               "generic CSV / PortaPack Mayhem). For generic CSV inputs, pass "
               "--csv-format to specify the column order.",
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
    ap.add_argument("--format", choices=["auto", "avr", "sbs1", "json", "csv", "mayhem"],
                    default="auto", help="force input format (default: auto-detect)")
    ap.add_argument("--csv-format", help="comma-separated column names for "
                    "generic CSV: icao,callsign,lat,lon,alt_ft,...")
    ap.add_argument("--upload", action="store_true",
                    help="POST to wdgwars.pl/api/upload/ after conversion")
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
        upload_rc = _do_upload(all_records, args) if (args.upload and all_records) else 0
        if args.open_after:
            for d in _OUT_DIRS_WRITTEN:
                _open_folder(d)
        return upload_rc

    # Single file
    rc, records = _process_one_file(path, args)
    if rc != 0:
        return rc
    upload_rc = _do_upload(records, args) if args.upload else 0
    if args.open_after:
        for d in _OUT_DIRS_WRITTEN:
            _open_folder(d)
    return upload_rc

    return 0


if __name__ == "__main__":
    sys.exit(main())

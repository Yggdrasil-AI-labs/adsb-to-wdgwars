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
    """Sniff the first non-empty, non-comment line and decide the format."""
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#"):
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
    """Handle two shapes:
      1) dump1090 aircraft.json snapshot: {"now":..., "aircraft":[{...},...]}
      2) NDJSON / JSON-lines: one aircraft object per line
    """
    rows: dict[str, dict] = {}
    text = path.read_text(encoding="utf-8", errors="replace")

    def _ingest(ac: dict, now_ts: float | None = None):
        icao = (ac.get("hex") or ac.get("icao") or "").upper()
        if not icao:
            return
        lat = ac.get("lat")
        lon = ac.get("lon")
        if lat is None or lon is None:
            return
        ts_str = _now_iso()
        if now_ts:
            ts_str = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        rec = _norm_record(
            icao=icao,
            callsign=(ac.get("flight") or ac.get("callsign") or "").strip(),
            lat=lat, lon=lon,
            alt_ft=int(ac.get("alt_baro") or ac.get("altitude") or ac.get("alt") or 0),
            speed_kt=int(float(ac.get("gs") or ac.get("speed") or 0)),
            heading=int(float(ac.get("track") or ac.get("heading") or 0)),
            first_seen=ts_str,
        )
        if rec:
            rows[icao] = rec

    # Try whole-file JSON snapshot
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "aircraft" in obj:
            now_ts = obj.get("now")
            for ac in obj["aircraft"]:
                _ingest(ac, now_ts=now_ts)
            return rows
        if isinstance(obj, list):
            for ac in obj:
                _ingest(ac)
            return rows
    except json.JSONDecodeError:
        pass

    # Fall back to JSON-lines
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
                print(f"  HTTP {resp.status} in {time.monotonic() - t0:.1f}s "
                      f"imported={imp} already_seen={seen}", file=sys.stderr)
                badges = data.get("new_badges") or []
                if badges:
                    print(f"  new badges: {badges}", file=sys.stderr)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            print(f"  HTTP {e.code}: {_scrub(body, api_key)}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"  upload error: {_scrub(str(e), api_key)}", file=sys.stderr)
            return 1
    print(f"DONE — aircraft_sent={total_sent} imported={total_imported} "
          f"already_seen={total_seen}", file=sys.stderr)
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
                    out_path.write_text(json.dumps(records, indent=2))
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
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert ADS-B capture text files to WDGoWars aircraft "
                    "JSON, and optionally upload to wdgwars.pl.",
        epilog="Format is auto-detected (AVR raw / SBS-1 / dump1090 JSON / "
               "generic CSV). For generic CSV inputs, pass --csv-format to "
               "specify the column order.",
    )
    ap.add_argument("input", nargs="?",
                    help="ADS-B capture file (.txt, .csv, .json) "
                         "OR a directory when used with --watch. "
                         "Not required when using --save-key or --whoami.")
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
    args = ap.parse_args()

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

    if not args.input:
        ap.error("input file/directory is required (unless using --save-key or --whoami)")
    path = Path(args.input)
    if not path.exists():
        sys.exit(f"input not found: {path}")

    # Watch mode — directory, loop forever
    if args.watch:
        return watch_dir(path, args)

    fmt = args.format if args.format != "auto" else detect_format(path)
    print(f"[muninn] detected format: {fmt}", file=sys.stderr)

    if fmt == "avr" or fmt == "avr-tagged":
        rows = parse_avr(path)
    elif fmt == "sbs1":
        rows = parse_sbs1(path)
    elif fmt == "json":
        rows = parse_json(path)
    elif fmt == "mayhem":
        rows = parse_mayhem(path)
    elif fmt == "csv":
        rows = parse_csv(path, fmt=args.csv_format)
    elif fmt == "empty":
        sys.exit("input file is empty")
    else:
        sys.exit(f"unknown format: {fmt}")

    records = list(rows.values())
    print(f"[muninn] decoded {len(records)} unique aircraft with positions",
          file=sys.stderr)

    # Decide where output goes:
    #   --stdout            -> print to stdout, write nothing
    #   --out PATH          -> write to that exact path
    #   --upload --no-save  -> upload-only, no local file
    #   (otherwise)         -> <input>.wdgwars.json next to the input file
    out_path: Path | None = None
    if args.stdout:
        print(json.dumps(records, indent=2))
    elif args.out:
        out_path = Path(args.out).expanduser().resolve()
    elif args.upload and args.no_save:
        out_path = None
    else:
        # Default: same directory as input, suffixed .wdgwars.json
        stem = path.stem  # filename without extension
        out_path = (path.parent / f"{stem}.wdgwars.json").resolve()

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(records, indent=2))
        print(f"\n[muninn] OK -- wrote {len(records)} aircraft to:\n"
              f"       {out_path}\n", file=sys.stderr)

    if args.upload:
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
                      "file was still written.", file=sys.stderr)
                return 0
        return upload(records, key, args.api_url,
                      batch_size=args.batch_size, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""adsb_to_wdgwars.py — convert ADS-B capture files to the WDGoWars aircraft
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
  python3 adsb_to_wdgwars.py mycapture.txt --out vessels.json

  # Convert and upload (API key from env or --key)
  WDGWARS_API_KEY=YOURKEY python3 adsb_to_wdgwars.py mycapture.txt --upload

  # Show what would be sent without sending
  python3 adsb_to_wdgwars.py mycapture.txt --upload --dry-run

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
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_API_URL = "https://wdgwars.pl/api/upload/"


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
        from pyModeS.extra.tcpclient import TcpClient  # noqa: F401 — only to confirm install
    except ImportError:
        sys.exit("AVR raw input requires pyModeS — install with: pip install pyModeS")

    # CPR position decoding needs paired even/odd frames per aircraft. We
    # accumulate frames per ICAO and decode positions globally (using the
    # local ref point if provided via --ref).
    even: dict[str, tuple[float, str]] = {}  # icao -> (ts, msg)
    odd:  dict[str, tuple[float, str]] = {}
    rows: dict[str, dict] = {}

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            # Strip optional timestamp prefix; keep the *...; part
            if "*" in line:
                line = line[line.index("*"):]
            if not line.startswith("*") or not line.endswith(";"):
                continue
            msg = line[1:-1]
            if len(msg) not in (14, 28):  # short or long Mode-S
                continue
            try:
                df = pms.df(msg)
            except Exception:
                continue
            if df != 17 and df != 18:
                continue  # only ADS-B extended squitter carries positions
            try:
                icao = pms.icao(msg).upper()
                tc = pms.adsb.typecode(msg)
            except Exception:
                continue
            now = time.time()
            entry = rows.setdefault(icao, {"icao": icao, "callsign": "",
                                           "lat": None, "lon": None,
                                           "alt_ft": 0, "speed_kt": 0,
                                           "heading": 0,
                                           "first_seen": _now_iso()})
            try:
                if 1 <= tc <= 4:
                    entry["callsign"] = pms.adsb.callsign(msg).strip().rstrip("_")
                elif 9 <= tc <= 18:  # airborne position
                    oe = pms.adsb.oe_flag(msg)
                    if oe == 0:
                        even[icao] = (now, msg)
                    else:
                        odd[icao] = (now, msg)
                    if icao in even and icao in odd:
                        t0, m0 = even[icao]
                        t1, m1 = odd[icao]
                        try:
                            lat, lon = pms.adsb.airborne_position(m0, m1, t0, t1)
                            entry["lat"] = lat
                            entry["lon"] = lon
                        except Exception:
                            pass
                    try:
                        entry["alt_ft"] = pms.adsb.altitude(msg) or entry["alt_ft"]
                    except Exception:
                        pass
                elif tc == 19:  # airborne velocity
                    try:
                        v = pms.adsb.velocity(msg)
                        if v:
                            spd, hdg, _vr, _typ = v
                            if spd: entry["speed_kt"] = int(spd)
                            if hdg is not None: entry["heading"] = int(hdg)
                    except Exception:
                        pass
            except Exception:
                continue

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
                "User-Agent": "adsb-to-wdgwars/1.0",
                "Accept": "application/json",
            },
        )
        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=120) as resp:
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
            print(f"  HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}",
                  file=sys.stderr)
            return 1
        except Exception as e:
            print(f"  upload error: {e}", file=sys.stderr)
            return 1
    print(f"DONE — aircraft_sent={total_sent} imported={total_imported} "
          f"already_seen={total_seen}", file=sys.stderr)
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
    ap.add_argument("input", help="ADS-B capture file (.txt, .csv, .json)")
    ap.add_argument("--out", "-o", help="write JSON to this path (default: stdout if not uploading)")
    ap.add_argument("--format", choices=["auto", "avr", "sbs1", "json", "csv"],
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

    path = Path(args.input)
    if not path.exists():
        sys.exit(f"input file not found: {path}")

    fmt = args.format if args.format != "auto" else detect_format(path)
    print(f"[adsb] detected format: {fmt}", file=sys.stderr)

    if fmt == "avr" or fmt == "avr-tagged":
        rows = parse_avr(path)
    elif fmt == "sbs1":
        rows = parse_sbs1(path)
    elif fmt == "json":
        rows = parse_json(path)
    elif fmt == "csv":
        rows = parse_csv(path, fmt=args.csv_format)
    elif fmt == "empty":
        sys.exit("input file is empty")
    else:
        sys.exit(f"unknown format: {fmt}")

    records = list(rows.values())
    print(f"[adsb] decoded {len(records)} unique aircraft with positions",
          file=sys.stderr)

    if args.out:
        Path(args.out).write_text(json.dumps(records, indent=2))
        print(f"[adsb] wrote {args.out}", file=sys.stderr)
    elif not args.upload:
        print(json.dumps(records, indent=2))

    if args.upload:
        key = args.key or os.environ.get("WDGWARS_API_KEY", "").strip()
        if not key:
            sys.exit("no API key — pass --key or set WDGWARS_API_KEY")
        return upload(records, key, args.api_url,
                      batch_size=args.batch_size, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())

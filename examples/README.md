# Examples

Sample captures for every format Muninn auto-detects. Each file is small,
real (or derived from real data), and has a documented expected
aircraft count so you can verify a parser change end-to-end.

## Verified expected output

| File | Format | Aircraft (positions) | Notes |
|---|---|---:|---|
| `avr_sample.txt`        | AVR raw           |   0 | Smoke test — 2 frames, no valid CPR pair, parser-doesn't-crash check |
| `avr_real.txt`          | AVR raw           |   8 | Real HackRF capture |
| `sbs1_sample.txt`       | SBS-1 (BaseStation) |  3 | Hand-crafted across 3 ICAOs |
| `sbs1_real.txt`         | SBS-1             |  10 | Real port-30003 capture |
| `dump1090_sample.json`  | dump1090 / readsb |   2 | aircraft.json shape |
| `dump1090_real.json`    | dump1090 / readsb |  12 | Real readsb snapshot |
| `mayhem_sample.txt`     | PortaPack Mayhem  |   6 | HackRF Mayhem firmware output |
| `vrs_sample.json`       | VRS (acList)      |  12 | Derived from `dump1090_real.json` |
| `ndjson_sample.json`    | NDJSON / JSON-lines |  12 | One aircraft per line |
| `tar1090_chunk_sample.json.gz` | gzipped tar1090 | 12 | Gzipped dump1090 snapshot |
| `gdl90_synthetic.gdl90`        | GDL-90 binary     |   1 | Synthetic Traffic Report frame |
| `gdl90_real.gdl90`             | GDL-90 binary     |   1 | Authoritative fixture from [NathanVaughn/gdl90py](https://github.com/NathanVaughn/gdl90py/blob/main/tests/messages/test_traffic_report.py) — ICAO `AB4549`, callsign `N825V`, 44.907°N, -122.995°W |
| `stratux_sample.json`          | Stratux `/traffic` | 12 | Derived from `dump1090_real.json` |
| `beast_sample.beast`           | Mode-S Beast binary |  8 | Real AVR frames wrapped in Beast (matches `avr_real.txt` baseline) |

## Quick regression sweep

After changes to a parser, run the CLI against every sample and confirm
the counts above match:

```bash
for f in examples/*.txt examples/*.json examples/*.json.gz; do
    n=$(python3 muninn.py "$f" --out /tmp/t.json --no-version-check 2>&1 \
        | grep "decoded" | grep -oE "[0-9]+ unique")
    printf "%-44s -> %s\n" "$f" "${n:-FAILED}"
done
```

A failure on *any* line means a parser regression — flag before
shipping.

## Cached output envelopes (`*.wdgwars.json`)

Each input fixture below has a sibling `<stem>.wdgwars.json` checked into
the repo: the exact dump1090-fa-shaped JSON envelope Muninn emits for that
input. They exist for cross-implementation parity testing — if another
project re-implements one of these parsers in a different language, diff
its output against the cached envelope to confirm byte-equivalent shape.

| Cached envelope | Source fixture |
|---|---|
| `dump1090_real.wdgwars.json`    | `dump1090_real.json` |
| `sbs1_real.wdgwars.json`        | `sbs1_real.txt` |
| `sbs1_sample.wdgwars.json`      | `sbs1_sample.txt` |
| `mayhem_sample.wdgwars.json`    | `mayhem_sample.txt` |
| `stratux_sample.wdgwars.json`   | `stratux_sample.json` |
| `vrs_sample.wdgwars.json`       | `vrs_sample.json` |
| `ndjson_sample.wdgwars.json`    | `ndjson_sample.json` |
| `tar1090_chunk_sample.wdgwars.json` | `tar1090_chunk_sample.json.gz` |

To regenerate one, run `python3 muninn.py <input>` — the default output
path is `<stem>.wdgwars.json` next to the input. Note that the `now`
field is the wall-clock time of the run, so regenerating produces a
different `now` value; the aircraft list shape and counts are stable.

## Where the real captures came from

- `avr_real.txt`, `sbs1_real.txt`, `dump1090_real.json` — captured on
  a real receiver. Realistic mix of GA,
  commercial, and helicopter traffic, plus a handful of message types
  that exercise the edge cases (no position, missing altitude, etc.).
- `mayhem_sample.txt` — PortaPack Mayhem firmware on an H4M, captured
  from the same area.

The four "real" files anchor the regression tests. The `*_sample.*`
files are minimal hand-crafted examples for fast smoke tests.

## Generating new fixtures

The VRS / NDJSON / gzipped fixtures are derived from `dump1090_real.json`
so they all decode to the same 12-aircraft baseline. To regenerate:

```python
import json, gzip
from pathlib import Path
src = json.loads(Path("dump1090_real.json").read_text())
aircraft = src["aircraft"]

# VRS — acList wrapper, mixed-case keys
Path("vrs_sample.json").write_text(json.dumps({
    "src": 3, "totalAc": len(aircraft),
    "acList": [
        {"Icao": a["hex"].upper(),
         "Call": (a.get("flight") or "").strip(),
         "Lat": a.get("lat"), "Long": a.get("lon"),
         "Alt": a.get("alt_baro", 0),
         "Spd": a.get("gs", 0),
         "Trak": a.get("track", 0)}
        for a in aircraft if a.get("lat") is not None
    ],
}, indent=2))

# NDJSON — one record per line
with open("ndjson_sample.json", "w") as f:
    for a in aircraft:
        if a.get("lat") is not None:
            f.write(json.dumps({k: a[k] for k in
                ("hex", "flight", "lat", "lon", "alt_baro", "gs", "track")
                if k in a}) + "\n")

# tar1090-style gzipped chunk
with gzip.open("tar1090_chunk_sample.json.gz", "wt") as f:
    json.dump({"now": src["now"], "aircraft": aircraft}, f)
```

Keeping these derived fixtures in lockstep with `dump1090_real.json`
means the same baseline count proves all four JSON dialect paths work.

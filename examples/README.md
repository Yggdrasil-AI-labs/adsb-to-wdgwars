# Examples

Sample captures for every format Muninn auto-detects. Each file is small,
real (or derived from real data), and has a documented expected
aircraft count so you can verify a parser change end-to-end.

## Verified expected output

| File | Format | Aircraft (positions) | Notes |
|---|---|---:|---|
| `avr_sample.txt`        | AVR raw           |   1 | From `pyModeS` docs; tiny smoke test |
| `avr_real.txt`          | AVR raw           |   8 | Real HackRF capture, Cleveland area |
| `sbs1_sample.txt`       | SBS-1 (BaseStation) |  1 | Hand-crafted |
| `sbs1_real.txt`         | SBS-1             |  10 | Real port-30003 capture |
| `dump1090_sample.json`  | dump1090 / readsb |   1 | aircraft.json shape |
| `dump1090_real.json`    | dump1090 / readsb |  12 | Real readsb snapshot |
| `mayhem_sample.txt`     | PortaPack Mayhem  |   6 | HackRF Mayhem firmware output |
| `vrs_sample.json`       | VRS (acList)      |  12 | Derived from `dump1090_real.json` |
| `ndjson_sample.json`    | NDJSON / JSON-lines |  12 | One aircraft per line |
| `tar1090_chunk_sample.json.gz` | gzipped tar1090 | 12 | Gzipped dump1090 snapshot |

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

## Where the real captures came from

- `avr_real.txt`, `sbs1_real.txt`, `dump1090_real.json` — captured on
  the Cleveland-area receiver (Lorain County). Realistic mix of GA,
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

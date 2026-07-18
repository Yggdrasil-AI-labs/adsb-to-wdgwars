# Muninn / portal parity fixtures

Per-format `(input, expected-Muninn-output)` pairs for cross-implementation
parity testing. Each format directory contains the raw input fixture
(copied verbatim from
[adsb-to-wdgwars/examples/](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars/tree/main/examples))
and the JSON Muninn's reference parser produces for it.

## Shape

`*.muninn.json` is a JSON array of **flat normalised records**, one per
unique aircraft, matching `muninn.py`'s `_norm_record()` output:

```json
{
  "icao":       "AABBCC",            // uppercase 6-hex, ICAO24
  "callsign":   "DAL2805",           // trimmed of trailing spaces
  "lat":        41.083467,           // rounded to 6 decimals
  "lon":        -81.584285,
  "alt_ft":     34000,
  "speed_kt":   362,
  "heading":    302,
  "first_seen": "2026-05-11 00:35:42",   // UTC, "Y-m-d H:i:s"
  "type":       "ADSB"               // constant
}
```

This is the same shape a portal-side parser produces after its
extract-then-normalise pass, so a field-for-field diff against the
`*.muninn.json` artifact verifies parser parity end-to-end.

## Layout

```
fixtures/
  stratux/   sample.json       + expected.muninn.json   (12 records)
  vrs/       sample.json       + expected.muninn.json   (12 records)
  sbs1/      sample.txt        + expected.muninn.json   ( 3 records)
             real.txt          + real.muninn.json       (10 records)
  mayhem/    sample.txt        + expected.muninn.json   ( 6 records)
  ndjson/    sample.json       + expected.muninn.json   (12 records)
  tar1090/   sample.json.gz    + expected.muninn.json   (12 records)
  dump1090/  real.json         + real.muninn.json       (12 records)
```

Generated against Muninn v2.0.3 on 2026-05-31. To regenerate against a
newer Muninn:

```python
import muninn, json
from pathlib import Path
recs = muninn._convert_one(Path("stratux/sample.json"), None, None)
Path("stratux/expected.muninn.json").write_text(json.dumps(recs, indent=2))
```

## Known parity quirks

Two non-bug differences a portal-side diff will surface. Both are
expected; documenting them here so the test harness can mute them.

### `first_seen` defaults to wall-clock for formats with no timestamp

For input dialects that don't carry a frame timestamp (Stratux,
VirtualRadarServer when `PosTime` is absent, PortaPack Mayhem), Muninn
falls back to "now" via `_now_iso()`. The cached
`expected.muninn.json` was generated at one specific moment; a
portal-side import-now will yield a different `first_seen` string for
those formats.

Either freeze the comparison clock on both sides, or diff with
`first_seen` excluded for stratux / vrs / mayhem.

dump1090 / readsb / tar1090 use the **file-level `now` epoch** for every
record (formatted as UTC `Y-m-d H:i:s`), not `now - seen_pos`. A naive
read of dump1090's per-aircraft `seen_pos` suggests subtracting it from
`now` to get the position-fix time — Muninn deliberately doesn't, because
the snapshot timestamp is the canonical "when this record was observed"
and per-aircraft `seen_pos` drift is recorded elsewhere. Portal-side
ports should match: file-level `now` on every record.

SBS-1 derives `first_seen` from the generated date/time fields (CSV
columns 6/7). NDJSON uses any timestamp key the producer includes, or
falls back to wall-clock. Both diff bit-exact when both sides agree on
the source field.

### sbs1/real.txt: 10 records vs 11 extractor entries

`AC07DC` appears in `sbs1/real.txt` only on MSG,3 rows whose lat/lon
fields are empty (header decoded, position frame partial). Muninn's
extractor drops it at parse time because it requires lat/lon to
instantiate a per-ICAO record. A portal-side parser that instantiates
on any valid ICAO row will produce 11 extractor entries and then drop
AC07DC downstream as `mode_s_no_gps` — converging on 10 imported.

Both behaviours are correct. The diff hits if comparison is run
**before** the validation/no-GPS-drop pass on the portal side. If your
extractor produces 11, post-filter no-lat/lon rows before diffing
against `real.muninn.json`.

## Out of scope

Three formats appear in `examples/` but not here because they're out of
scope for portal-native parsers (binary framing or CPR position
decoding required). Muninn handles them:

- AVR raw Mode-S (`*XXXX;` lines, needs pyModeS for CPR)
- Mode-S Beast binary (`.beast`, escaped framing + AVR payloads)
- GDL-90 binary (`.gdl90`, byte-stuffed frames + 24-bit packed altitude)

For these, the upload path is Muninn → HMAC `/api/upload/`, not a
direct file upload to the portal.

## Source

- Reference parser: [Yggdrasil-AI-labs/adsb-to-wdgwars](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars) (Muninn, Python, MIT)
- Input fixtures: `adsb-to-wdgwars/examples/` in the same repo

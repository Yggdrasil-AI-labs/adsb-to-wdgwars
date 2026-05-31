# NOTES: `.sqb` (RTL1090 / BaseStation SQLite) input support

## What was added

A new `parse_sqb()` parser in `muninn.py` that reads Kinetic Avionics
BaseStation-schema SQLite databases (`.sqb`), plus `detect_format()`
sniffing (magic bytes `"SQLite format 3\x00"` + `.sqb` extension fallback),
dispatch wiring in both `_convert_one()` and `_process_one_file()`, two
CLI additions (`--format sqb`, `--sqb-tz ZONE`), a regenerable synthetic
fixture at `tests/fixtures/sample.sqb` (built by
`tests/fixtures/build_sample_sqb.py`), 13 unit tests in
`tests/test_sqb_input.py`, and a new row in the README's supported-format
table. Stdlib only — `sqlite3` and `zoneinfo` are both built into Python
3.9+.

## Assumptions baked in

1. **Timestamps default to UTC.** BaseStation writes naive strings like
   `"2024-08-15 14:32:11.123"` with no timezone information. The rest of
   muninn's output is UTC, so the default assumption is "this is UTC."
   `--sqb-tz America/New_York` (or any IANA zone) opts into local-time
   interpretation. On Windows installs the IANA zone database is supplied
   by the `tzdata` PyPI package, so users will need `pip install tzdata`
   if `--sqb-tz` reports an unknown zone — that's documented in the
   README.
2. **Fractional seconds are dropped.** Muninn's `first_seen` is
   whole-second resolution everywhere else, so `.123` is truncated rather
   than rounded.
3. **One Flights row → up to two records.** BaseStation stores one row
   per flight (not per position report) with `First*` / `Last*` lat/lon/
   altitude columns. We emit one record per endpoint where the
   coordinates are valid. Both endpoints survive into the upload payload
   even though they share an ICAO — the dict is keyed internally by
   `f"{icao}-{flight_idx}-first|last"` and only `.values()` is consumed
   downstream, which matches how `_to_dump1090_fa()` treats each entry as
   an independent observation.
4. **`0.0 / 0.0` is treated as "no fix", not Ghana.** BaseStation writes
   literal zeros when the receiver had no decoded position at flight
   start/end.
5. **`Last(GroundSpeed|Track)` does not exist in the schema.** The
   end-of-flight record surfaces `speed_kt=0` / `heading=0` rather than
   carrying forward the `First*` values, which would be misleading on a
   long-duration flight where the aircraft has clearly changed speed and
   heading.
6. **Schema drift is handled by introspection.** `PRAGMA table_info` is
   used on both `Aircraft` and `Flights`; missing optional columns become
   `NULL` in the projection rather than failing the SELECT. The only
   columns we hard-require are `Aircraft.ModeS` and `Flights.AircraftID`
   (without them there's no ICAO and no join).
7. **Empty / missing Flights exits nonzero.** If the `Flights` table is
   absent or empty (some installs never enable the logger), muninn
   `sys.exit`s with a clear message rather than write an empty upload
   JSON — same convention as other parsers.

## Quirks worth flagging if asked

- If the Windows user paste-uploads their `.sqb` directly into the web
  drag-and-drop UI, that won't work — the web flavour (Pyodide) doesn't
  ship the parser. They need the CLI for `.sqb`.
- The default UTC-timestamps assumption is the right call when uploading
  to WDGoWars (the portal expects UTC), but the `first_seen` field will
  be wrong by however many hours the receiver host was off UTC unless
  `--sqb-tz` is used.
- The read-only URI (`file:...?mode=ro`) means it's safe to point muninn
  at a `.sqb` that a live BaseStation / RTL1090 process is still writing
  to. The journal file is not touched.
- Per-flight records share an ICAO, so the existing "Each unique ICAO
  is kept once with its most recent position" line in `muninn.py`'s
  module docstring is now strictly inaccurate for `.sqb` inputs. I left
  the docstring alone because changing it for one format would
  understate the cross-format behaviour — happy to revise if you'd
  prefer.

## How to verify locally

```
python tests/fixtures/build_sample_sqb.py     # rebuild fixture
python -m unittest tests.test_sqb_input -v    # 13 tests, 1 skipped on
                                              # systems without tzdata
python muninn.py tests/fixtures/sample.sqb --stdout --no-version-check
```

The CLI run should emit a 4-aircraft dump1090-fa-shaped JSON to stdout.

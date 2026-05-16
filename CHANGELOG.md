# Changelog

All notable changes to Muninn are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.7.0] — 2026-05-15

### Added (experimental)
- **GDL-90 binary format** — the protocol cockpit ADS-B receivers speak
  (Stratux, ForeFlight Sentry, Garmin GDL series). Decodes Traffic
  Report (msg 0x14) and Ownship Report (msg 0x0A) frames, handles the
  0x7E/0x7D byte-stuffing per FAA Public ICD Rev A. Detected by the
  0x7E flag byte followed by a known message ID.

  **EXPERIMENTAL — needs real-capture validation.** Implemented from
  spec without a test corpus. The synthetic-frame round-trip in
  `examples/gdl90_synthetic.gdl90` passes, but field offsets, scaling
  factors, and CRC handling have not been verified against an actual
  Stratux/Sentry log. If you have a GDL-90 binary capture, please
  open an issue with a sample so the parser can be validated.

  CRC-16-CCITT FCS validation is currently skipped — frames that
  unescape cleanly and have a known message ID are accepted. This may
  change if real-world streams contain frame-aligned noise.

### Note on what we deliberately do NOT support
- **OpenSky Network**, **FlightAware**, **ADS-B Exchange** and similar
  aggregator-API formats are explicitly out of scope. WDGoWars is a
  wardriving game — the point is uploading what *your* receiver heard.
  Importing aggregated network data would defeat that and pollute the
  game's contribution model.

## [1.6.1] — 2026-05-15

### Fixed
- `detect_format` now skips `;`-prefixed comment lines in addition to
  `#`-prefixed ones. AVR captures from pyModeS-style tooling traditionally
  use `;` for block comments at the top of the file (the frame terminator
  is also `;` but always preceded by `*<hex>`, so a line that *starts*
  with `;` is unambiguously a comment). Previously, `examples/avr_sample.txt`
  was being misdetected as CSV because its first non-empty line was
  `; Sample AVR raw Mode-S ...`. End-to-end regression sweep added in
  `examples/README.md` catches this class of bug.

## [1.6.0] — 2026-05-15

### Added
- **VRS (VirtualRadarServer) JSON** — recognizes the `acList` wrapper and
  maps mixed-case field names (`Icao`, `Lat`, `Long`, `Call`, `Alt`, `Spd`,
  `Trak`) into the muninn record schema. Common among hobbyist ADS-B
  feeders running the VRS Windows server.
- **NDJSON / JSON-lines** — one JSON aircraft per line. Detected via the
  same fall-through that already existed; now documented and tested.
  Works with both dump1090 and VRS field names mixed in one stream.
- **Gzipped JSON (`.json.gz` / `.gz`)** — tar1090 history chunks decode
  transparently. Detected by extension or by 1f 8b magic bytes, so a
  hand-gzipped capture also works. Same parser, no new flags.

### Changed
- `parse_json` docstring expanded to list every JSON dialect it accepts;
  `detect_format` now sniffs through gzip transparently.

## [1.5.2] — 2026-05-15

### Added
- **`--open`** opens the output folder in your OS file manager
  (`explorer` on Windows, `open` on macOS, `xdg-open` elsewhere) after the
  JSON is written. Tracks every dir Muninn actually wrote to and pops them
  open in one batch.
- **`--config`** prints the current state (version, config dir, saved
  input/output folders, whether an API key is stored) and exits. Saves you
  poking around `~/.config/muninn/` or `%APPDATA%\muninn\`.
- **`--reset`** forgets the saved input/output folder choice so the next
  run re-prompts. Stored API keys are not touched.

### Changed
- Empty-input message now lists every supported file extension and points
  at `--reset` instead of telling users to delete a JSON file by hand.

## [1.5.1] — 2026-05-15

### Added
- **`-q` / `--quiet`** suppresses informational output — the format-detection
  notice, decoded-count line, OK/wrote summary, dump1090 network warning, and
  range-sanity warning. Errors and key-rejection messages still print. Useful
  for cron jobs and scripted pipelines that just want the JSON file.
- **`--no-version-check`** skips the daily HEAD request to GitHub's releases
  API entirely. For offline boxes and anyone who'd rather not phone home at
  all. (The check is already cached for 24 h, but this lets you opt out.)

## [1.5.0] — 2026-05-15

### Added
- **Range sanity check:** after decoding any capture file, Muninn now warns
  if aircraft positions suggest a mix of locally received and remotely fed
  data. It computes the median geographic position of all aircraft (robust
  against outliers) and flags any beyond 500 km — the approximate radio
  horizon for 1090 MHz at cruise altitude. No records are filtered; the
  warning is informational only.
- **dump1090 network input check:** at startup, Muninn probes
   (Beast input) and  (raw input). If
  either port is open it warns immediately, before processing any file. The
  most common cause of implausible reception ranges (e.g. aircraft 1500 km
  apart) is dump1090 running with  while a piaware or FlightAware
  feeder silently mixes remote aircraft into the local stream. The warning
  includes the fix: add  to block input
  while keeping dump1090 output ports active.

### Fixed
- Range centroid now uses **median** lat/lon instead of mean, so a small
  number of remote outliers cannot pull the centre point far enough to
  incorrectly flag the majority of local aircraft.

## [1.4.1] — 2026-05-11

### Docs
- README rewritten to walk through the actual first-run experience (folder
  prompt, desktop option, shortcut creation, daily workflow).
- README now lists every CLI flag, not just the common ones.
- Added status badges (latest release, MIT license, security threat model).
- SECURITY.md documents the daily version-check HEAD request and the
  desktop-shortcut creation flow.
- `input/README.md` and `output/README.md` clarified for users who picked
  the Desktop option (those files only describe the in-repo folders).

## [1.4.0] — 2026-05-11

### Added
- **Desktop install option:** picking "On Desktop" now creates a single
  `Muninn/` folder on the Desktop with `input/` and `output/` nested
  inside (cleaner than two top-level folders).
- **Desktop shortcut with raven icon (Windows).** After picking the
  Desktop option, Muninn offers to create `Muninn.lnk` on the Desktop.
  Double-click it and it opens a terminal, runs `muninn.py`, and pauses
  so you can read the output. Uses the raven icon (`assets/muninn.ico`).
- `assets/muninn.ico` — multi-resolution Windows icon (16/24/32/48/64/128/256)
  generated from `muninn.png`.

## [1.3.0] — 2026-05-11

### Added
- **First-run prompt asks where you want your input/output folders.** You can
  pick either "right here in the repo" or "on the Desktop" (auto-detects
  `~/Desktop` or `~/OneDrive/Desktop` on Windows). Choice is saved so it
  never asks again.
- Saved-folder config lives at `~/.config/muninn/folders.json`. Delete that
  file to re-prompt.

## [1.2.0] — 2026-05-11

### Added
- **`input/` and `output/` folders.** Drop capture files in `input/`, run
  `python3 muninn.py` with no arguments, get converted JSON in `output/`.
  Zero-config workflow for non-technical users.
- **`--out-dir DIR`** writes all output JSON into one directory instead of
  scattering it next to each input file.
- **Batch mode**: pointing the input at a directory (instead of a single
  file) processes every supported capture in one pass. Works with `--upload`
  too — uploads happen once at the end with all aircraft.

### Changed
- Single-file conversion logic refactored into `_process_one_file()` so the
  batch + single + watch paths all share the same code. No behavior change
  for existing invocations.

## [1.1.0] — 2026-05-11

### Added
- `--version` flag prints the running Muninn version.
- `--update` self-updates via `git pull` if you cloned the repo; otherwise
  prints the latest-release URL to download.
- Background version check (cached daily) — prints a one-line notice when a
  newer release is available. No telemetry, just a HEAD against the GitHub
  releases API.
- Interactive `--setup` is now a yes/no opt-in (banner explains the key is
  only needed if you actually want to upload — local conversion works
  without one).
- Smarter Windows path handling: unquoted paths with spaces are auto-joined,
  and bad paths print a hint suggesting double quotes.
- Dark raven banner + icon (assets/banner.png, assets/muninn.png).

### Changed
- **On-disk JSON now uses dump1090-fa / readsb shape** so the WDGoWars
  web upload form accepts the file directly. The `--upload` HMAC path
  is unaffected — it still uses the original envelope against
  `/api/upload/`.
- Rebranded from `adsb-to-wdgwars` to **Muninn**. The repo URL stays at
  `github.com/HiroAlleyCat/adsb-to-wdgwars` for searchability; the script
  is now `muninn.py` and config lives at `~/.config/muninn/`.

### Security
- API key file written with `O_CREAT | 0o600` so the secret is never
  world-readable, even briefly.
- `--save-key` refuses to write through a symlink (anti-symlink-attack).
- Error output runs through `_scrub()` so the key never leaks in tracebacks.
- Explicit `ssl.create_default_context()` for all upload traffic
  (TLS 1.2+, hostname verification, system trust store).
- Full threat model: [SECURITY.md](SECURITY.md).

## [1.0.0] — 2026-05-10

Initial public release.

### Added
- Five input format parsers, all auto-detected:
  - PortaPack Mayhem `ADSB.TXT` (HackRF H4M)
  - AVR raw Mode-S (dump1090 `--raw`, readsb port 30002) — uses pyModeS
  - SBS-1 / BaseStation CSV (port 30003)
  - dump1090 `aircraft.json` snapshot
  - Generic CSV with `--csv-format` column hints
- `--upload` POSTs to `https://wdgwars.pl/api/upload/` with an
  HMAC-SHA256-signed envelope and a per-request nonce.
- `--watch` mode polls a directory, auto-converts and uploads new files
  with state persistence in `.adsb-state.json`.
- `--save-key` and `--whoami` for persistent API-key storage.

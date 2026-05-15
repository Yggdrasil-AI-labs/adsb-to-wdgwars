# Changelog

All notable changes to Muninn are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

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

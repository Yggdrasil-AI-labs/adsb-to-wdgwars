# Changelog

All notable changes to Muninn are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-05-28 - Extract transport to gungnir

Structural refactor. **No wire-protocol change** — the HMAC envelope
sent to wdgwars.pl is byte-identical to v1.11.1 (verified by
`gungnir/tests/test_muninn_parity.py`). Existing cron jobs continue to
work without any change to their invocation. Existing API keys at
`~/.config/muninn/api.key` and `%APPDATA%/muninn/api.key` are read in
place — config path is unchanged.

### Changed

- **Transport, HMAC envelope, key management, cooldown persistence,
  silent-drop detection, and User-Agent are now provided by
  [gungnir](https://github.com/HiroAlleyCat/gungnir) ≥ 0.1.1.** Muninn
  becomes a thin ADS-B-specific layer (input parsing + record
  normalization) over the shared library. Same library will back
  Heimdall and wigle-to-wdgwars in subsequent releases so bug fixes
  land once.

### Improved (free wins from gungnir, no muninn-side code)

- **Retries on 5xx and network errors** with exponential backoff
  (3 attempts, 2s/4s gaps). v1.x failed the upload on the first
  transient hiccup.
- **429 rate-limit response now stops the whole batch** and persists
  a cooldown deadline. The next cron tick respects the deadline so
  multiple cron jobs don't drown the server. v1.x kept POSTing more
  chunks after a 429.
- **Silent-drop pattern now exits non-zero.** v1.11.1 added the
  HTTP-200-ok-true-zero-counters detector but only logged a warning;
  v2.0 also returns exit code 1 so cron jobs surface the failure.
- **Inter-chunk cooldown of 1s between chunks.** v1.x sent chunks
  back-to-back, which can drown a server under a 30-chunk batch.
  Configurable via the gungnir Client; this default is safe for the
  wdgwars.pl prod server.
- **User-Agent now includes the repo URL.** Server admins can trace
  Muninn traffic to its source via the standard
  `muninn/2.0.0 (+https://github.com/HiroAlleyCat/adsb-to-wdgwars)`
  bot-UA convention. v1.x was bare `muninn/1.11.1`.
- **API key redaction works for short keys too.** v1.x scrub()
  required `len(key) > 8` before redacting, which leaked short test
  keys into log output. gungnir redacts on any non-empty match.
- **Library-level logging via the standard `logging` module.**
  Muninn still defaults to its v1.x stderr-line-per-event behavior;
  users who set up their own root logger override it.

### Removed

- The local copies of `_SSL_CTX`, `DEFAULT_API_URL`, `ME_API_URL`,
  `_config_dir`, `_key_path`, the HMAC envelope build, and the
  `urllib`-based upload loop. All moved to gungnir. The Muninn-side
  function signatures (`upload`, `load_key`, `save_key`,
  `check_whoami`, `_scrub`) are preserved as thin shims so any
  external script that imported them from muninn continues to work.

### Migration

- Install gungnir before upgrading:
  `pip install -e ../gungnir` (until gungnir is on PyPI).
- No config-file changes needed.
- No cron-stanza changes needed.

## [1.11.1] - 2026-05-28 - Stay on HMAC `/api/upload/`, fix leading-zero ICAOs

### Fixed
- **`_norm_record` no longer strips leading zeros from the ICAO.** The
  previous `icao.upper().lstrip("0")` turned valid Mode-S addresses like
  `0DB36A` into `DB36A`, which fails the server's `^[0-9A-F]{6}$`
  validation and silently dropped on import. ICAOs are now passed through
  uppercase as-is.
- Default `--batch-size` lowered from 1000 to 500, matching the server's
  preferred 100-500 per request.

### Reverted
- v1.11.0 switched the upload endpoint to `/api/upload-csv` (multipart)
  based on the assumption that the HMAC `/api/upload/` path had been
  deprecated. That was wrong: a server-side v4 audit patch had
  temporarily over-narrowed the aircraft Type allowlist, silently
  skipping every record that carried a DO-260B emitter category. The
  upstream maintainer reverted the filter and confirmed the HMAC
  envelope at `/api/upload/` is the canonical aircraft route. Muninn
  swaps back to it.

### Verified
- Live RTL-SDR end-to-end after the server fix: 10 aircraft sent, 4
  imported, 6 already on file, 4 new badges. HMAC envelope + ICAO fix
  both confirmed in one run.

## [1.10.0] — 2026-05-24 — Retract v1.9.0 Zigbee support

### Removed
- **All v1.9.0 Zigbee / 802.15.4 capture support is withdrawn.** The
  `parse_zigbee_pcap`, `parse_zigbee_csv`, `parse_zigbee_ndjson`,
  `_aggregate_zigbee_pans`, `--zigbee` / `--lat` / `--lon` / `--channel`
  CLI flags, and web-front-end Zigbee surfacing are all removed.
  `tests/test_zigbee.py` deleted.

### Why
v1.9.0 was built on a misread. The WDGoWars `meshcore_nodes` upload
channel is named for **Meshcore (LoRa, sub-GHz)**, not Zigbee
(802.15.4, 2.4 GHz). The fact that v1.9.0's Zigbee uploads were credited
on the mesh leaderboard reflected a server-side validation gap (records
were routed by container key, not by per-record `type`), not a
legitimately broad mesh channel. Shipping that gap to the broader
community at scale would have polluted the mesh leaderboard with
wrong-protocol data.

The validation gap and four related findings were responsibly disclosed
to LOCOSP (WDGoWars admin) on 2026-05-24 with a scrub list for the
local records that were uploaded during testing. The Zigbee feature is
withdrawn ahead of any community pickup.

### Kept
- The six ADS-B regression tests added during v1.9.0 development are
  preserved in `tests/test_adsb_regression.py` (extracted from the
  deleted `tests/test_zigbee.py`). They were always ADS-B coverage and
  remain useful.
- The GitHub Actions `tests.yml` workflow stays. CI now runs the ADS-B
  regression suite on Python 3.10 / 3.11 / 3.12.
- `tests/__init__.py` and the package structure remain so future
  test files drop in cleanly.

### What replaces it
A separate sibling tool (working name **Heimdall**) will handle genuine
**Meshcore LoRa** captures via MeshMapper CSV → `meshcore_nodes` upload,
matching the schema (`timestamp,node_id,type,name,lat,lon,rssi,snr`)
that the WDGoWars `lora_manager.py` ingest path actually expects.
Muninn stays scoped to aircraft.

### Migration
If you installed v1.9.0 and were using Zigbee features:

1. Downgrade or upgrade to v1.10.0; both work, v1.10.0 is the supported
   line going forward.
2. Stop uploading 802.15.4 captures to WDGoWars under `meshcore_nodes`.
   The leaderboard credit was never legitimately for Zigbee.
3. If you have local Zigbee captures you want to publish, hold them
   until/unless WDGoWars announces a dedicated Zigbee channel.

## [1.8.1] — 2026-05-15

### Changed
- **GDL-90 parser promoted from experimental to validated.** Authoritative
  test vector from `NathanVaughn/gdl90py` (`tests/messages/test_traffic_report.py`)
  decodes byte-for-byte to the expected ICAO `AB4549`, callsign `N825V`,
  lat 44.907067 (target 44.90708), lon -122.994862 (target -122.99488),
  speed 123 kt, track 45°. Shipped as `examples/gdl90_real.gdl90` for
  future regressions.
- **Beast parser cross-validated against pyModeS's real-world dataset.**
  The 2000-frame `tests/data/sample_data_adsb.csv` from `junzis/pyModeS`
  decodes to the same 1-aircraft count in both AVR and Beast format,
  proving the binary-container parser produces output identical to
  parse_avr on the same underlying Mode-S frames.

## [1.8.0] — 2026-05-15

### Added
- **Stratux JSON** — the `/traffic` endpoint output from Stratux DIY
  cockpit receivers. Top-level dict keyed by ICAO hex, values are
  aircraft dicts using `Icao_addr`/`Tail`/`Reg`/`Lat`/`Lng`/`Alt`/
  `Speed`/`Track`/`Position_valid` field names. Detected by the
  Stratux-specific `Icao_addr` / `Position_valid` signature on the
  first dict value. Records with `Position_valid: false` are skipped.
- **Mode-S Beast binary** (dump1090's native wire protocol on TCP
  30005). Each message is `0x1A <type> <6B ts> <1B sig> <data>` with
  `0x1A 0x1A` byte-stuffing. Detection: 0x1A start + type byte 0x31 /
  0x32 / 0x33. Mode-S short and long messages are extracted as hex
  and fed into pyModeS PipeDecoder (same path as parse_avr), so all
  the CPR-pairing / callsign-merging / altitude-tracking logic is
  shared. CRC validation is currently skipped — accept anything that
  decodes cleanly.

### Fixed
- The new Beast parser revealed a class of latent bug: pyModeS
  PipeDecoder emits position records under `latitude`/`longitude`/
  `track` keys, not `lat`/`lon`/`heading`. parse_avr was already
  using the correct keys; the first draft of parse_beast wasn't.
  Now both go through the same key set.

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

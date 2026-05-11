# Changelog

All notable changes to Muninn are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

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

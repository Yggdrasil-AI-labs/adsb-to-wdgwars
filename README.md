<p align="center">
  <img src="assets/banner.png" alt="Muninn — Odin's memory-raven for the WDGoWars sky" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/HiroAlleyCat/adsb-to-wdgwars/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/HiroAlleyCat/adsb-to-wdgwars?color=b08850&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-b08850.svg"></a>
  <a href="https://github.com/HiroAlleyCat/adsb-to-wdgwars/blob/main/SECURITY.md"><img alt="Security" src="https://img.shields.io/badge/security-threat%20model-b08850.svg"></a>
</p>

# Muninn

Convert ADS-B capture files (HackRF H4M, dump1090 / readsb, tar1090, VirtualRadarServer, Stratux, Mode-S Beast, RTL-SDR, RTL1090, PortaPack Mayhem, GDL-90 cockpit receivers) to WDGoWars-compatible JSON and optionally upload them. Auto-detects 13 input dialects and decompresses gzipped chunks transparently.

> **Linked by WDGoWars as the recommended advanced converter.** The
> WDGoWars portal includes a native importer for the common JSON
> dialects (dump1090 / readsb / tar1090 / Stratux / VRS / Sleipnir)
> and the SBS-1 / PortaPack Mayhem text formats — drag-and-drop a
> file and it imports. For everything else — AVR raw Mode-S, Mode-S
> Beast binary, GDL-90 binary, NDJSON, gzipped tar1090 chunks,
> BaseStation `.sqb`, and the HMAC-signed `/api/upload/` route — the
> portal links Muninn from `/help`, `/changelog`, and the
> upload-profile UI as the recommended path. If your receiver
> already speaks a dialect the portal accepts, you can skip Muninn;
> if not, you're in the right place.

> **Note on v1.9.0 (Zigbee).** v1.9.0 added 802.15.4 / Zigbee capture
> support and was withdrawn the next day in v1.10.0. The feature
> rested on a misread of the WDGoWars mesh channel (which is for
> **Meshcore / LoRa**, not Zigbee). See the
> [v1.10.0 CHANGELOG entry](CHANGELOG.md#1100--2026-05-24--retract-v190-zigbee-support)
> for the full story. If you're on v1.9.0, please upgrade to v1.10.0.

> **Note on v1.11.0 (endpoint switch).** v1.11.0 briefly moved the
> upload from `/api/upload/` (HMAC envelope) to `/api/upload-csv`
> (multipart) after misreading a server-side regression as a path
> deprecation. The HMAC envelope at `/api/upload/` is the canonical
> aircraft route and is not going away. v1.11.1 reverts that and
> also fixes a latent ICAO-leading-zero bug that was silently
> dropping valid Mode-S addresses on import. If you're on v1.11.0,
> please upgrade to v1.11.1.

> **Note on the 2026-05-30 star reset.** Muninn had 10 stargazers
> and 1 watcher before a maintainer mistake (flipping the repo
> visibility private→public to try to bust a stale GitHub
> contributor cache) reset both counts to zero. GitHub Support
> confirmed this is documented, intentional behavior on any
> visibility change and the counts can't be restored on their end.
> The repo itself, releases, history, and code are otherwise
> unaffected. If you previously starred or watched Muninn and
> wouldn't mind doing so again, it would be appreciated — no
> hard feelings if you'd rather not.

**Scope:** Muninn is for **data your own receiver captured**. Aggregator-API
formats (OpenSky, FlightAware, ADS-B Exchange) are intentionally not
supported — WDGoWars is a wardriving game, importing thousands of other
people's aircraft would defeat the contribution model. If your data
came from a live SDR / Stratux / PortaPack you set up, you're in the
right place.

---

## Pick your path

Muninn ships in **two flavours** that share the same parsing core. Use whichever fits your setup — they don't depend on each other.

| | **Web (browser)** | **CLI (terminal)** |
|---|---|---|
| **For** | One-off uploads, admins, anyone without Python | Headless boxes, RTL-SDR rigs, cron, scripted feeds |
| **Install** | None — open a URL | Clone repo, run `python3 muninn.py` |
| **Where parsing happens** | In your browser (Pyodide / WASM) | Locally with stdlib Python |
| **Internet required** | Yes (initial page load, ~10 MB cached) | No (only for `--update` and uploads) |
| **Runs without a display** | No | **Yes** — headless-safe |

If you're running on a Raspberry Pi, a server, or anything without a desktop, **use the CLI** — the rest of this README is for you. Scroll down to [CLI install](#cli-install).

If you just want to drop a file and have it uploaded, **use the web version** at [hiroalleycat.github.io/adsb-to-wdgwars](https://hiroalleycat.github.io/adsb-to-wdgwars) (deploys from the `web/` directory in this repo).

---

## Got components but no decoded data yet?

Muninn does not talk to your RTL-SDR directly. It consumes the output of a decoder that does. If you just unboxed a dongle and antenna, install a decoder first:

| OS | Recommended decoder | How to install |
|---|---|---|
| **Raspberry Pi / Linux** | dump1090-fa | Follow FlightAware's installer at [flightaware.com/adsb/piaware/install](https://flightaware.com/adsb/piaware/install). You do not have to share with FlightAware; local decoding works either way. |
| **Windows** | dump1090-win | Install [Zadig](https://zadig.akeo.ie), replace the dongle driver with WinUSB, then grab [dump1090-win](https://github.com/MalcolmRobb/dump1090) and run `dump1090.exe --net --write-json out`. |
| **macOS** | dump1090 | `brew install dump1090 && dump1090 --net --write-json /tmp/dump1090` |

Confirm it is working. You should see aircraft counts climbing:

```bash
# Pi / Linux (dump1090-fa default path)
curl -s http://localhost:8080/data/aircraft.json | jq '.aircraft | length'
```

Not sure where your decoder writes `aircraft.json`? Run `sudo find /run /var -name aircraft.json 2>/dev/null` to locate it. Common spots: `/run/dump1090-fa/aircraft.json` (FlightAware), `/run/readsb/aircraft.json` (readsb), `/run/adsbfi-feed/aircraft.json` (ADS-B Fi feeder).

Then point Muninn at the decoder's output:

```bash
# One-shot: convert + upload the current snapshot
python3 muninn.py /run/dump1090-fa/aircraft.json --upload

# Continuous: watch the decoder's output folder
python3 muninn.py --watch /run/dump1090-fa --watch-glob 'aircraft.json'
```

### Antenna reality check

The small whip that ships in most RTL-SDR kits is a general-purpose scanner antenna and will see almost nothing at 1090 MHz. A proper ADS-B antenna (quarter-wave around 6.8 cm, a FlightAware stub, or a Stratux / RadarBox dipole) will jump your aircraft count by 5 to 10 times. Indoor near a window works for testing; outdoor or rooftop is ideal.

If `rtl_test` finds the dongle but no aircraft show up after 5 minutes, the antenna is almost always the cause, not the software.

---

## CLI install

You need **Python 3.10 or newer** and a working `pip`. Git is **not**
required — Muninn's installer fetches its one dependency
([gungnir](https://github.com/HiroAlleyCat/gungnir), the shared HMAC
transport) over plain HTTPS.

### Option A — ZIP download (no git needed)

1. Grab the ZIP from [the GitHub repo](https://github.com/HiroAlleyCat/adsb-to-wdgwars) (Code → Download ZIP) and unzip it.
2. Double-click **`setup.bat`** (Windows) or run **`./setup.sh`** (Mac/Linux). It installs dependencies and prompts for your API key.
3. After that, double-click **`run.bat`** / **`run.sh`** to process anything in `input/`.

### Option B — clone with git

```bash
git clone https://github.com/HiroAlleyCat/adsb-to-wdgwars
cd adsb-to-wdgwars
python3 -m pip install -r requirements.txt
python3 muninn.py
```

On first run, Muninn asks **where** you want your input/output folders:

```
 Where would you like your input/output folders?

   1) Right here:  C:\Users\you\adsb-to-wdgwars\input
                   C:\Users\you\adsb-to-wdgwars\output
   2) On Desktop:  C:\Users\you\Desktop\Muninn  (with input/ and output/ inside)

 Choose [1/2] (default: 1):
```

Pick whichever you prefer — it remembers your choice. On Windows, picking option 2 also offers to create a desktop shortcut with the raven icon. Double-click the shortcut and Muninn runs.

### The day-to-day workflow

1. Drop your `ADSB.TXT` (or any supported capture file) into the `input` folder.
2. Run `python3 muninn.py` (or double-click the desktop shortcut if you have one).
3. Grab the converted `.wdgwars.json` from the `output` folder.

Multiple files in `input/` get converted in one pass.

### Or pass a path directly

If you prefer to skip the folder workflow:

```bash
python3 muninn.py /path/to/your-capture.txt
```

Output goes next to the input file (`your-capture.wdgwars.json`).

---

## Uploading to WDGoWars

Two options:

**Option A — drag-and-drop the JSON into the website.** The `.wdgwars.json` Muninn writes is in the dump1090-fa format that the WDGoWars web upload form accepts. Just drag it from your output folder into the upload page.

**Option B — let Muninn upload for you.** Add `--upload`:

```bash
python3 muninn.py --upload
```

First time, Muninn asks for your WDGoWars API key (y/n prompt — local conversion works fine without one). The key is saved locally in mode `0600`, scrubbed from all error output, and sent over TLS 1.2+ with an HMAC-SHA256-signed envelope to `https://wdgwars.pl/endpoint/upload/` (a server-side alias of `/api/upload/` that bypasses Cloudflare's per-IP L7 rate-limit — see the v2.0.4 changelog). Force `/api/upload/` with `--api-url` if needed.

Grab your API key from your WDGoWars profile page.

---

## Supported input formats

Auto-detected from the first line of the file:

| Format | Looks like | Source |
|---|---|---|
| PortaPack Mayhem | `8DA39EF2... ICAO:A39EF2 EJM333 Alt:40000 Lat:... Lon:...` | HackRF PortaPack H4M |
| AVR raw Mode-S | `*8D4840D6...;` per line | dump1090 `--raw`, readsb port 30002 (needs `pip install pyModeS`) |
| SBS-1 / BaseStation | `MSG,3,...` CSV | dump1090 `--net`, readsb port 30003 |
| dump1090 JSON | `{"aircraft": [...]}` | `/run/readsb/aircraft.json` |
| Generic CSV | `icao,lat,lon,alt,...` | anything with a header row |
| BaseStation SQLite (`.sqb`) | SQLite file with `Aircraft` + `Flights` tables | RTL1090's SQLite logging plugin, PlanePlotter, Kinetic BaseStation |

Notes on `.sqb`:
- BaseStation stores one row per **flight** (not per position report), so muninn emits up to two records per flight: one at `StartTime` / `First*` and one at `EndTime` / `Last*`, whichever sides have valid coordinates.
- Timestamps in BaseStation are naive strings like `"2024-08-15 14:32:11.123"` with no timezone information. Muninn defaults to treating them as **UTC** (matching the rest of muninn's output). If your BaseStation install logged in local time, pass `--sqb-tz America/New_York` (or any IANA zone) to convert on the fly. On Windows, the IANA zone database is provided by the `tzdata` PyPI package — install it with `pip install tzdata` if `--sqb-tz` reports an unknown zone.
- BaseStation does not store `Last(GroundSpeed|Track)`, so the end-of-flight record surfaces `speed_kt=0` / `heading=0` rather than carrying forward the values from `First*`.
- If `Flights` is absent or empty (some installs only populate `Aircraft`), muninn exits nonzero with a clear message rather than write an empty JSON.

---

## All command-line flags

```
--out PATH         write JSON to one specific output path
--out-dir DIR      write all output JSON into this folder (created if missing)
--stdout           print JSON to stdout instead of writing a file
--upload           POST to WDGoWars after converting (HMAC-signed envelope)
--watch DIR        watch a folder; auto-convert (and upload) new files
--watch-interval N seconds between watch polls (default: 30)
--watch-glob G     glob for the watch dir (default: *.txt; use * for all)
--format FMT       force input format (auto|avr|sbs1|json|csv|mayhem|sqb)
--csv-format COLS  column-order hint for generic CSV inputs
--sqb-tz ZONE      IANA timezone for interpreting BaseStation .sqb
                   timestamps (default: treat as UTC)
--setup            interactive API-key wizard
--save-key KEY     non-interactive: save a given API key
--whoami           validate your stored API key and show account stats
--no-save          with --upload, skip writing the local JSON file
--dry-run          with --upload, build the request but don't send
--key KEY          one-shot override of the stored API key
--api-url URL      override the upload endpoint
--batch-size N     aircraft per upload chunk (default: 500)
--version          print Muninn's version
--update           pull the latest release (git pull if you cloned)
-q, --quiet        suppress informational output (banners, format/decoded
                   notices, range + dump1090 warnings). Errors still print.
--no-version-check skip the daily GitHub release check entirely (use for
                   offline / privacy-conscious setups).
--open             after writing JSON, pop open the output folder in your
                   file manager (Explorer / Finder / xdg-open).
--config           print the current Muninn config (folders, key, version)
                   and exit.
--reset            forget the saved input/output folder choice (re-prompt
                   next run). Does not touch your API key.
```

---

## Range and feed sanity checks

Muninn runs two automatic checks every time it processes a file.

### dump1090 network input check

On startup, Muninn probes  (Beast input) and  (raw input). If either port is open it prints a warning before processing anything:



The most common cause of implausibly large reception ranges (aircraft 1000+ km apart) is dump1090 running with  while a piaware or FlightAware feeder is also active, silently injecting remote aircraft into the local stream. No data is sent or received during the probe — it is a single connect attempt per port.

### Aircraft range check

After decoding, Muninn checks whether any aircraft positions are beyond 500 km from the median position of the capture — roughly the 1090 MHz radio horizon at cruise altitude. Outliers are flagged with their ICAO, callsign, and distance:



No records are removed — these are warnings only. If you are deliberately aggregating data from multiple locations, you can ignore them.

## Updating

Double-click **`update.bat`** (Windows) or run **`./update.sh`** (Mac/Linux) from the Muninn folder. The script:

1. Pulls the latest `requirements.txt` from GitHub so any new dependencies are visible to pip.
2. Runs `pip install --upgrade -r requirements.txt`.
3. Updates `muninn.py` itself (via `git pull` if you cloned the repo, otherwise via a direct HTTPS download from GitHub).

This order matters across versions that add or bump a dependency — pip has to know about the new dep before muninn.py tries to import it.

If you prefer the CLI:

```bash
python3 muninn.py --update
```

`muninn.py --update` also refreshes `requirements.txt` and re-runs pip itself, so direct CLI updates self-heal too — but only if `muninn.py` can already load (i.e. its current deps are installed). The wrapper script is the more robust path because it bootstraps deps before importing anything.

Muninn also does a once-a-day background check against the GitHub releases API and prints a one-liner on launch if a newer version is out. No telemetry, single HEAD request, cached locally for 24h. See [CHANGELOG.md](CHANGELOG.md) for per-release notes.

---

## Re-running first-time setup

To change where the input/output folders live, or re-run the API-key prompt:

```bash
# folders
del "%APPDATA%\muninn\folders.json"        (Windows)
rm  ~/.config/muninn/folders.json          (Mac/Linux)

# API key (just re-save it)
python3 muninn.py --setup
```

---

## Security

- API key stored at `%APPDATA%\muninn\api.key` (Windows) or `~/.config/muninn/api.key` (Unix, mode `0600`).
- API key is **never** required for local conversion — only for `--upload`.
- HMAC-SHA256-signed envelope, explicit TLS 1.2+ context, system trust store.
- Key is scrubbed from all error output via `_scrub()`.
- `--save-key` refuses to write through a symlink.
- No telemetry. Nothing leaves your machine unless `--upload` is set.

Full threat model: [SECURITY.md](SECURITY.md). Found a vulnerability? Open a private security advisory via the repo's [Security tab](https://github.com/HiroAlleyCat/adsb-to-wdgwars/security/advisories).

---

## License

MIT — see [LICENSE](LICENSE).

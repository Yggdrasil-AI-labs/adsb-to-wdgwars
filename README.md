<p align="center">
  <img src="assets/banner.png" alt="Muninn — Odin's memory-raven for the WDGoWars sky" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/HiroAlleyCat/adsb-to-wdgwars/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/HiroAlleyCat/adsb-to-wdgwars?color=b08850&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-b08850.svg"></a>
  <a href="https://github.com/HiroAlleyCat/adsb-to-wdgwars/blob/main/SECURITY.md"><img alt="Security" src="https://img.shields.io/badge/security-threat%20model-b08850.svg"></a>
</p>

# Muninn

Convert ADS-B capture files (HackRF H4M, dump1090 / readsb, tar1090, VirtualRadarServer, Stratux, Mode-S Beast, RTL-SDR, PortaPack Mayhem, GDL-90 cockpit receivers) to WDGoWars-compatible JSON and optionally upload them. Auto-detects 12 input dialects and decompresses gzipped chunks transparently.

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

## CLI install

```bash
git clone https://github.com/HiroAlleyCat/adsb-to-wdgwars
cd adsb-to-wdgwars
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

First time, Muninn asks for your WDGoWars API key (y/n prompt — local conversion works fine without one). The key is saved locally in mode `0600`, scrubbed from all error output, and sent over TLS 1.2+ with an HMAC-SHA256-signed envelope to `https://wdgwars.pl/api/upload/`.

Grab your API key from your WDGoWars profile page.

---

## Supported input formats

Auto-detected from the first line of the file:

| Format | Looks like | Source |
|---|---|---|
| **Zigbee pcap (v1.9.0)** | libpcap/pcapng, linktype 195/230 | Wireshark, KillerBee, Sonoff sniffer, CC2531 |
| **Zigbee CSV (v1.9.0)** | header `pan_id,channel,lat,lon,rssi,first_seen` | Sleipnir wardrive nodes, Marauder + GPS |
| PortaPack Mayhem | `8DA39EF2... ICAO:A39EF2 EJM333 Alt:40000 Lat:... Lon:...` | HackRF PortaPack H4M |
| AVR raw Mode-S | `*8D4840D6...;` per line | dump1090 `--raw`, readsb port 30002 (needs `pip install pyModeS`) |
| SBS-1 / BaseStation | `MSG,3,...` CSV | dump1090 `--net`, readsb port 30003 |
| dump1090 JSON | `{"aircraft": [...]}` | `/run/readsb/aircraft.json` |
| Generic CSV | `icao,lat,lon,alt,...` | anything with a header row |

---


## Zigbee / 802.15.4 (mesh channel) — v1.9.0+

Muninn also feeds the WDGoWars **`mesh`** leaderboard channel, which the
portal staff confirmed covers 802.15.4 / Zigbee networks. The CLI ships
this in v1.9.0; the web UI is web-side parity arrives in v1.9.1.

### Three input formats

| Format | Looks like | Source |
|---|---|---|
| `zigbee-pcap` | libpcap or pcapng with linktype 195 / 230 | Wireshark, tshark, KillerBee `zbdump`, Sonoff USB sniffer, CC2531 + znp |
| `zigbee-csv` | header row `pan_id,channel,lat,lon,rssi,first_seen` | Sleipnir wardrive nodes, Marauder + GPS exports, any hand-rolled CSV |
| `zigbee-ndjson` | one JSON record per line with a `pan_id` field | streaming pipelines |

Auto-detected by pcap linktype or CSV header. No flag needed in the common case.

### Stationary captures need static GPS

`.pcap` files have no GPS metadata. For a stationary home sniffer, supply
`--lat` and `--lon` once and Muninn stamps every PAN with that position:

```bash
python3 muninn.py home-zigbee.pcap --upload --lat 41.4712 --lon -81.7887
```

For CSV and NDJSON inputs, GPS is per-row so no `--lat/--lon` flag is needed.

For a roving sniffer, GPX-sidecar pairing is planned for v1.9.1. Until then,
roving captures should be exported as CSV with per-row GPS.

### What lands on the leaderboard

Frames are aggregated to **one record per unique PAN ID** (dominant channel,
mean lat/lon, mean RSSI, earliest first_seen). The WDGoWars server
dedupes mesh credit on PAN ID alone, so finer grain is silently no-op'd.
Broadcast PAN `0xFFFF` is filtered.

A successful upload returns:

```json
{"ok":true,"meshcore_imported":N,"meshcore_already_seen":N,"new_badges":[...]}
```

The first push unlocks the `mesh_first` badge.

### Examples

```bash
# A KillerBee dump captured at home; static GPS, autodetect linktype
python3 muninn.py zb-home.pcap --lat 41.4712 --lon -81.7887 --upload

# A Sleipnir CSV with GPS already in each row; force Zigbee mode (override autodetect)
python3 muninn.py sleipnir-zigbee.csv --zigbee --upload

# Preview without uploading
python3 muninn.py capture.pcap --lat 41.47 --lon -81.79 --stdout | jq '.meshcore_nodes[:3]'

# Dry-run to inspect the envelope shape
python3 muninn.py capture.pcap --lat 41.47 --lon -81.79 --upload --dry-run
```

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
--format FMT       force input format (auto|avr|sbs1|json|csv|mayhem)
--csv-format COLS  column-order hint for generic CSV inputs
--setup            interactive API-key wizard
--save-key KEY     non-interactive: save a given API key
--whoami           validate your stored API key and show account stats
--no-save          with --upload, skip writing the local JSON file
--dry-run          with --upload, build the request but don't send
--key KEY          one-shot override of the stored API key
--api-url URL      override the upload endpoint
--batch-size N     aircraft per upload chunk (default: 1000)
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

```bash
python3 muninn.py --update
```

That runs `git pull` in place if you cloned the repo. If you downloaded the ZIP instead, grab the newest one from the [Releases page](https://github.com/HiroAlleyCat/adsb-to-wdgwars/releases).

Muninn also does a once-a-day background check against the GitHub releases API and prints a one-liner if a newer version is out. No telemetry — single HEAD request, cached locally for 24h. See [CHANGELOG.md](CHANGELOG.md) for per-release notes.

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

<p align="center">
  <img src="assets/banner.png" alt="Muninn. Odin's memory-raven for the WDGWars sky" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars/actions/workflows/ci-quality-gates.yml"><img alt="CI" src="https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars/actions/workflows/ci-quality-gates.yml/badge.svg"></a>
  <a href="https://sonarcloud.io/dashboard?id=Yggdrasil-AI-labs_adsb-to-wdgwars"><img alt="Quality gate" src="https://sonarcloud.io/api/project_badges/measure?project=Yggdrasil-AI-labs_adsb-to-wdgwars&metric=alert_status"></a>
  <a href="https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/Yggdrasil-AI-labs/adsb-to-wdgwars?color=b08850&label=release"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-b08850.svg"></a>
  <a href="https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars/blob/main/SECURITY.md"><img alt="Security" src="https://img.shields.io/badge/security-threat%20model-b08850.svg"></a>
</p>

# Muninn

Convert ADS-B capture files (HackRF H4M, dump1090 / readsb, tar1090, VirtualRadarServer, Stratux, Mode-S Beast, RTL-SDR, RTL1090, PortaPack Mayhem, GDL-90 cockpit receivers) to WDGWars-compatible JSON and optionally upload them. Auto-detects 13 input dialects and decompresses gzipped chunks transparently.

## Family

Sibling repos in the WDGWars feeder family:

- [Heimdall](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars). MeshCore LoRa feeder
- [wigle-to-wdgwars](https://github.com/Yggdrasil-AI-labs/wigle-to-wdgwars). WiGLE Wi-Fi/BLE feeder
- [gungnir](https://github.com/Yggdrasil-AI-labs/gungnir), shared HMAC transport library
- [wdgwars-api-tester](https://github.com/Yggdrasil-AI-labs/wdgwars-api-tester). API surface probe

> **Linked by WDGWars as the recommended advanced converter.** The
> WDGWars portal includes a native importer for the common JSON
> dialects (dump1090 / readsb / tar1090 / Stratux / VRS / Sleipnir)
> and the SBS-1 / PortaPack Mayhem text formats, drag-and-drop a
> file and it imports. For everything else. AVR raw Mode-S, Mode-S
> Beast binary, GDL-90 binary, NDJSON, gzipped tar1090 chunks,
> BaseStation `.sqb`, and the HMAC-signed `/api/upload/` route, the
> portal links Muninn from `/help`, `/changelog`, and the
> upload-profile UI as the recommended path. If your receiver
> already speaks a dialect the portal accepts, you can skip Muninn;
> if not, you're in the right place.

**Scope:** Muninn is for **data your own receiver captured**. Aggregator-API
formats (OpenSky, FlightAware, ADS-B Exchange) are intentionally not
supported. WDGWars is a wardriving game, importing thousands of other
people's aircraft would defeat the contribution model. If your data
came from a live SDR / Stratux / PortaPack you set up, you're in the
right place.

---

## Pick your path

Muninn ships in **two flavours** that share the same parsing core. Use whichever fits your setup, they don't depend on each other.

| | **Web (browser)** | **CLI (terminal)** |
|---|---|---|
| **For** | One-off uploads, admins, anyone without Python | Headless boxes, RTL-SDR rigs, cron, scripted feeds |
| **Install** | None. Open a URL | Clone repo, run `./run.sh` |
| **Where parsing happens** | In your browser (Pyodide / WASM) | Locally with stdlib Python |
| **Internet required** | Yes (initial page load, ~10 MB cached) | No (only for `--update` and uploads) |
| **Runs without a display** | No | **Yes**: headless-safe |

If you're running on a Raspberry Pi, a server, or anything without a desktop, **use the CLI**: the rest of this README is for you. Scroll down to [CLI install](#cli-install).

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
./run.sh /run/dump1090-fa/aircraft.json --upload

# Continuous: watch the decoder's output folder
./run.sh --watch /run/dump1090-fa --watch-glob 'aircraft.json'
```

`/run/dump1090-fa` and `/run/readsb` are root-owned runtime directories,
your account can usually read `aircraft.json` there but not write back
into that folder. The one-shot command above still uploads fine either way
(a local write failure warns but never blocks `--upload`), but to avoid the
warning and keep a local copy, add `--out-dir` pointing at a folder you own,
e.g. `--out-dir ~/muninn-output`.

### Antenna reality check

The small whip that ships in most RTL-SDR kits is a general-purpose scanner antenna and will see almost nothing at 1090 MHz. A proper ADS-B antenna (quarter-wave around 6.8 cm, a FlightAware stub, or a Stratux / RadarBox dipole) will jump your aircraft count by 5 to 10 times. Indoor near a window works for testing; outdoor or rooftop is ideal.

If `rtl_test` finds the dongle but no aircraft show up after 5 minutes, the antenna is almost always the cause, not the software.

---

## CLI install

You need **Python 3.10 or newer** and a working `pip`. Git is **not**
required. Muninn's installer fetches its one dependency
([gungnir](https://github.com/Yggdrasil-AI-labs/gungnir), the shared HMAC
transport) over plain HTTPS.

### Option A - ZIP download (no git needed)

1. Grab the ZIP from [the GitHub repo](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars) (Code → Download ZIP) and unzip it.
2. Double-click **`setup.bat`** (Windows) or run **`./setup.sh`** (Mac/Linux). It installs dependencies and prompts for your API key.
3. After that, double-click **`run.bat`** / **`run.sh`** to process anything in `input/`.

### Option B - clone with git

```bash
git clone https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars
cd adsb-to-wdgwars
python3 -m venv .venv          # required on Bookworm / Homebrew (PEP 668)
.venv/bin/pip install -r requirements.txt
.venv/bin/python muninn.py
```

(`setup.sh` does all of the above for you. This block is just the manual equivalent.)

On first run, Muninn asks **where** you want your input/output folders:

```
 Where would you like your input/output folders?

   1) Right here:  C:\Users\you\adsb-to-wdgwars\input
                   C:\Users\you\adsb-to-wdgwars\output
   2) On Desktop:  C:\Users\you\Desktop\Muninn  (with input/ and output/ inside)

 Choose [1/2] (default: 1):
```

Pick whichever you prefer, it remembers your choice. On Windows, picking option 2 also offers to create a desktop shortcut with the raven icon. Double-click the shortcut and Muninn runs.

### Confirm Muninn understands your decoder's output first

If you're wiring up a new capture source (a JSON grabber, a custom decoder, an NDJSON pipeline), check the parser sees what you expect before turning on `--upload`:

```bash
./run.sh /path/to/your-capture.ndjson.gz --preview
```

Prints the first 6 normalised records as JSON-lines on stdout, then exits. No file write, no upload. If the records look right (correct ICAOs, sensible lat/lon, the callsign field your decoder uses), you can safely wire `--upload` or `--schedule` after.

### The day-to-day workflow

1. Drop your `ADSB.TXT` (or any supported capture file) into the `input` folder.
2. Run `./run.sh` (or double-click the desktop shortcut if you have one).
3. Grab the converted `.wdgwars.json` from the `output` folder.

Multiple files in `input/` get converted in one pass.

### Or pass a path directly

If you prefer to skip the folder workflow:

```bash
./run.sh /path/to/your-capture.txt
```

Output resolves in this order: `--out` (exact path) > `--out-dir` (a
folder) > the output folder you picked at first-run setup, if any > next
to the input file (`your-capture.wdgwars.json`), as a last resort. That
last-resort default matters if you point Muninn straight at a decoder's own
runtime directory (e.g. `/run/readsb`). Those are usually root-owned and
not writable by your account, so pick a folder you can write to with
`--out-dir` (see the note under "One-shot" below).

---

## Uploading to WDGWars

Two options:

**Option A, drag-and-drop the JSON into the website.** The `.wdgwars.json` Muninn writes is in the dump1090-fa format that the WDGWars web upload form accepts. Just drag it from your output folder into the upload page.

**Option B. Let Muninn upload for you.** Add `--upload`:

```bash
./run.sh --upload
```

First time, Muninn asks for your WDGWars API key (y/n prompt, local conversion works fine without one). The key is saved locally in mode `0600`, scrubbed from all error output, and sent over TLS 1.2+ with an HMAC-SHA256-signed envelope to `https://wdgwars.pl/endpoint/upload/` (a server-side alias of `/api/upload/` that bypasses Cloudflare's per-IP L7 rate-limit, see the v2.0.4 changelog). Force `/api/upload/` with `--api-url` if needed.

Grab your API key from your WDGWars profile page.

Generate a key just for Muninn and give it a name, rather than reusing one you handed another tool. Keys are switched off one at a time from the same profile page, so revoking this one later costs you nothing else.

By configuring a key you're authorising Muninn to upload the captures you give it to WDGWars under your own account. It won't ask again per upload. Use `--preview` or `--dry-run` to see exactly what would be sent before you commit to it.

---

## Running on a schedule

`muninn.py --setup` offers to install a scheduled task at the end. You can also configure scheduling at any time:

```bash
./run.sh --schedule          # interactive
./run.sh --unschedule        # remove
```

Two modes:

- **Watch**: long-running daemon that uploads new captures as soon as they appear. Best for decoders that write a new file per capture session (tar1090 chunks, NDJSON sessions, MeshCore exports).
- **Periodic**: runs every N minutes (1, 60) against the current state of the folder. Best for decoders that rewrite a single rolling file in place (dump1090-fa, readsb, VRS).

You don't have to know which one fits: the prompt asks for the folder first and preselects the right mode from what it finds there (a lone `aircraft.json` means a rolling snapshot, so periodic; wildcard patterns mean new files appear over time, so watch). Watch mode's local output follows the same resolution order as everything else (`--out-dir` > your configured output folder > beside the input), and a read-only decoder dir like `/run/readsb` no longer blocks its uploads, see the note under "One-shot" above.

Per-platform mechanism, all user-scope (no sudo):

| Platform | Mechanism |
|---|---|
| Linux with systemd | `~/.config/systemd/user/muninn-upload.{service,timer}` |
| macOS / Linux without systemd | user crontab (periodic only. Cron can't run daemons) |
| Windows | `schtasks /Create` at user scope |

The interactive flow shows the exact unit / cron line / scheduled task that will be installed and asks "Install now?" before touching your system. The marker comment `managed-by-muninn` flags entries Muninn owns, so `--unschedule` removes only what was installed by Muninn (your other crontab entries are left alone).

For headless / scripted install:

```bash
./run.sh --schedule \
  --schedule-mode periodic \
  --schedule-input /run/dump1090-fa \
  --schedule-glob 'aircraft.json' \
  --schedule-interval 5
```

### Dry-run first

The interactive flow defaults dry-run to **yes**: the installed task runs `--dry-run`, which decodes and writes JSON but doesn't actually POST to wdgwars.pl. Useful for verifying the install before any data lands on your account. Once you confirm the scheduled task is picking up files correctly, re-run `--schedule` and answer No to the dry-run prompt to flip to live uploads. The headless equivalent is `--schedule-dry-run`.

Verify and tail logs (Linux/systemd example):

```bash
systemctl --user status muninn-upload.timer
journalctl --user -u muninn-upload.timer -f
```

### Reboot survival (systemd lingering)

Systemd **user** units, the kind `--schedule` installs on Linux. Only run while the installing user has an active login session. Without something extra, a reboot or even a plain logout stops `muninn-upload.timer`/`.service` with no error anywhere. The decoder is a separate system service and keeps running, so its web map keeps showing planes, from the outside everything looks healthy while nothing is actually being uploaded anymore.

To prevent that, right after enabling the unit `--schedule` (interactive and headless alike) checks `loginctl show-user <user> --property=Linger` and, if lingering isn't already on, attempts `loginctl enable-linger <user>` itself. Some systems let a user enable their own lingering unprivileged via polkit; others require root. Muninn never prompts for or runs `sudo` itself, if the unprivileged attempt fails, or `loginctl`/`systemd-logind` isn't present at all, it prints a loud warning and the exact command to run yourself:

```bash
sudo loginctl enable-linger <user>
```

It never reports lingering as enabled without re-checking the property afterward. An unverified success here would just reproduce the exact bug this exists to prevent. Re-run `--schedule` at any point to re-check. `--unschedule` only removes the unit files; it does not touch lingering.

---

## Live TCP stream (skip the file entirely)

If your receiver serves a raw SBS-1/BaseStation feed on a TCP port (dump1090 `--net`'s port 30003, readsb's equivalent), Muninn can connect to it directly instead of watching a file that something else (ncat, a shell redirect) writes to disk:

```bash
./run.sh --stream 192.168.1.50:30003 --upload
```

- Port defaults to `30003` if you omit it (`--stream 192.168.1.50` works the same as `--stream 192.168.1.50:30003`).
- Uploads flush every `--stream-interval` seconds (default 5), covering only the aircraft that changed since the last flush, not everything seen so far.
- Auto-reconnects with backoff if the receiver drops the connection; whatever it had already decoded is kept in memory and flushed on the next successful reconnect.
- Runs until Ctrl+C, same as `--watch`.
- Combine with `--dry-run` to watch it decode without uploading, or `--stdout` to see the JSON on each flush.

This is the same data `--watch`ing a directory of ncat-piped SBS-1 output gives you, just without the extra `ncat`/`tee` process and without waiting on a poll interval against a file. Only SBS-1/BaseStation text is supported this way (it's line-oriented and trivial to stream); Beast binary (port 30005) still needs the file-based `--watch` path.

### Multiple `--stream` feeds

Each `muninn.py --stream` process handles exactly one TCP target, there's no
multi-target flag. For more than one receiver, run one process per feed (same
pattern as running Muninn on multiple hosts against multiple SDRs, just over
sockets instead of files). `systemd/muninn-stream@.service` in this repo is an
instantiated unit template that makes adding feeds a one-liner instead of
hand-writing a new unit file each time:

```bash
cp systemd/muninn-stream@.service ~/.config/systemd/user/
systemctl --user daemon-reload

# One line per receiver - %i becomes the --stream target:
systemctl --user enable --now muninn-stream@192.168.1.50:30003.service
systemctl --user enable --now muninn-stream@10.0.0.12:30003.service
```

Edit the `ExecStart` path in the template first if Muninn isn't cloned at
`~/code/adsb-to-wdgwars`. All instances upload under the same API key; the
server dedupes overlapping coverage the same way it already does across
separate hosts.

This is a plain static template, not wired into `--schedule`, `--schedule`'s
interactive/headless flow (watch/periodic modes) doesn't know about `--stream`
yet, so there's no auto-generated-and-installed path for it. If that
friction is worth removing later, that's a separate follow-up.

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
- Timestamps in BaseStation are naive strings like `"2024-08-15 14:32:11.123"` with no timezone information. Muninn defaults to treating them as **UTC** (matching the rest of muninn's output). If your BaseStation install logged in local time, pass `--sqb-tz America/New_York` (or any IANA zone) to convert on the fly. On Windows, the IANA zone database is provided by the `tzdata` PyPI package, install it with `pip install tzdata` if `--sqb-tz` reports an unknown zone.
- BaseStation does not store `Last(GroundSpeed|Track)`, so the end-of-flight record surfaces `speed_kt=0` / `heading=0` rather than carrying forward the values from `First*`.
- If `Flights` is absent or empty (some installs only populate `Aircraft`), muninn exits nonzero with a clear message rather than write an empty JSON.

---

## All command-line flags

```
--out PATH         write JSON to one specific output path
--out-dir DIR      write all output JSON into this folder (created if missing)
--stdout           print JSON to stdout instead of writing a file
--upload           POST to WDGWars after converting (HMAC-signed envelope)
--watch DIR        watch a folder; auto-convert (and upload) new files
--watch-interval N seconds between watch polls (default: 30)
--watch-glob G     glob for the watch dir (default: *.txt; use * for all)
--stream HOST[:PORT]  connect directly to a live SBS-1/BaseStation TCP
                   feed instead of watching a file (port defaults to
                   30003). No input file/directory needed.
--stream-interval N seconds between upload flushes for --stream (default: 5)
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
--batch-size N     aircraft per upload chunk (default: 1000)
--preview          print the first 6 normalised records as JSON lines and
                   exit (no file written, no upload, parser dry-run)
--version          print Muninn's version
--check-version    ask GitHub whether a newer release exists, then exit
                   (the only thing here that contacts GitHub on its own)
--update           pull the latest release (git pull if you cloned; ZIP
                   installs refresh muninn.py, requirements.txt, and the
                   run/setup/update wrapper scripts)
--schedule         install a scheduled upload task (interactive alone, or
                   headless with the --schedule-* flags below)
--unschedule       remove every Muninn-managed scheduled task
--schedule-mode M  watch (daemon watching a folder) or periodic (every N
                   minutes) for headless --schedule
--schedule-input D decoder output directory baked into the scheduled task
--schedule-glob G  file pattern for the scheduled task (default guessed
                   from the directory contents)
--schedule-interval N  minutes between periodic runs (default 5, 1-60)
--schedule-dry-run install the schedule with --dry-run baked in
-q, --quiet        suppress informational output (banners, format/decoded
                   notices, range + dump1090 warnings). Errors still print.
--no-version-check accepted for compatibility, does nothing (Muninn never
                   checks for updates on its own any more; use
                   --check-version instead)
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

On startup, Muninn probes localhost port 30104 (Beast input, `--net-bi-port`) and port 30001 (raw Mode-S input, `--net-ri-port`). If either port is open it prints a warning before processing anything:

```
[muninn] WARNING: dump1090 network input port(s) are open on localhost:
[muninn]   port 30104: Beast input (--net-bi-port) -- accepts remote aircraft feeds
[muninn]   Remote aircraft data may be mixing with locally received planes.
```

The most common cause of implausibly large reception ranges (aircraft 1000+ km apart) is dump1090 running with `--net` while a piaware or FlightAware feeder is also active, silently injecting remote aircraft into the local stream. No data is sent or received during the probe. It is a single connect attempt per port.

### Aircraft range check

After decoding, Muninn checks whether any aircraft positions are beyond 500 km from the median position of the capture, roughly the 1090 MHz radio horizon at cruise altitude. Outliers are flagged with their ICAO, callsign, and distance:

```
[muninn] WARNING: 2 of 41 aircraft (5%) are >500 km from the position centroid, possible network-fed remote data mixed with local reception.
[muninn]   Centroid: 41.4600, -82.1800
[muninn]   outlier: a1b2c3 UAL123 @ 51.4700,-0.4543, 5642 km from centroid
[muninn]   If unexpected, check whether dump1090 has --net enabled with a remote Beast/piaware feed active on the same machine.
```

No records are removed. These are warnings only. If you are deliberately aggregating data from multiple locations, you can ignore them.

## Updating

Double-click **`update.bat`** (Windows) or run **`./update.sh`** (Mac/Linux) from the Muninn folder. The script:

1. Pulls the latest `requirements.txt` from GitHub so any new dependencies are visible to pip.
2. Runs `pip install --upgrade -r requirements.txt`.
3. Updates `muninn.py` itself (via `git pull` if you cloned the repo, otherwise via a direct HTTPS download from GitHub).

This order matters across versions that add or bump a dependency. Pip has to know about the new dep before muninn.py tries to import it.

If you prefer the CLI:

```bash
./run.sh --update
```

`muninn.py --update` also refreshes `requirements.txt` and re-runs pip itself, so direct CLI updates self-heal too. But only if `muninn.py` can already load (i.e. its current deps are installed). The wrapper script is the more robust path because it bootstraps deps before importing anything.

Muninn never phones home on its own. If you want to know whether a newer release exists, run `--check-version` and it asks GitHub once and tells you. Nothing else on an ordinary run contacts anybody except the WDGWars upload endpoint, and only when you invoke an upload. See [CHANGELOG.md](CHANGELOG.md) for per-release notes.

---

## Re-running first-time setup

To change where the input/output folders live, or re-run the API-key prompt:

```bash
# folders
del "%APPDATA%\muninn\folders.json"        (Windows)
rm  ~/.config/muninn/folders.json          (Mac/Linux)

# API key (just re-save it)
./run.sh --setup
```

---

## Security

- API key stored at `%APPDATA%\muninn\api.key` (Windows) or `~/.config/muninn/api.key` (Unix, mode `0600`).
- API key is **never** required for local conversion, only for `--upload`.
- HMAC-SHA256-signed envelope, explicit TLS 1.2+ context, system trust store.
- Key is scrubbed from all error output via `_scrub()`.
- `--save-key` refuses to write through a symlink.
- No telemetry. Nothing leaves your machine unless `--upload` is set.

Full threat model: [SECURITY.md](SECURITY.md). Found a vulnerability? Open a private security advisory via the repo's [Security tab](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars/security/advisories).

---

## License

MIT, see [LICENSE](LICENSE).

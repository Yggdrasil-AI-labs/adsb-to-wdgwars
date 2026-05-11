# adsb-to-wdgwars

**Take an ADS-B `.txt` file from your H4M (or dump1090, readsb, RTL-SDR — anything) and upload the aircraft to WDGoWars.**

That's it. One Python script, three minute setup.

---

## What is this?

This is a **command-line tool** (Terminal / PowerShell / Command Prompt). It is **not** an app, not a website, not a GUI. You feed it a text file, it gives you back something WDGoWars can accept.

> "But I don't know command-line!"

That's fine — there's exactly **three commands** to learn, all listed below. Copy-paste, replace one filename, done.

---

## I just want to upload my H4M file (5 steps)

### Step 1: Make sure you have Python

Open a terminal and type:

```bash
python3 --version
```

If you see something like `Python 3.10.6` you're good. If you see "command not found" or "not recognized", install Python from [python.org](https://www.python.org/downloads/) (any version ≥ 3.8 works) — make sure to check "Add Python to PATH" during install on Windows.

### Step 2: Get this tool

```bash
git clone https://github.com/HiroAlleyCat/adsb-to-wdgwars
cd adsb-to-wdgwars
```

> Don't have `git`? Click the green **"Code"** button at the top of [this repo page](https://github.com/HiroAlleyCat/adsb-to-wdgwars), pick **"Download ZIP"**, unzip it, then `cd` into the folder.

### Step 3: (Maybe) Install one dependency

You only need this if your H4M file looks like raw hex (`*8D4840D6...;` per line). For everything else, **skip this step**.

```bash
pip install pyModeS
```

### Step 4: Convert your file

You can run this from anywhere — the tool writes its output **right next to your input file**, not wherever you happen to be:

```bash
python3 adsb_to_wdgwars.py /path/to/your-h4m-file.txt
```

That writes `your-h4m-file.wdgwars.json` next to your input. The tool prints the full path when it's done so you know exactly where to look. Example output:

```
[adsb] detected format: mayhem
[adsb] decoded 47 unique aircraft with positions

[adsb] OK -- wrote 47 aircraft to:
       /home/babe/captures/ADSB.wdgwars.json
```

Want a different name or location? Use `--out`:
```bash
python3 adsb_to_wdgwars.py your-file.txt --out ~/Desktop/aircraft.json
```

Want to pipe it somewhere else (a different script, jq, etc.) without writing a file? Use `--stdout`.

### Step 5: One-time setup (saves your API key)

> **You only need this if you want to upload to WDGoWars.** If you just want a local JSON file, you're already done — skip to the end.

Run the interactive setup:

```bash
python3 adsb_to_wdgwars.py --setup
```

You'll see:

```
────────────────────────────────────────────────────────────
 adsb-to-wdgwars — API key setup
────────────────────────────────────────────────────────────

 An API key is ONLY needed if you want to upload to WDGoWars.
 Local conversion to JSON works without one.

 Get your key from: https://wdgwars.pl/  →  profile  →  API Key
 It will be saved to: /home/you/.config/adsb-to-wdgwars/api.key

 Set up your WDGoWars API key now? [Y/n]
```

Answer **`y`** (or just hit Enter) to continue. Answer **`n`** to skip — the tool will still convert files locally, it just won't upload.

If you said yes, you'll be prompted next:

```
 Paste your WDGoWars API key (hidden):
```

Paste your key (it stays hidden — won't show in your terminal or shell history), hit Enter. The tool validates the key against the WDGoWars server, then saves it locally if it works. **You only do this once.**

You'll see:
```
[adsb] key OK — user=YourName
[adsb]   wifi=29067 ble=65420 aircraft=4053 total=98540
 ✓ Setup complete. You can now run uploads without --key:
   python3 adsb_to_wdgwars.py yourfile.txt --upload
```

If you paste the wrong key, the tool tells you and lets you try again. Ctrl+C to cancel.

> **Don't want to use `--setup`?** You can also pass the key non-interactively:
> ```bash
> python3 adsb_to_wdgwars.py --save-key "your-key-here"
> ```
> Or just run an upload — if no key is saved, the tool automatically prompts you for one and saves it after.

### Step 6: Upload

```bash
python3 adsb_to_wdgwars.py your-h4m-file.txt --upload
```

That's it. No env vars, no `--key` flag, the tool reads your saved key automatically.

**Verify your saved key any time:**
```bash
python3 adsb_to_wdgwars.py --whoami
```

**One-off override** (different account, testing) — pass `--key` on the command line:
```bash
python3 adsb_to_wdgwars.py your-file.txt --upload --key "different-key"
```

### Key resolution order

The tool looks for your API key in this order — first match wins:
1. `--key YOURKEY` on the command line
2. `$WDGWARS_API_KEY` environment variable
3. Saved key file (written by `--setup` or `--save-key`)

If none of those find a key when you try to `--upload`, the tool automatically drops into interactive setup so you can paste it then.

Done. You should see something like:

```
[adsb] detected format: sbs1
[adsb] decoded 47 unique aircraft with positions
chunk 1/1: 47 aircraft, 8210 B
  HTTP 200 in 11.1s imported=12 already_seen=35
DONE — aircraft_sent=47 imported=12 already_seen=35
```

"imported" is how many were new to WDGoWars. "already_seen" means another user (or you) already submitted them.

---

## Worked example (real Pi data)

Tested against a live ADS-B feed:

```
[adsb] detected format: json
[adsb] decoded 8 unique aircraft with positions
chunk 1/1: 8 aircraft, 1928 B
  HTTP 200 in 11.1s imported=2 already_seen=6
DONE — aircraft_sent=8 imported=2 already_seen=6
```

Real aircraft sent: DAL1407, UAL1776, SKW499X, plus 4 NetJets corporate jets, all at FL230-FL430 over northeastern Ohio. Server accepted the upload, credited 2 as new captures and 6 as reinforcement.

---

## Watch mode — drop files and forget

If you regularly save H4M captures and want them auto-uploaded, point the tool at a folder and walk away:

```bash
WDGWARS_API_KEY="your-key" python3 adsb_to_wdgwars.py /path/to/captures --watch --upload
```

The tool will:
- Poll the folder every 30 seconds (configurable with `--watch-interval N`)
- Pick up any new `.txt` files (configurable with `--watch-glob '*.txt'`)
- Convert each one, write the JSON next to it, push to WDGoWars
- Skip files it's already processed (state kept in `.adsb-state.json` in the folder)
- Survive Ctrl+C cleanly and resume exactly where it left off next time
- Retry failed uploads on the next cycle automatically

**Real example log:**
```
[watch] watching /home/babe/captures every 30s for '*.txt' (Ctrl+C to stop)
[watch] 12 files already processed

[watch] processing 2026-05-10-flight01.txt
[watch]   decoded 47 aircraft
[watch]   wrote /home/babe/captures/2026-05-10-flight01.wdgwars.json
chunk 1/1: 47 aircraft, 8210 B
  HTTP 200 in 11.1s imported=12 already_seen=35
```

Run it under `tmux`, `screen`, or as a `systemd` service to keep it going across reboots. Each H4M dump you save shows up on WDGoWars within 30 seconds with zero manual work.

## Frequently Asked Questions

### Is this a download/install thing or a terminal thing?

**Terminal.** You run `python3 adsb_to_wdgwars.py yourfile.txt` from a command line. There's no `.exe` to double-click, no website to log into. The tool reads your text file and either writes a JSON file or POSTs to WDGoWars's API directly.

### What text formats does it accept?

It **auto-detects** these (you don't have to tell it which):

| Looks like | Format | Where you'd see it |
|---|---|---|
| `8DA4... ICAO:A41144 DAL2594 Alt:34000 Lat:41.67 Lon:-81.47 ...` | **PortaPack Mayhem ADSB.TXT** | **HackRF + PortaPack H1/H2/H4/H4M** running Mayhem firmware |
| `*8D4840D6202CC371C32CE0576098;` | AVR raw Mode-S | dump1090 `--raw` output, generic RTL-SDR |
| `MSG,3,1,1,A12345,1,2026/05/10,...` | SBS-1 / BaseStation CSV | Most ADS-B receivers, dump1090 port 30003, readsb |
| `{"aircraft":[{"hex":"a12345",...}]}` | dump1090 / readsb JSON | `aircraft.json` from `dump1090-fa`, FlightAware Pi |
| `icao,lat,lon,alt,...` | Generic CSV | Custom receivers — use `--csv-format` if not auto-detected |

If your file doesn't fit any of these, paste a 3-line sample in a GitHub issue and I'll add support.

### Where do I get a WDGoWars API key?

Log into [wdgwars.pl](https://wdgwars.pl/), open your **profile page**, look for "API Key". Copy that whole string. Save it once with `--save-key`:

```bash
python3 adsb_to_wdgwars.py --save-key "paste-your-key-here"
```

After that, every upload command uses it automatically. To check it works: `python3 adsb_to_wdgwars.py --whoami`.

### Can I see what would be uploaded without actually uploading?

Yes — add `--dry-run` to the upload command:

```bash
python3 adsb_to_wdgwars.py myfile.txt --upload --dry-run
```

It'll print the request shape and stop before actually POSTing.

### What if the upload fails?

The tool prints the server's error message. The two common ones:

- **`403 Nonce already used`** — the tool already handles this (it uses `/api/upload/` with the trailing slash). If you somehow trigger it, double-check you're on the latest version.
- **`401 Unauthorized`** — your API key is wrong, or `WDGWARS_API_KEY` isn't set. Re-check.
- **`429 Too Many Requests`** — server-side cooldown. Wait a few minutes and try again.
- **`0 imported, all already_seen`** — totally normal for popular flight corridors; you're contributing reinforcement credit to the account, just not novel captures.

### My H4M file is some other format you don't support

Open a GitHub issue with:
1. The first 5 lines of your file (you can paste it raw — it's not private data)
2. The model/firmware of your H4M

I'll add detection + a parser. Usually takes ~30 minutes.

### Does this work on Windows / Mac / Linux?

Yes, all three. Python 3.8+ is the only requirement.

### Does this only work for ADS-B aircraft?

Yes — aircraft only. WiFi/BLE has its own upload path on WDGoWars; use whatever tool you already use for that.

### Can I see what the tool is doing under the hood?

Sure — the whole tool is one Python file (`adsb_to_wdgwars.py`, ~530 lines). Read it. The interesting parts are:
- `detect_format()` — sniffs the first non-comment line
- `parse_sbs1()` / `parse_avr()` / `parse_json()` / `parse_csv()` — one per format
- `upload()` — builds the HMAC-SHA256 signed envelope and POSTs

### Does it leak my data anywhere?

No. The tool only POSTs to `https://wdgwars.pl/api/upload/` (the URL is hardcoded — change with `--api-url` if WDGoWars ever moves it). No telemetry, no analytics, no error reporting. Run it offline with `--stdout` if you want to verify the output before uploading.

### Is my API key stored securely?

Yes. When you run `--save-key`, the file is written with mode `0600` (Unix — only your user can read it) via a low-level `os.open()` that sets permissions **before** writing the secret. Symlink attacks are explicitly blocked. The key is never printed in any output, even on errors (any leak into a server response message is scrubbed with `xxxx…xxxx` redaction before display).

See [SECURITY.md](SECURITY.md) for the full threat model + mitigations.

---

## Technical reference (for nerds + PR contributors)

### Output schema (what's in `aircraft.json`)

```json
[
  {
    "icao": "AC3053",
    "callsign": "SWA588",
    "lat": 41.462870,
    "lon": -82.241030,
    "alt_ft": 40000,
    "speed_kt": 420,
    "heading": 275,
    "first_seen": "2026-05-10 13:45:22",
    "type": "ADSB"
  }
]
```

| Field | Type | Source |
|---|---|---|
| `icao` | string | UPPER hex, 24-bit, ICAO transponder code |
| `callsign` | string | Flight string ("UAL1776"), tail number, or empty |
| `lat` / `lon` | float | Decimal degrees, WGS-84 |
| `alt_ft` | int | Altitude in feet (`alt_baro` from dump1090) |
| `speed_kt` | int | Ground speed in knots |
| `heading` | int | True track in degrees (0–359) |
| `first_seen` | string | `"YYYY-MM-DD HH:MM:SS"` UTC |
| `type` | string | Always `"ADSB"` |

### Upload envelope (what's POSTed to `/api/upload/`)

```python
body_json = json.dumps({"networks": [], "aircraft": [...], "meshcore_nodes": []},
                       separators=(",", ":"))
data_b64  = base64.b64encode(body_json.encode()).decode()
nonce     = secrets.token_hex(8)
sig       = hmac.new(api_key.encode(), (nonce + data_b64).encode(),
                     hashlib.sha256).hexdigest()
envelope  = {"data": data_b64, "nonce": nonce, "sig": sig}
```

Headers:
```
Content-Type: application/json
X-API-Key: <your-key>
```

⚠️ **The trailing slash on `/api/upload/` is required.** Without it the server returns `403 {"error":"Nonce already used"}` for every request.

### Server response (success)

```json
{
  "ok": true,
  "aircraft_imported": 15,
  "aircraft_already_seen": 972,
  "new_badges": []
}
```

Format reference is reverse-engineered from the canonical [`LOCOSP/WatchDogsGo`](https://github.com/LOCOSP/WatchDogsGo) wardrive client (`plugins/wardrive_upload.py`). Not officially documented — file an issue if WDGoWars publishes a spec.

### Full CLI

```
usage: adsb_to_wdgwars.py [-h] [--out OUT] [--stdout] [--no-save]
                          [--format {auto,avr,sbs1,json,csv,mayhem}]
                          [--csv-format CSV_FORMAT]
                          [--upload] [--dry-run]
                          [--key KEY] [--api-url API_URL]
                          [--batch-size BATCH_SIZE]
                          input

positional arguments:
  input                 ADS-B capture file (.txt, .csv, .json)

options:
  --out PATH            write JSON to this exact path
                        (default: <input>.wdgwars.json right next to input)
  --stdout              print JSON to stdout instead of writing a file
  --no-save             with --upload: skip the local audit-trail JSON
  --format              force input format (default: auto-detect)
  --csv-format          column order for generic CSV
  --upload              POST to wdgwars.pl after conversion
  --dry-run             with --upload, build request but don't send
  --key KEY             API key (overrides $WDGWARS_API_KEY)
  --api-url URL         override upload endpoint
  --batch-size N        aircraft per upload chunk (default: 1000)
```

### Where does the output go?

| You ran | Output goes to |
|---|---|
| `python3 adsb_to_wdgwars.py file.txt` | `file.wdgwars.json` next to the input |
| `python3 adsb_to_wdgwars.py file.txt --out /tmp/x.json` | `/tmp/x.json` exactly |
| `python3 adsb_to_wdgwars.py file.txt --stdout` | stdout (nothing written) |
| `python3 adsb_to_wdgwars.py file.txt --upload` | `file.wdgwars.json` (kept as audit trail) **and** POSTed to WDGoWars |
| `python3 adsb_to_wdgwars.py file.txt --upload --no-save` | POSTed only, no local file |

The tool always prints the absolute path of the output file so you can find it.

### Custom CSV format hint

If your H4M dumps a CSV without a header row:

```bash
python3 adsb_to_wdgwars.py myfile.csv \
  --csv-format icao,lat,lon,alt_ft,speed_kt,heading,callsign,first_seen
```

Recognised column names (case-insensitive): `icao` / `hex`, `callsign`, `lat` / `latitude`, `lon` / `longitude`, `alt_ft` / `alt` / `altitude`, `speed_kt` / `speed` / `gs`, `heading` / `track` / `cog`, `first_seen` / `timestamp`. Use `_` or `skip` to ignore a column.

---

## Security

Full threat model + mitigations in [SECURITY.md](SECURITY.md). Short version: API key stored mode-600, never printed in errors (scrubbed), explicit TLS verification, no telemetry, no `eval`/`exec`/`shell=True`, no network calls without `--upload` opt-in, symlink-attack-resistant key file writes.

Report security issues via GitHub Security Advisories (link in SECURITY.md), not public issues.

## License

MIT — do whatever you want with it. Credit nice but not required.

## Issues / PRs

Open an issue with:
- A sample of your input file (3-5 lines)
- The error or unexpected output
- Your OS + Python version

PRs welcome — especially for new input formats (Stratux, FlightAware Pro, custom H4M firmwares).

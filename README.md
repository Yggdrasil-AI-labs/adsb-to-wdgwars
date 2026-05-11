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

Put your H4M `.txt` file in the same folder, then:

```bash
python3 adsb_to_wdgwars.py your-h4m-file.txt --out aircraft.json
```

This makes a new file called `aircraft.json` with the data WDGoWars wants.

### Step 5: Upload

Get your API key from your WDGoWars profile, then:

**On Mac/Linux:**
```bash
export WDGWARS_API_KEY="your-api-key-here"
python3 adsb_to_wdgwars.py your-h4m-file.txt --upload
```

**On Windows (PowerShell):**
```powershell
$env:WDGWARS_API_KEY = "your-api-key-here"
python3 adsb_to_wdgwars.py your-h4m-file.txt --upload
```

**On Windows (Command Prompt):**
```cmd
set WDGWARS_API_KEY=your-api-key-here
python3 adsb_to_wdgwars.py your-h4m-file.txt --upload
```

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

## Frequently Asked Questions

### Is this a download/install thing or a terminal thing?

**Terminal.** You run `python3 adsb_to_wdgwars.py yourfile.txt` from a command line. There's no `.exe` to double-click, no website to log into. The tool reads your text file and either writes a JSON file or POSTs to WDGoWars's API directly.

### What text formats does it accept?

It **auto-detects** these (you don't have to tell it which):

| Looks like | Format | Where you'd see it |
|---|---|---|
| `*8D4840D6202CC371C32CE0576098;` | AVR raw Mode-S | dump1090 `--raw` output, some H4M variants |
| `MSG,3,1,1,A12345,1,2026/05/10,...` | SBS-1 / BaseStation CSV | Most ADS-B receivers, dump1090 port 30003, readsb |
| `{"aircraft":[{"hex":"a12345",...}]}` | dump1090 / readsb JSON | `aircraft.json` from `dump1090-fa`, FlightAware Pi |
| `icao,lat,lon,alt,...` | Generic CSV | Some custom receivers — use `--csv-format` if it's not auto-detected |

If your file doesn't fit any of these, paste a 3-line sample in a GitHub issue and I'll add support.

### Where do I get a WDGoWars API key?

Log into [wdgwars.pl](https://wdgwars.pl/), open your **profile page**, look for "API Key". Copy that whole string. That's what goes in `WDGWARS_API_KEY`.

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

No. The tool only POSTs to `https://wdgwars.pl/api/upload/` (the URL is hardcoded — change with `--api-url` if WDGoWars ever moves it). No telemetry, no analytics. Run it offline with `--out aircraft.json` if you want to verify the output before uploading.

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
usage: adsb_to_wdgwars.py [-h] [--out OUT]
                          [--format {auto,avr,sbs1,json,csv}]
                          [--csv-format CSV_FORMAT]
                          [--upload] [--dry-run]
                          [--key KEY] [--api-url API_URL]
                          [--batch-size BATCH_SIZE]
                          input

positional arguments:
  input                 ADS-B capture file (.txt, .csv, .json)

options:
  --out OUT, -o OUT     write JSON to this path
  --format              force input format (default: auto-detect)
  --csv-format          column order for generic CSV
  --upload              POST to wdgwars.pl after conversion
  --dry-run             with --upload, build request but don't send
  --key KEY             API key (overrides $WDGWARS_API_KEY)
  --api-url URL         override upload endpoint
  --batch-size N        aircraft per upload chunk (default: 1000)
```

### Custom CSV format hint

If your H4M dumps a CSV without a header row:

```bash
python3 adsb_to_wdgwars.py myfile.csv \
  --csv-format icao,lat,lon,alt_ft,speed_kt,heading,callsign,first_seen
```

Recognised column names (case-insensitive): `icao` / `hex`, `callsign`, `lat` / `latitude`, `lon` / `longitude`, `alt_ft` / `alt` / `altitude`, `speed_kt` / `speed` / `gs`, `heading` / `track` / `cog`, `first_seen` / `timestamp`. Use `_` or `skip` to ignore a column.

---

## License

MIT — do whatever you want with it. Credit nice but not required.

## Issues / PRs

Open an issue with:
- A sample of your input file (3-5 lines)
- The error or unexpected output
- Your OS + Python version

PRs welcome — especially for new input formats (Stratux, FlightAware Pro, custom H4M firmwares).

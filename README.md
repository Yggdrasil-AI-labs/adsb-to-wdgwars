# adsb-to-wdgwars

Convert ADS-B capture text files (from H4M, dump1090, readsb, RTL-SDR, or any standard receiver) to the **WDGoWars** aircraft upload JSON, and optionally POST directly to `wdgwars.pl`.

## Why

ADS-B receivers spit out a few different text formats. WDGoWars wants a specific HMAC-signed JSON envelope. This tool handles the conversion + signing in one step so you don't have to write your own.

## Supported input formats

The format is auto-detected from the first non-empty line:

| Looks like | Format | Notes |
|---|---|---|
| `*8D4840D6202CC371C32CE0576098;` | **AVR raw Mode-S** | Needs `pyModeS` for DF17/DF18 position decoding |
| `MSG,3,1,1,A12345,1,2023/01/01,12:00:00.000,...` | **SBS-1 / BaseStation CSV** | Already decoded — direct field mapping |
| `{"now":..., "aircraft":[{...}]}` | **dump1090 / readsb JSON** | Snapshot or JSON-lines stream |
| `icao,lat,lon,alt,callsign,timestamp` | **Generic CSV** | Pass `--csv-format` if header is missing |

## Install

```bash
git clone https://github.com/HiroAlleyCat/adsb-to-wdgwars
cd adsb-to-wdgwars
pip install -r requirements.txt   # only needed for AVR raw input
```

`pyModeS` is the only dependency, and it's optional — you only need it if you're feeding raw Mode-S frames (`*XXXX;` lines).

## Use

### Just convert to JSON

```bash
python3 adsb_to_wdgwars.py mycapture.txt --out aircraft.json
```

Outputs an array of WDGoWars-shaped records:

```json
[
  {
    "icao": "AC3053",
    "callsign": "SWA588",
    "lat": 41.46287,
    "lon": -82.24103,
    "alt_ft": 40000,
    "speed_kt": 420,
    "heading": 275,
    "first_seen": "2026-05-10 13:45:22",
    "type": "ADSB"
  }
]
```

### Convert + upload

```bash
export WDGWARS_API_KEY="your-key"
python3 adsb_to_wdgwars.py mycapture.txt --upload
```

Or with the key on the CLI:

```bash
python3 adsb_to_wdgwars.py mycapture.txt --upload --key "your-key"
```

### Dry-run (show what would be sent)

```bash
python3 adsb_to_wdgwars.py mycapture.txt --upload --dry-run
```

### Generic CSV with custom column order

If your H4M or other receiver dumps a CSV without a header, tell the tool which columns mean what:

```bash
python3 adsb_to_wdgwars.py mycapture.txt \
  --csv-format icao,lat,lon,alt_ft,speed_kt,heading,first_seen
```

Recognised column names (case-insensitive):

```
icao | hex          ICAO 24-bit hex
callsign            Flight string / tail number
lat | latitude      Latitude (decimal degrees)
lon | longitude     Longitude (decimal degrees)
alt_ft | alt | altitude   Altitude in feet
speed_kt | speed | gs     Speed in knots
heading | track | cog     Heading in degrees
first_seen | timestamp    "YYYY-MM-DD HH:MM:SS" UTC
_ | skip                  Skip this column
```

## What the tool actually does

1. **Auto-detect** the input format from the first line.
2. **Decode** each frame:
   - SBS-1: direct CSV field map (MSG type 1 → callsign, 3 → position, 4 → velocity).
   - AVR raw: `pyModeS` decode of DF17/DF18 (extended squitter) frames; pairs even/odd CPR position frames per aircraft to globally decode lat/lon.
   - JSON: pulls `hex`/`flight`/`lat`/`lon`/`alt_baro`/`gs`/`track` etc. from each aircraft object.
   - CSV: heuristic header detect, or explicit `--csv-format`.
3. **Dedup** by ICAO, keeping the **most recent** position per aircraft.
4. **Drop** records without lat/lon (server rejects them anyway).
5. **Output** the JSON array, or directly **POST** as a signed envelope.

## The upload envelope (for reference)

WDGoWars's aircraft upload endpoint is `POST https://wdgwars.pl/api/upload/` (**trailing slash required** — without it the server returns `403 {"error":"Nonce already used"}` because the no-slash variant rejects every payload as a replay).

The body is an HMAC-SHA256-signed JSON envelope:

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

Server response on success:
```json
{"ok":true,"aircraft_imported":15,"aircraft_already_seen":972,"new_badges":[]}
```

Format reference is reverse-engineered from the canonical [LOCOSP/WatchDogsGo](https://github.com/LOCOSP/WatchDogsGo) wardrive_upload.py plugin.

## Aircraft record schema

| Field | Type | Notes |
|---|---|---|
| `icao` | string | UPPER hex, 24-bit |
| `callsign` | string | Flight number / tail; may be empty |
| `lat` | float | Decimal degrees |
| `lon` | float | Decimal degrees |
| `alt_ft` | int | Altitude in feet (`alt_baro` from dump1090) |
| `speed_kt` | int | Ground speed in knots |
| `heading` | int | True track in degrees |
| `first_seen` | string | `"YYYY-MM-DD HH:MM:SS"` UTC |
| `type` | string | Always `"ADSB"` |

## Troubleshooting

- **`403 Nonce already used`** — you forgot the trailing slash on `/api/upload/`.
- **`401 Unauthorized`** — your `X-API-Key` is wrong or missing.
- **`429 Too Many Requests`** — server-side cooldown active. The response includes `cooldown` seconds; wait then retry.
- **`0 imported, all already_seen`** — normal for popular flight corridors; you're contributing reinforcement credit even when not capturing new aircraft.
- **AVR raw decoding misses aircraft** — CPR position decode needs even/odd frame pairs from the same aircraft within ~10 seconds. Sparse captures may not have enough pairs to globally resolve position; consider using local reference coordinates (future feature).

## License

MIT — see [LICENSE](LICENSE).

## Contributing

PRs welcome — especially for new input formats (Stratux, FlightAware Pro, etc.) and bug fixes for edge cases in CPR position decoding.

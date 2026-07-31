# Drop your ADS-B captures here

Save your `ADSB.TXT` (or any other supported ADS-B capture file) into this folder, then run from the repo root:

```bash
python3 muninn.py
```

That's it, no arguments needed. Muninn picks up every supported file in this folder, converts each one, and writes the resulting `.wdgwars.json` into the matching `output/` folder.

**Note:** if you picked the "On Desktop" option during first-run setup, your active `input/` folder is `Desktop\Muninn\input` (not this one in the repo). This file is just here so the folder isn't empty when you clone the repo. Muninn always reads from whichever folder your saved choice points at.

## Supported file types

- PortaPack Mayhem `ADSB.TXT` (HackRF H4M)
- AVR raw Mode-S logs (dump1090 `--raw` / readsb port 30002)
- SBS-1 / BaseStation CSV (dump1090 `--net` / readsb port 30003)
- dump1090 `aircraft.json`
- Generic CSV with `icao,lat,lon,...` columns

Add `--upload` to push results to WDGWars after conversion:

```bash
python3 muninn.py --upload
```

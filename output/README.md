# Converted ADS-B JSON lands here

When you run `python3 muninn.py` with files in the matching `input/` folder, the resulting `.wdgwars.json` files are written here. Each output file is named after its input, e.g. `ADSB.TXT` → `ADSB.wdgwars.json`.

**Note:** if you picked the "On Desktop" option during first-run setup, your active `output/` folder is `Desktop\Muninn\output` (not this one in the repo). This file is just here so the folder exists in the cloned repo.

## What's in the JSON

The output uses dump1090-fa / readsb shape:

```json
{
  "now": 1762000000.0,
  "messages": 268,
  "aircraft": [
    {"hex": "ad15cf", "flight": "AAL3142", "lat": 44.81, "lon": -93.34, ...},
    ...
  ]
}
```

That's the format the WDGWars **web upload form** expects, drag-and-drop straight from here into the website.

If you'd rather skip the drag-and-drop, run with `--upload` and Muninn sends everything for you over the API.

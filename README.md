<p align="center">
  <img src="assets/banner.png" alt="Muninn — Odin's memory-raven for the WDGoWars sky" width="100%"/>
</p>

# Muninn

Convert ADS-B capture files (H4M, dump1090, readsb, RTL-SDR) to WDGoWars JSON and optionally upload them.

One Python script. No dependencies for most formats.

---

## Quickstart

```bash
git clone https://github.com/HiroAlleyCat/adsb-to-wdgwars
cd adsb-to-wdgwars
python3 muninn.py /path/to/your-capture.txt
```

Writes `your-capture.wdgwars.json` next to your input file. Done.

---

## Upload to WDGoWars

```bash
python3 muninn.py your-capture.txt --upload
```

First time uploading? It'll ask for your API key (y/n prompt) and save it for future runs. Grab the key from your WDGoWars profile page.

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

---

## Useful flags

```
--out PATH       write JSON somewhere specific (default: next to input)
--stdout         print JSON to stdout instead of writing a file
--upload         POST to WDGoWars after converting
--watch DIR      watch a folder, auto-convert + upload new files as they appear
--setup          run the interactive API-key wizard
--whoami         show which account your saved key belongs to
--dry-run        with --upload, build the request but don't send
```

---

## Security

- API key stored at `~/.config/muninn/api.key` (mode `0600` on Unix).
- HMAC-SHA256-signed envelope, explicit TLS 1.2+ context.
- Key is scrubbed from all error output.
- No telemetry. Nothing leaves your machine unless `--upload` is set.

Full threat model: [SECURITY.md](SECURITY.md)

---

## License

MIT

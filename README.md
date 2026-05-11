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
--version        print Muninn's version
--update         pull the latest release (uses git pull if you cloned)
```

---

## Updating

If you cloned the repo:
```bash
python3 muninn.py --update
```
That runs `git pull` in place. If you downloaded the ZIP instead, grab the
newest one from the [Releases page](https://github.com/HiroAlleyCat/adsb-to-wdgwars/releases).

Muninn also does a daily background check against the GitHub releases API
and will print a one-liner if a newer version is out — no telemetry, just a
single HEAD request, cached locally for 24h.

See [CHANGELOG.md](CHANGELOG.md) for what's new in each release.

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

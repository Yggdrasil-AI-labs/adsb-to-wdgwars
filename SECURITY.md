# Security Notes

## What this tool does

- Reads a local ADS-B text file (or files in a watched directory).
- Decodes aircraft records.
- Writes a JSON output file next to the input.
- Optionally POSTs the records to `https://wdgwars.pl/api/upload/` (configurable).

## What this tool **does not** do

- ❌ **No telemetry, analytics, or "phone home" of any kind.** The tool only contacts `wdgwars.pl` (and only when `--upload` is set). The hostname is hardcoded; you can override it with `--api-url` if needed.
- ❌ **No `eval`, `exec`, `os.system`, or `shell=True` subprocess calls.** No command-injection paths.
- ❌ **No remote code download/execution.** Pure stdlib + optional `pyModeS` (open-source, MIT, well-known in the ADS-B community).
- ❌ **No data sent anywhere except WDGoWars when explicitly opted in via `--upload`.**

## API key handling

- Resolution priority: `--key` flag → `$WDGWARS_API_KEY` → `~/.config/muninn/api.key` (or `%APPDATA%\muninn\api.key` on Windows).
- The saved key file is written with **mode `0600`** on Unix (only the owner can read it). The file descriptor is opened with `O_CREAT | O_TRUNC` and the permission bits **before** any bytes are written, so the secret is never world-readable, even briefly.
- `--save-key` **refuses to write through a symlink** — protects against an attacker who managed to plant `~/.config/muninn/api.key -> /home/you/.ssh/id_rsa` from clobbering your SSH key when you run `--save-key`.
- The tool **never prints the API key** in any output (success or failure). All error messages route through a `_scrub()` helper that replaces the key with `xxxx…xxxx` (first 4 + last 4 chars) if it ever appears in a server response or exception trace.
- The key is sent over HTTPS (`X-API-Key` header) to `wdgwars.pl` only. The TLS context is explicit (`ssl.create_default_context()`) — system trust store, hostname verification on, TLS 1.2+, secure ciphers.

## What the API key can do

The WDGoWars API key authorizes you to submit observations under your account. **If it leaks**, an attacker could:
- Submit fake aircraft / WiFi / BLE captures under your name.
- Read your account stats via `GET /api/me`.

It cannot (as far as we know):
- Change your password.
- Withdraw money / make purchases (there isn't any).
- Affect other users' accounts.

If you suspect your key has leaked, rotate it on the WDGoWars site and run `--save-key NEW_KEY` locally.

## Output file handling

- The default output path (`<input>.wdgwars.json`) is always written **next to the input file**. Both paths are resolved with `Path.resolve()` and printed as absolute paths.
- If `--out PATH` is passed, **the tool writes to exactly that path**. No path-traversal sandboxing — you decide where it goes. Be careful not to point `--out` at something important.
- Existing files at the output path are **overwritten without warning**. Run with `--stdout` first if you want to preview.

## Watch mode

- The state file `.adsb-state.json` lives in the watched directory and tracks `filename → size:mtime` signatures. It is **not** itself a secret (no API keys, no aircraft data — just file metadata).
- The watch loop **only reads files matching `--watch-glob`** (default `*.txt`). It explicitly skips:
  - Dotfiles (anything starting with `.`)
  - The tool's own `.wdgwars.json` outputs (no infinite loop)
- Uploads happen only when `--upload` is explicitly passed. Conversion-only mode is fully local.

## Dependencies

- **`pyModeS`** (optional, MIT licensed) — only loaded if your input is AVR raw Mode-S. Active open-source project widely used in the ADS-B community.
- Otherwise: Python standard library only. No third-party HTTP libs, no async frameworks, no native extensions.

## Reporting issues

Found a security problem? Open a private security advisory on GitHub:
1. Go to the [Security tab](https://github.com/HiroAlleyCat/muninn/security/advisories) of this repo
2. Click "Report a vulnerability"
3. Describe the issue + a proof of concept if possible

Please do **not** post security issues to the public issue tracker. Aim is to give the maintainer time to patch before public disclosure.

## Threat model — what this tool defends against

| Threat | Mitigation |
|---|---|
| API key disclosure via shell history | Persistent `--save-key` file (no env var or CLI arg needed for normal use) |
| API key disclosure via error logs | `_scrub()` redacts the key from all printed errors / exceptions |
| API key file world-readable | `O_CREAT | 0o600` on first write + explicit `chmod 0o600` follow-up |
| Symlink attack on key file | `--save-key` refuses to write through symlinks |
| MITM on upload | Explicit `ssl.create_default_context()` — system trust store, hostname verification, TLS 1.2+ |
| Command injection | No `shell=True`, no `eval`, no `os.system`. All file I/O via `pathlib`/`open()`. |
| Replay attacks against WDGoWars | HMAC-SHA256-signed envelope with a `secrets.token_hex(8)` nonce per request — server rejects replays |
| Unintended uploads | `--upload` is **opt-in** only. Without it, the tool never makes network requests. |
| Surveillance / telemetry | None. The tool only contacts WDGoWars when explicitly opted in. No analytics, no error reporting, no usage tracking. |

## Things this tool does NOT defend against (out of scope)

- Malware on the user's machine that can read `~/.config/muninn/api.key` (any malware running as your user can read this file — that's the OS's security boundary, not the tool's).
- WDGoWars server compromise (the tool can only be as secure as the server it talks to).
- Network DNS poisoning (TLS cert verification mitigates active MITM but a compromised CA could still be a problem).
- Decoding accuracy / correctness — the tool does its best with `pyModeS`'s CPR decoder, but doesn't validate that the receiver itself wasn't fed bogus data.

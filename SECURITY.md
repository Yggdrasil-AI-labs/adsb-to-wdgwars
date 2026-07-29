# Security Notes

## What this tool does

- Reads a local ADS-B text file (or every supported file in a chosen folder).
- With `--stream HOST[:PORT]` (v2.1.0+), instead connects directly to a TCP address **you supply on the command line** and reads a live SBS-1/BaseStation feed from it — no file involved. The host/port is never inferred, guessed, or pulled from anywhere but that flag; this is the same trust model as `--api-url`, just for the input side instead of the output side. Whatever is on the other end of that connection is treated as untrusted input: the line buffer is capped at 64 KiB (a real SBS-1 line is ~100 bytes), so a peer that never sends a newline can't grow Muninn's memory without bound.
- Decodes aircraft records.
- Writes a JSON output file next to the input, or in the configured output folder.
- Optionally POSTs the records to `https://wdgwars.pl/endpoint/upload/` (the server's Cloudflare-friendly alias of `/api/upload/`, default since v2.0.4; configurable).
- Once per 24h, makes a single HEAD request to GitHub's releases API to check for a newer version. Cached locally in `~/.config/muninn/version-check.json`. Disable by deleting that file (it'll be re-checked but never sooner than daily).
- On Windows, may create a desktop shortcut (`Muninn.lnk`) pointing at the local `muninn.py` script — only when the user explicitly says yes during first-run setup.

## What this tool **does not** do

- ❌ **No telemetry or analytics.** The only outbound traffic is:
  - `https://wdgwars.pl/endpoint/upload/` (alias of `/api/upload/`) — only when `--upload` is set. Hostname hardcoded; override with `--api-url`.
  - `https://api.github.com/repos/Yggdrasil-AI-labs/adsb-to-wdgwars/releases/latest` — single HEAD request, **at most once per 24h**, to compare your local version to the latest tag. No identifiers, no machine info, no usage data — just a public-API call to read a release tag. Result is cached in `~/.config/muninn/version-check.json`. Delete that file to reset; set `__version__` to `"99.0.0"` or stub out `_check_for_update()` if you want to disable it entirely.
  - A raw TCP connection to whatever `--stream HOST[:PORT]` you pass — only when that flag is set, and only to the address you gave it. Muninn never scans, defaults to, or discovers a host on its own; if you don't pass `--stream`, no such connection is ever attempted.
- ❌ **No `eval`, `exec`, `os.system`, or `shell=True` subprocess calls.** No command-injection paths.
- ❌ **No remote code download/execution.** Pure stdlib + optional `pyModeS` (open-source, MIT, well-known in the ADS-B community).
- ❌ **No data sent anywhere except WDGWars when explicitly opted in via `--upload`.**

## API key handling

- Resolution priority: `--key` flag → `$WDGWARS_API_KEY` → `~/.config/muninn/api.key` (or `%APPDATA%\muninn\api.key` on Windows).
- The saved key file is written with **mode `0600`** on Unix (only the owner can read it). The file descriptor is opened with `O_CREAT | O_TRUNC` and the permission bits **before** any bytes are written, so the secret is never world-readable, even briefly.
- `--save-key` **refuses to write through a symlink** — protects against an attacker who managed to plant `~/.config/muninn/api.key -> /home/you/.ssh/id_rsa` from clobbering your SSH key when you run `--save-key`.
- The tool **never prints the API key** in any output (success or failure). All error messages route through a `_scrub()` helper that replaces the key with `xxxx…xxxx` (first 4 + last 4 chars) if it ever appears in a server response or exception trace.
- The key is sent over HTTPS (`X-API-Key` header) to `wdgwars.pl` only. The TLS context is explicit (`ssl.create_default_context()`) — system trust store, hostname verification on, TLS 1.2+, secure ciphers.

## What the API key can do

The WDGWars API key authorizes you to submit observations under your account. **If it leaks**, an attacker could:
- Submit fake aircraft / WiFi / BLE captures under your name.
- Read your account stats via `GET /api/me`.

It cannot (as far as we know):
- Change your password.
- Withdraw money / make purchases (there isn't any).
- Affect other users' accounts.

If you suspect your key has leaked, rotate it on the WDGWars site and run `--save-key NEW_KEY` locally.

## Output file handling

- Resolution order for `<stem>.wdgwars.json`: `--out` (exact path) > `--out-dir` (a folder) > the output folder saved by the first-run prompt / `--setup`, if any > **next to the input file**, as the last resort (added v2.1.2 — see CHANGELOG; previously an explicit input path always used this last-resort tier, even when a folder was configured). Every resolved path is passed through `Path.resolve()` and printed as an absolute path.
- If `--out PATH` is passed, **the tool writes to exactly that path**. No path-traversal sandboxing — you decide where it goes. Be careful not to point `--out` at something important.
- Existing files at the output path are **overwritten without warning**. Run with `--stdout` first if you want to preview.
- A failed local write (most commonly `PermissionError` when pointed at a decoder's root-owned runtime directory, e.g. `/run/readsb`) never produces a traceback and never aborts an in-flight `--upload` — the local file is a side artifact of the audit trail, not the point of `--upload`. It only becomes a hard failure when `--upload` was not requested, since in that case the write was the entire point of the run (v2.1.2, `_write_local_output`).

## Watch mode

- The state file `.adsb-state.json` lives in the watched directory when that directory is writable, and tracks `filename → size:mtime` signatures. It is **not** itself a secret (no API keys, no aircraft data — just file metadata). The in-dir write is confined to the watched directory and refuses a symlinked state file, so it can't be redirected elsewhere. When the watched directory is **not** writable (a decoder's root-owned runtime dir like `/run/readsb`), the state moves to `watch-state-<12-hex-of-sha256(dir)>.json` under the muninn config dir instead of failing every cycle (v2.1.3, `_resolve_watch_state_path`) — same metadata, a location the user owns, filename derived from the resolved dir path so two watched dirs never collide.
- Watch mode's per-file output follows the same resolution order as one-shot mode (`--out-dir` > configured output folder > beside the input, last) and the same failed-write rules as `_write_local_output` above: a failed local write warns and never blocks an in-flight `--upload` (v2.1.3; previously watch mode always wrote beside the input, *before* the upload, so a read-only watched dir silently killed every upload).
- The watch loop **only reads files matching `--watch-glob`** (default `*.txt`). It explicitly skips:
  - Dotfiles (anything starting with `.`)
  - The tool's own `.wdgwars.json` outputs (no infinite loop)
- Uploads happen only when `--upload` is explicitly passed. Conversion-only mode is fully local.

## Dependencies

- **`pyModeS`** (optional, MIT licensed) — only loaded if your input is AVR raw Mode-S. Active open-source project widely used in the ADS-B community.
- Otherwise: Python standard library only. No third-party HTTP libs, no async frameworks, no native extensions.

## Reporting issues

Found a security problem? Open a private security advisory on GitHub:
1. Go to the [Security tab](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars/security/advisories) of this repo
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
| Injection into persisted scheduler entries | Capture dir + glob baked into systemd/cron/schtasks entries are quoted (`shlex.quote` for cron, double-quoting for systemd/schtasks) and the glob is whitelisted (`_validate_glob`); newlines/NUL rejected so no second directive can be injected |
| Watch state-file path traversal | The in-dir `.adsb-state.json` write is confined to the watched directory and refuses a symlinked state file (`_state_path_for`); the read-only-dir fallback filename is a hash of the resolved dir path, not attacker-nameable (`_resolve_watch_state_path`) |
| `.sqb` filename overriding read-only DB open | SQLite opened via a `pathname2url`-encoded read-only URI (`_sqlite_ro_uri`), so a name like `x?mode=rwc.sqb` can't flip the mode |
| Redirected decoder dir under world-writable `/tmp` | Symlinked candidate directories are excluded from suggestions (`_guess_decoder_dirs`) |
| Replay attacks against WDGWars | HMAC-SHA256-signed envelope with a `secrets.token_hex(8)` nonce per request — server rejects replays |
| Unintended uploads | `--upload` is **opt-in** only. Without it, the tool never makes network requests. |
| Surveillance / telemetry | None. The tool only contacts WDGWars when explicitly opted in. No analytics, no error reporting, no usage tracking. |

A full review of the SonarCloud SAST findings against `muninn.py` — what was
fixed, and what is accepted-by-design for a local operator CLI with the
reasoning — lives in [SECURITY-FINDINGS.md](SECURITY-FINDINGS.md).

## Things this tool does NOT defend against (out of scope)

- Malware on the user's machine that can read `~/.config/muninn/api.key` (any malware running as your user can read this file — that's the OS's security boundary, not the tool's).
- WDGWars server compromise (the tool can only be as secure as the server it talks to).
- Network DNS poisoning (TLS cert verification mitigates active MITM but a compromised CA could still be a problem).
- Decoding accuracy / correctness — the tool does its best with `pyModeS`'s CPR decoder, but doesn't validate that the receiver itself wasn't fed bogus data.

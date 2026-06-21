# SonarCloud SAST findings — review & disposition

On **2026-06-21** SonarCloud's SAST flagged **21 security findings** in
`muninn.py`. They were initially marked *Accepted* (tracked debt) to unblock
the CI quality gate, then reviewed and addressed in this change. This document
records the disposition of each finding so the *Accepted* status in SonarCloud
has a written rationale behind it.

## Threat model recap

`muninn.py` is a **single-file CLI run by an operator on their own machine**.
The person typing the command already has a shell on the box, so "path
traversal" / "command injection" *through their own argv* is not the privilege
boundary it is for a network service — an operator who wants to write to
`/etc/passwd` can just do so directly. Treating every CLI argument as a hostile
attacker would produce security theatre.

The boundaries that **are** real, and that the fixes below defend, are:

1. **Persisted scheduler artifacts.** A glob/path baked into a systemd unit,
   crontab line, or Windows scheduled task runs **repeatedly, unattended, with
   the user's privileges**. A stray quote or shell metacharacter there is a
   latent, recurring bug the operator can't see and undo — qualitatively worse
   than a one-shot foreground command.
2. **Second-order path/command construction.** A value that is used to build a
   *different* path (the watch state file) or that carries a NUL/newline into a
   rendered command can escape the location the operator actually chose, or
   inject an extra directive.
3. **A capture file's own metadata** (its name, or SQLite URI metacharacters in
   it) overriding tool behaviour — e.g. flipping a read-only DB open to
   read-write-create.

The hardening lives in the **"Path & argument safety"** section near the top of
`muninn.py` (helpers `_validate_glob`, `_reject_control_chars`, `_user_path`,
`_state_path_for`, `_sqlite_ro_uri`, plus `_systemd_quote` / `_schtasks_arg`
next to the renderers). Regression tests are in
[`tests/test_security.py`](tests/test_security.py).

## Disposition summary

| Rule | Sev | Count | Where | Disposition |
|---|---|---|---|---|
| `pythonsecurity:S2083` | BLOCKER | 1 | `watch_dir()` state-file write | **Fixed** — confined to watched dir, symlink rejected |
| `python:S5443` | CRITICAL | 1 | `_guess_decoder_dirs()` `/tmp/dump1090` | **Fixed** — symlink-excluded; reduced to confirmed suggestion |
| `pythonsecurity:S8707` | MAJOR | 12 | CLI path construction | **Fixed (normalise) / accept-by-design** — see below |
| `python:S6549` | MAJOR | 2 | partial-path traversal | **Fixed** — `resolve()` + control-char rejection |
| `python:S6350` | MAJOR | 2 | scheduler command argument | **Fixed** — quoting + glob validation |
| `python:S8706` | MAJOR | 2 | `parse_sqb()` SQLite open | **Fixed** — URI-encoded read-only open |
| `pythonsecurity:S8705` | MAJOR | 1 | OS command from path | **Fixed** — quoting (cron `shlex`, schtasks/systemd quote) |

## Per-rule detail

### S2083 (BLOCKER) — path traversal into the watch state file
**Was:** `state_path = watch_dir / ".adsb-state.json"` then `write_text(...)`,
with `watch_dir` coming from argv.
**Fix:** `_state_path_for()` resolves the watched directory and the state path
and asserts the state file's parent is exactly the resolved directory. A
planted `.adsb-state.json` symlink pointing outside the dir is now refused
rather than followed. The filename itself is a constant, so this fully closes
the traversal vector.
Tests: `StatePathConfinementTests`.

### S5443 (CRITICAL) — use of a publicly-writable directory
**Was:** `_guess_decoder_dirs()` lists `/tmp/dump1090` (macOS) as a candidate
decoder-output directory. `/tmp` is world-writable, so another local user could
pre-create or symlink it.
**Fix:** the candidate filter now excludes any symlinked path
(`c.is_dir() and not c.is_symlink()`), so a redirected `/tmp/dump1090` is never
suggested. It remains in the list because some legacy dump1090 builds genuinely
write there, but it is only ever an **interactive suggestion the operator must
confirm**, never an automatic destination, and never followed through a symlink.
Tests: `DecoderDirSymlinkTests`.

### S8706 (MAJOR ×2) — database connection built from CLI input
**Was:** `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` — a capture file
named e.g. `x?mode=rwc.sqb` would parse as the file `x` opened `mode=rwc`,
silently dropping the read-only guard.
**Fix:** `_sqlite_ro_uri()` builds the URI with
`urllib.request.pathname2url()`, which percent-encodes `?` and `#` in the path,
so the only query parameter is the trailing `mode=ro` we control. The
emscripten/Pyodide branch (plain `connect(str(path))`, no URI) is unchanged —
the web build drops files into a private MEMFS path no other process can reach,
so URI-mode safety is academic there.
Tests: `SqliteUriTests`.

### S6350 (MAJOR ×2) & S8705 (MAJOR ×1) — command argument / OS command from untrusted data
**Was:** the scheduler renderers interpolated the capture directory and glob
straight into command text:
- **cron** (`render_cron_line`) — the line is executed by `/bin/sh`, so a dir
  like `/data/$(reboot)` would run on every tick.
- **systemd** (`render_systemd_units`) — `ExecStart` word-splits on whitespace
  and treats `%` as a specifier; an unquoted path with spaces breaks the unit.
- **schtasks** (`render_schtasks_create`) — the action string is re-parsed by
  Task Scheduler; a `"` in the path breaks out of its quotes.
**Fix:**
- cron uses `shlex.quote()` on python/script/dir (log path left unquoted so
  `$HOME` still expands).
- systemd uses `_systemd_quote()` — POSIX-normalised, double-quoted, and
  rejects `"`, `%`, `\`, and control characters.
- schtasks uses `_schtasks_arg()` — double-quoted, rejects `"` and control
  characters.
- the glob goes through `_validate_glob()` in all three renderers (whitelist of
  filename + glob metacharacters only).
Newlines are rejected everywhere via `_reject_control_chars()` so a value can't
inject a second crontab/systemd directive.
Tests: `CronQuotingTests`, `SystemdQuotingTests`, `SchtasksQuotingTests`,
`GlobValidationTests`.

### S8707 (MAJOR ×12) & S6549 (MAJOR ×2) — path construction from CLI args
These cover every place argv flows into a `Path(...)` used for I/O: the main
input path, `--out`, `--out-dir`, `--schedule-input`, and the directory passed
into the scheduler renderers.

- **`--schedule-input`** and the renderer paths feed persisted scheduler
  artifacts → these are now normalised with `_user_path()` and quoted/validated
  as described under S6350 above. **Fixed.**
- **The watch state file** path is confined (S2083 above). **Fixed.**
- **`--out` / `--out-dir` / the main input path** are **accept-by-design** for a
  local CLI: by documented contract (`SECURITY.md` → "Output file handling")
  the operator decides exactly where output goes — there is no meaningful
  sandbox root to confine to. They are still **normalised**: `_user_path()`
  expands `~`, collapses `..`/`.` via `resolve()` so the printed absolute path
  is unambiguous, and `_reject_control_chars()` rejects NUL/newline before the
  value touches the filesystem.

The disposition is therefore a mix of *fixed* (anything reaching a persisted
artifact or second-order path) and *accept-by-design with normalisation*
(output destinations an operator intentionally controls). Either way the taint
now passes through a validation/normalisation choke point rather than going
straight from argv to syscall.
Tests: `UserPathTests`, `ControlCharTests` (plus the renderer suites above).

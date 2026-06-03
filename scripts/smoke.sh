#!/usr/bin/env bash
# Pre-release smoke test for Muninn. Runs in CI and locally.
#
# Exercises the contained, deterministic parts of the install path:
#   1. Unit tests, with the live-key safety guard explicitly opted-in
#      (matches the CI invocation, surfaces guard regressions).
#   2. README example linter (catches venv-form drift like the v2.0.8
#      footgun the Pi24 user hit).
#   3. Throwaway venv + pinned-dep install (matches setup.sh flow).
#   4. muninn.py --version + --help sanity.
#
# Live `--schedule` install against the real systemd user manager is
# NOT part of this script — it requires a clean host without a real
# WDGoWars key, and a side-effecting systemctl --user environment.
# That belongs in a pre-release manual checklist, not a script anyone
# can run.
#
# Run from the repo root:
#
#     bash scripts/smoke.sh
#
# Exit: 0 all pass, 1 any failure (fail-fast).

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d -t muninn-smoke-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

say()  { printf "[smoke] %s\n" "$*"; }
fail() { printf "[smoke] FAIL: %s\n" "$*" >&2; exit 1; }
ok()   { printf "[smoke] ok: %s\n" "$*"; }

cd "$REPO_DIR"

# ─── 1. README example linter (stdlib-only, runs without deps) ───
say "linting README examples..."
if python3 scripts/check_readme_examples.py README.md > "$TMP_DIR/lint.log" 2>&1; then
    ok "README clean"
else
    cat "$TMP_DIR/lint.log" >&2
    fail "README linter"
fi

# ─── 2. throwaway venv + pinned deps (tests need gungnir) ───
say "creating throwaway venv at $TMP_DIR/venv..."
if ! python3 -m venv "$TMP_DIR/venv" > "$TMP_DIR/venv.log" 2>&1; then
    cat "$TMP_DIR/venv.log" >&2
    fail "venv create (is python3-venv installed?)"
fi
if [ -x "$TMP_DIR/venv/bin/python" ]; then
    VENV_PY="$TMP_DIR/venv/bin/python"
elif [ -x "$TMP_DIR/venv/Scripts/python.exe" ]; then
    VENV_PY="$TMP_DIR/venv/Scripts/python.exe"
else
    fail "could not find venv python interpreter under $TMP_DIR/venv/"
fi
say "installing pinned deps into venv..."
if ! "$VENV_PY" -m pip install -q -r requirements.txt \
        > "$TMP_DIR/pip.log" 2>&1; then
    tail -20 "$TMP_DIR/pip.log" >&2
    fail "pip install -r requirements.txt"
fi
ok "venv + deps"

# ─── 3. unit tests via venv python, live-key guard explicit ───
say "running unit tests via venv python..."
if MUNINN_TEST_ALLOW_LIVE_KEY=1 "$VENV_PY" -m unittest discover tests/ \
        > "$TMP_DIR/tests.log" 2>&1; then
    ok "tests passed"
else
    tail -20 "$TMP_DIR/tests.log" >&2
    fail "unit tests"
fi

# ─── 4. CLI sanity through the venv python ───
say "muninn.py --version..."
VER=$("$VENV_PY" muninn.py --version 2>&1 | head -1) \
    || fail "--version"
say "  $VER"
"$VENV_PY" muninn.py --help > /dev/null || fail "--help"
ok "--version + --help"

# ─── 5. --schedule headless validation: write unit file to a temp XDG
#       and assert its content. No systemctl interaction (which would
#       need the live user manager). Just renderer round-trip.
#
# Linux-with-systemd only. macOS gets cron and Windows gets schtasks,
# both of which need different assertions and (in the Windows case)
# bump into the schtasks /TR 261-char cap when the temp dir path is
# long. CI runs Linux, so we focus there. Aligned with wigle's gate.
if [ "$(uname -s)" = "Linux" ] && command -v systemctl >/dev/null 2>&1 \
        && [ -d /run/systemd/system ]; then
    say "rendering systemd unit (no install) — XDG-isolated..."
    export XDG_CONFIG_HOME="$TMP_DIR/xdg"
    mkdir -p "$XDG_CONFIG_HOME"
    mkdir -p "$TMP_DIR/captures"
    # Run muninn's headless --schedule but suppress systemctl errors —
    # the unit file write happens BEFORE the systemctl call, so we get
    # the artifact even when systemctl can't find our XDG path.
    "$VENV_PY" muninn.py --schedule --schedule-mode watch \
        --schedule-input "$TMP_DIR/captures" --schedule-glob 'aircraft.json' \
        --schedule-dry-run > "$TMP_DIR/sched.log" 2>&1 || true
    UNIT="$XDG_CONFIG_HOME/systemd/user/muninn-upload.service"
    if [ ! -f "$UNIT" ]; then
        cat "$TMP_DIR/sched.log" >&2
        fail "no unit file written to $UNIT"
    fi
    grep -q "Description=Muninn ADS-B watch+upload \[DRY-RUN\]" "$UNIT" \
        || fail "dry-run marker missing from unit Description"
    grep -q -- "--watch .* --watch-glob 'aircraft.json' --upload --dry-run" "$UNIT" \
        || fail "ExecStart missing expected flags"
    grep -q "# managed-by-muninn" "$UNIT" \
        || fail "marker comment missing from unit"
    ok "unit content correct (dry-run + marker + flags)"
else
    say "(skipping systemd unit smoke — not on a systemd Linux host)"
fi

say "all smoke checks passed"
exit 0

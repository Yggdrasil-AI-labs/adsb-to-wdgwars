"""Muninn test suite.

Safety net: refuse to start the test process if the user has a real
WDGoWars API key configured on this host. Test code that exercises
`--upload` or `--watch` reads the same key from `~/.config/muninn/api.key`
as production runs do — so a stray test invocation on a developer
workstation can post synthetic data to LOCOSP's prod. Happened once on
2026-06-01 (v2.0.9 scheduler E2E test → 2 phantom aircraft on the
operator's live account); the guard exists so it doesn't happen twice.

To run tests with a real key present (e.g. you're explicitly testing
the upload path against a sacrificial account), opt in:

    MUNINN_TEST_ALLOW_LIVE_KEY=1 python -m unittest discover tests/

The guard runs once at import time and only flags the canonical key
path. Other key sources (env var WDGWARS_KEY, --key on the command
line) are out of scope — those require explicit caller intent.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path


def _check_live_key_guard() -> None:
    if os.environ.get("MUNINN_TEST_ALLOW_LIVE_KEY") == "1":
        return
    # Mirror muninn._key_path() without importing muninn (which would
    # trigger gungnir import, which may not be present in a minimal
    # test environment).
    cfg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    key_file = Path(cfg) / "muninn" / "api.key"
    if not key_file.exists():
        return
    sys.stderr.write(
        "\n"
        "================================================================\n"
        " Muninn test suite: live API key detected, refusing to run.\n"
        "================================================================\n"
        f" Found: {key_file}\n"
        "\n"
        " Tests that exercise --upload or --watch will read this key\n"
        " and post synthetic data to LOCOSP's production endpoint.\n"
        " This guard exists to keep test runs from accidentally\n"
        " polluting your real WDGoWars account.\n"
        "\n"
        " To run tests anyway (e.g. you're testing against a\n"
        " sacrificial account on purpose):\n"
        "\n"
        "     MUNINN_TEST_ALLOW_LIVE_KEY=1 python -m unittest discover tests/\n"
        "\n"
        " To run tests with no key risk, move the key aside first:\n"
        "\n"
        f"     mv {key_file} {key_file}.bak\n"
        "================================================================\n"
        "\n"
    )
    sys.exit(2)


_check_live_key_guard()

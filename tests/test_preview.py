"""--preview flag tests.

Added v2.0.10 to mirror Heimdall's --preview after a Pi24 user tried
that flag on Muninn and got `unrecognized arguments`. Behavior contract:
parses the input, prints up to 6 normalised records as JSON-lines on
stdout, returns 0, writes no file, posts no upload.

Run: python -m unittest tests/test_preview.py
"""
from __future__ import annotations
import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402


def _fake_args(**overrides):
    """Build a defaults-everywhere args namespace matching the CLI."""
    base = dict(
        preview=False, upload=False, dry_run=False, stdout=False,
        out=None, out_dir=None, no_save=False, format="auto",
        csv_format=None, sqb_tz=None, open_after=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class PreviewFlagTests(unittest.TestCase):

    def _make_sbs1_fixture(self) -> Path:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8")
        # 8 distinct ICAOs so we can confirm preview caps at 6
        for i, hex_id in enumerate(
                ["A11111", "B22222", "C33333", "D44444",
                 "E55555", "F66666", "111111", "222222"]):
            f.write(
                f"MSG,3,1,1,{hex_id},1,2026/06/01,12:00:0{i},"
                f"2026/06/01,12:00:0{i},TST{i:02d},35000,"
                f"480,270,40.5,-80.5,0,0,0,0,0,0\n"
            )
        f.close()
        return Path(f.name)

    def test_preview_returns_zero_exit(self):
        path = self._make_sbs1_fixture()
        try:
            args = _fake_args(preview=True)
            rc, records = muninn._process_one_file(path, args)
            self.assertEqual(rc, 0)
            self.assertEqual(len(records), 8,
                             "preview shouldn't drop records — just limit "
                             "what's printed")
        finally:
            path.unlink()

    def test_preview_writes_no_file(self):
        path = self._make_sbs1_fixture()
        sibling = path.parent / f"{path.stem}.wdgwars.json"
        # Make sure no stale fixture from a prior run
        if sibling.exists():
            sibling.unlink()
        try:
            args = _fake_args(preview=True)
            with redirect_stdout(io.StringIO()):
                muninn._process_one_file(path, args)
            self.assertFalse(sibling.exists(),
                             f"preview wrote {sibling} — should be no-op")
        finally:
            path.unlink()

    def test_preview_prints_at_most_6_records(self):
        path = self._make_sbs1_fixture()
        try:
            args = _fake_args(preview=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                muninn._process_one_file(path, args)
            lines = [l for l in buf.getvalue().splitlines() if l.strip()]
            self.assertEqual(len(lines), 6,
                             "preview should print exactly 6 records "
                             "(8 in fixture, capped at 6)")
        finally:
            path.unlink()

    def test_preview_each_line_is_valid_json(self):
        path = self._make_sbs1_fixture()
        try:
            args = _fake_args(preview=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                muninn._process_one_file(path, args)
            for line in buf.getvalue().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                self.assertIn("icao", rec)
                self.assertIn("lat", rec)
                self.assertIn("lon", rec)
        finally:
            path.unlink()

    def test_preview_with_fewer_than_6_records(self):
        # 2-aircraft fixture — preview should print just 2, not pad
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8")
        f.write(
            "MSG,3,1,1,AAAAAA,1,2026/06/01,12:00:00,"
            "2026/06/01,12:00:00,T01,35000,480,270,40.5,-80.5,0,0,0,0,0,0\n"
            "MSG,3,1,1,BBBBBB,1,2026/06/01,12:00:01,"
            "2026/06/01,12:00:01,T02,35000,480,270,40.5,-80.5,0,0,0,0,0,0\n"
        )
        f.close()
        path = Path(f.name)
        try:
            args = _fake_args(preview=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                muninn._process_one_file(path, args)
            lines = [l for l in buf.getvalue().splitlines() if l.strip()]
            self.assertEqual(len(lines), 2)
        finally:
            path.unlink()


class PreviewCLITests(unittest.TestCase):
    """End-to-end through the CLI rather than the parser entry point."""

    def test_preview_flag_in_help(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "muninn.py"), "--help"],
            capture_output=True, text=True, timeout=10,
            env={**__import__("os").environ,
                 "MUNINN_TEST_ALLOW_LIVE_KEY": "1"},
        )
        self.assertIn("--preview", result.stdout)


if __name__ == "__main__":
    unittest.main()

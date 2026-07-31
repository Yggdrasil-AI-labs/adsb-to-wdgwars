"""Watch-mode durability tests.

Added v2.1.3, closing the last of the three silent failures from the
2026-07-25 live install (systemd lingering, output beside a root-owned
input, and this one): watch mode pointed at a decoder's own runtime dir
(`/run/readsb`) installed cleanly and then failed every cycle with no
visible signal. The per-file output write and the state-file write both
targeted the watched (root-owned) directory, and the write ran BEFORE the
upload, so the upload never happened either.

Three behaviors under test:

1. `_watch_out_path`. Watch mode now uses the same output resolution
   order as `_process_one_file` (`--out-dir` > configured output folder >
   beside the input, `--no-save` with `--upload` skips the write).
2. `_resolve_watch_state_path`. State stays in the watched dir when it's
   writable (unchanged), and falls back to a per-directory file under the
   muninn config dir when it isn't.
3. `_is_rolling_file_pattern`, the scheduler prompt preselects periodic
   mode when the file pattern names one fixed rolling file, so users are
   no longer asked to classify their own decoder.

Permission failures are simulated with `os.access` / prefs mocks rather
than real unwritable directories, so the suite runs the same everywhere.

Run: python -m unittest tests/test_watch_mode.py
"""
from __future__ import annotations
import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402


def _fake_args(**overrides):
    base = dict(
        preview=False, upload=False, dry_run=False, stdout=False,
        out=None, out_dir=None, no_save=False, format="auto",
        csv_format=None, sqb_tz=None, open_after=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class WatchOutPathTests(unittest.TestCase):
    """--out-dir > configured output folder > beside the input."""

    def setUp(self):
        self.input = Path(tempfile.gettempdir()) / "aircraft.json"

    def test_beside_input_is_the_last_resort(self):
        with mock.patch.object(muninn, "_default_out_dir_from_prefs",
                               return_value=None):
            out = muninn._watch_out_path(self.input, _fake_args())
        self.assertEqual(out, (self.input.parent /
                               "aircraft.wdgwars.json").resolve())

    def test_configured_folder_wins_over_beside_input(self):
        configured = Path(tempfile.mkdtemp())
        with mock.patch.object(muninn, "_default_out_dir_from_prefs",
                               return_value=configured):
            out = muninn._watch_out_path(self.input, _fake_args())
        self.assertEqual(out, (configured / "aircraft.wdgwars.json").resolve())

    def test_out_dir_wins_over_configured_folder(self):
        configured = Path(tempfile.mkdtemp())
        explicit = Path(tempfile.mkdtemp())
        with mock.patch.object(muninn, "_default_out_dir_from_prefs",
                               return_value=configured):
            out = muninn._watch_out_path(
                self.input, _fake_args(out_dir=str(explicit)))
        self.assertEqual(out.parent.resolve(), explicit.resolve())
        self.assertEqual(out.name, "aircraft.wdgwars.json")

    def test_no_save_with_upload_skips_the_write(self):
        out = muninn._watch_out_path(
            self.input, _fake_args(upload=True, no_save=True))
        self.assertIsNone(out)

    def test_no_save_without_upload_still_writes(self):
        # --no-save is documented as an --upload companion; without
        # --upload the local write is the entire point of the run.
        with mock.patch.object(muninn, "_default_out_dir_from_prefs",
                               return_value=None):
            out = muninn._watch_out_path(
                self.input, _fake_args(no_save=True))
        self.assertIsNotNone(out)


class WatchStatePathTests(unittest.TestCase):
    """State lives in the watched dir when writable, config dir when not."""

    def test_writable_dir_keeps_state_in_dir(self):
        d = Path(tempfile.mkdtemp())
        state = muninn._resolve_watch_state_path(d)
        self.assertEqual(state.parent.resolve(), d.resolve())

    def test_unwritable_dir_falls_back_to_config_dir(self):
        d = Path(tempfile.mkdtemp())
        cfg = Path(tempfile.mkdtemp())
        with mock.patch.object(muninn.os, "access", return_value=False), \
             mock.patch.object(muninn, "_config_dir", return_value=cfg):
            state = muninn._resolve_watch_state_path(d)
        self.assertEqual(state.parent.resolve(), cfg.resolve())
        self.assertTrue(state.name.startswith("watch-state-"))
        self.assertTrue(state.name.endswith(".json"))

    def test_fallback_is_stable_per_directory(self):
        d = Path(tempfile.mkdtemp())
        cfg = Path(tempfile.mkdtemp())
        with mock.patch.object(muninn.os, "access", return_value=False), \
             mock.patch.object(muninn, "_config_dir", return_value=cfg):
            first = muninn._resolve_watch_state_path(d)
            second = muninn._resolve_watch_state_path(d)
        self.assertEqual(first, second)

    def test_fallback_differs_between_directories(self):
        cfg = Path(tempfile.mkdtemp())
        d1 = Path(tempfile.mkdtemp())
        d2 = Path(tempfile.mkdtemp())
        with mock.patch.object(muninn.os, "access", return_value=False), \
             mock.patch.object(muninn, "_config_dir", return_value=cfg):
            s1 = muninn._resolve_watch_state_path(d1)
            s2 = muninn._resolve_watch_state_path(d2)
        self.assertNotEqual(s1, s2)


class RollingFilePatternTests(unittest.TestCase):
    """Fixed filename = rolling snapshot = periodic preselected."""

    def test_aircraft_json_is_rolling(self):
        self.assertTrue(muninn._is_rolling_file_pattern("aircraft.json"))

    def test_wildcard_json_is_not_rolling(self):
        self.assertFalse(muninn._is_rolling_file_pattern("*.json"))

    def test_tar1090_chunks_are_not_rolling(self):
        self.assertFalse(muninn._is_rolling_file_pattern("chunk_*.json.gz"))

    def test_ndjson_sessions_are_not_rolling(self):
        self.assertFalse(muninn._is_rolling_file_pattern("*.ndjson.gz"))

    def test_char_class_is_not_rolling(self):
        self.assertFalse(muninn._is_rolling_file_pattern("cap[0-9].txt"))


if __name__ == "__main__":
    unittest.main()

"""Output-path resolution + graceful local-write failure tests.

Added v2.1.2 after a live bug report: `./run.sh /run/readsb/aircraft.json
--upload`, the README's own documented one-shot example, crashed with an
unhandled `PermissionError` writing the local `.wdgwars.json` artifact,
because `/run/readsb` is a root-owned runtime dir the feeder account can't
write to. Three things were wrong, and this file covers all three:

1. An explicit input path ignored the user's configured output folder
   (from the first-run folder prompt) and always fell back to writing
   beside the input file. Fixed resolution order:
   `--out` > `--out-dir` > configured output folder > beside the input.
2. A failed local write took the whole run down, including an in-flight
   `--upload`. Even though the local JSON is only a side artifact and the
   upload is the actual point of running with `--upload`.
3. The failure surfaced as a raw traceback instead of a plain-language
   message naming the path and suggesting `--out-dir`.

Filesystem permission failures are mocked (PermissionError on
`Path.write_text`) rather than requiring a real unwritable directory, so
this suite runs the same on every platform/CI runner.

Run: python -m unittest tests/test_output_resolution.py
"""
from __future__ import annotations
import argparse
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

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


def _sbs1_fixture() -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8")
    f.write(
        "MSG,3,1,1,AAAAAA,1,2026/06/01,12:00:00,"
        "2026/06/01,12:00:00,T01,35000,480,270,40.5,-80.5,0,0,0,0,0,0\n"
    )
    f.close()
    return Path(f.name)


class OutputPathResolutionTests(unittest.TestCase):
    """--out > --out-dir > configured output folder > beside the input."""

    def test_explicit_input_uses_configured_output_folder(self):
        # This is the core regression: before the fix, an explicit input
        # path skipped the configured folder entirely.
        path = _sbs1_fixture()
        sibling = path.parent / f"{path.stem}.wdgwars.json"
        if sibling.exists():
            sibling.unlink()
        try:
            with tempfile.TemporaryDirectory() as configured:
                with mock.patch.object(
                        muninn, "_load_folder_prefs",
                        return_value={"input": "whatever",
                                      "output": configured}):
                    args = _fake_args()
                    rc, records = muninn._process_one_file(path, args)
                expected = Path(configured) / f"{path.stem}.wdgwars.json"
                self.assertEqual(rc, 0)
                self.assertTrue(
                    expected.exists(),
                    "explicit input path should use the configured output "
                    "folder, not default beside the input")
                self.assertFalse(
                    sibling.exists(),
                    "should not have fallen back to beside the input when "
                    "a folder is configured")
        finally:
            path.unlink()
            if sibling.exists():
                sibling.unlink()

    def test_falls_back_beside_input_when_nothing_configured(self):
        # No saved prefs at all -> last-resort behavior must be unchanged.
        path = _sbs1_fixture()
        sibling = path.parent / f"{path.stem}.wdgwars.json"
        if sibling.exists():
            sibling.unlink()
        try:
            with mock.patch.object(
                    muninn, "_load_folder_prefs", return_value=None):
                args = _fake_args()
                rc, records = muninn._process_one_file(path, args)
            self.assertEqual(rc, 0)
            self.assertTrue(
                sibling.exists(),
                "with no configured folder, output should still land "
                "beside the input as the final fallback")
        finally:
            path.unlink()
            if sibling.exists():
                sibling.unlink()

    def test_out_dir_flag_overrides_configured_folder(self):
        path = _sbs1_fixture()
        try:
            with tempfile.TemporaryDirectory() as configured, \
                 tempfile.TemporaryDirectory() as explicit:
                with mock.patch.object(
                        muninn, "_load_folder_prefs",
                        return_value={"input": "x", "output": configured}):
                    args = _fake_args(out_dir=explicit)
                    rc, records = muninn._process_one_file(path, args)
                expected = Path(explicit) / f"{path.stem}.wdgwars.json"
                not_expected = Path(configured) / f"{path.stem}.wdgwars.json"
                self.assertEqual(rc, 0)
                self.assertTrue(expected.exists(),
                                "--out-dir must win over the configured "
                                "output folder")
                self.assertFalse(not_expected.exists())
        finally:
            path.unlink()

    def test_out_flag_overrides_everything(self):
        path = _sbs1_fixture()
        explicit_path = None
        try:
            with tempfile.TemporaryDirectory() as configured:
                with tempfile.NamedTemporaryFile(
                        suffix=".json", delete=False) as tmp:
                    explicit_path = Path(tmp.name)
                with mock.patch.object(
                        muninn, "_load_folder_prefs",
                        return_value={"input": "x", "output": configured}):
                    args = _fake_args(out=str(explicit_path))
                    rc, records = muninn._process_one_file(path, args)
            self.assertEqual(rc, 0)
            self.assertGreater(explicit_path.stat().st_size, 0,
                               "--out must win over everything else")
        finally:
            path.unlink()
            if explicit_path is not None:
                explicit_path.unlink(missing_ok=True)

    def test_no_save_with_upload_still_skips_local_write(self):
        # --no-save must keep meaning "skip the local write" even though a
        # configured output folder now exists to fall back to -- it must
        # not silently start writing again just because a folder is saved.
        path = _sbs1_fixture()
        try:
            with tempfile.TemporaryDirectory() as configured:
                with mock.patch.object(
                        muninn, "_load_folder_prefs",
                        return_value={"input": "x", "output": configured}):
                    args = _fake_args(upload=True, no_save=True)
                    rc, records = muninn._process_one_file(path, args)
                self.assertEqual(rc, 0)
                self.assertEqual(len(records), 1)
                self.assertFalse(
                    any(Path(configured).iterdir()),
                    "--no-save must still skip the local write even when "
                    "an output folder is configured")
        finally:
            path.unlink()


class FailedLocalWriteTests(unittest.TestCase):
    """A permission-denied local write must warn, never traceback, and must
    not abort an in-flight --upload."""

    def test_upload_proceeds_despite_failed_write(self):
        path = _sbs1_fixture()
        try:
            with mock.patch.object(
                    muninn, "_load_folder_prefs", return_value=None), \
                 mock.patch.object(
                    Path, "write_text",
                    side_effect=PermissionError(13, "Permission denied")):
                args = _fake_args(upload=True)
                buf = io.StringIO()
                with redirect_stderr(buf):
                    rc, records = muninn._process_one_file(path, args)
            self.assertEqual(
                rc, 0,
                "a failed local write must not fail the run when --upload "
                "is set -- the upload is the point")
            self.assertEqual(
                len(records), 1,
                "records must still be returned so the caller can upload")
            self.assertIn("WARNING", buf.getvalue())
        finally:
            path.unlink()

    def test_caller_still_invokes_upload_after_failed_write(self):
        # Mirrors main()'s own single-file dispatch logic to prove the
        # end-to-end contract: rc==0 + non-empty records -> upload happens.
        path = _sbs1_fixture()
        try:
            with mock.patch.object(
                    muninn, "_load_folder_prefs", return_value=None), \
                 mock.patch.object(
                    Path, "write_text",
                    side_effect=PermissionError(13, "Permission denied")), \
                 mock.patch.object(
                    muninn, "_do_upload", return_value=0) as mock_upload:
                args = _fake_args(upload=True)
                rc, records = muninn._process_one_file(path, args)
                upload_rc = (
                    muninn._do_upload(records, args)
                    if (args.upload and not args.preview) else 0
                )
            mock_upload.assert_called_once()
            uploaded_records = mock_upload.call_args[0][0]
            self.assertEqual(len(uploaded_records), 1)
            self.assertEqual(upload_rc, 0)
        finally:
            path.unlink()

    def test_fails_cleanly_without_upload(self):
        path = _sbs1_fixture()
        try:
            with mock.patch.object(
                    muninn, "_load_folder_prefs", return_value=None), \
                 mock.patch.object(
                    Path, "write_text",
                    side_effect=PermissionError(13, "Permission denied")):
                args = _fake_args(upload=False)
                buf = io.StringIO()
                with redirect_stderr(buf):
                    rc, records = muninn._process_one_file(path, args)
            self.assertEqual(
                rc, 1,
                "with no --upload, a failed local write is the whole "
                "point of the run and must fail")
            self.assertIn("ERROR", buf.getvalue())
        finally:
            path.unlink()

    def test_friendly_message_not_traceback(self):
        path = _sbs1_fixture()
        try:
            with mock.patch.object(
                    muninn, "_load_folder_prefs", return_value=None), \
                 mock.patch.object(
                    Path, "write_text",
                    side_effect=PermissionError(13, "Permission denied")):
                args = _fake_args(upload=True)
                buf = io.StringIO()
                with redirect_stderr(buf):
                    muninn._process_one_file(path, args)
            out = buf.getvalue()
            self.assertNotIn("Traceback", out)
            self.assertNotIn("PermissionError", out,
                             "should be a friendly message, not the "
                             "exception's own repr")
            self.assertIn("--out-dir", out,
                          "friendly message should point at the escape "
                          "hatch")
            self.assertIn("not writable", out)
            expected_path = str(
                (path.parent / f"{path.stem}.wdgwars.json").resolve())
            self.assertIn(expected_path, out,
                          "message should name the exact path that failed")
        finally:
            path.unlink()

    def test_generic_oserror_also_handled_gracefully(self):
        path = _sbs1_fixture()
        try:
            with mock.patch.object(
                    muninn, "_load_folder_prefs", return_value=None), \
                 mock.patch.object(
                    Path, "write_text",
                    side_effect=OSError(28, "No space left on device")):
                args = _fake_args(upload=False)
                buf = io.StringIO()
                with redirect_stderr(buf):
                    rc, records = muninn._process_one_file(path, args)
            self.assertEqual(rc, 1)
            self.assertNotIn("Traceback", buf.getvalue())
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()

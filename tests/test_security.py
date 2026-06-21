"""Security-hardening regression tests.

Covers the path/argument-safety helpers and the call-site hardening added
when the SonarCloud SAST findings on muninn.py were reviewed and fixed
(see SECURITY-FINDINGS.md for the per-finding disposition). All tests are
pure / filesystem-local: nothing here uploads, installs a real scheduler
entry, or touches the network.

Run: python -m unittest tests/test_security.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402


PY = "/usr/bin/python3"
SCRIPT = Path("/opt/adsb-to-wdgwars/muninn.py")


class GlobValidationTests(unittest.TestCase):
    """_validate_glob — applied wherever a pattern is baked into a persisted
    scheduler command (S6350)."""

    def test_accepts_real_decoder_globs(self):
        for good in ("aircraft.json", "chunk_*.json.gz", "*.ndjson.gz",
                     "*.json", "*.txt", "session_[0-9].ndjson"):
            self.assertEqual(muninn._validate_glob(good), good)

    def test_rejects_shell_metacharacters(self):
        for bad in ("*.txt; rm -rf ~", "$(reboot)", "`id`", "a|b",
                    "a>b", "a&b"):
            with self.assertRaises(muninn._UnsafeInput):
                muninn._validate_glob(bad)

    def test_rejects_quotes_spaces_and_separators(self):
        for bad in ('a"b', "a'b", "a b", "../etc/passwd", "sub/dir",
                    "back\\slash"):
            with self.assertRaises(muninn._UnsafeInput):
                muninn._validate_glob(bad)

    def test_rejects_empty(self):
        with self.assertRaises(muninn._UnsafeInput):
            muninn._validate_glob("")

    def test_unsafe_input_is_a_valueerror(self):
        # Renderers historically raise ValueError; the subclass keeps that
        # contract so existing assertRaises(ValueError) callers still catch it.
        self.assertTrue(issubclass(muninn._UnsafeInput, ValueError))


class ControlCharTests(unittest.TestCase):

    def test_clean_value_passes_through(self):
        self.assertEqual(
            muninn._reject_control_chars("/some/path", "x"), "/some/path")

    def test_rejects_nul_cr_lf(self):
        for bad in ("a\x00b", "a\nb", "a\rb"):
            with self.assertRaises(muninn._UnsafeInput):
                muninn._reject_control_chars(bad, "x")


class UserPathTests(unittest.TestCase):

    def test_expands_user(self):
        p = muninn._user_path("~", label="home")
        self.assertEqual(p, Path.home().resolve())

    def test_collapses_traversal(self):
        # resolve() canonicalises .. so downstream code sees one location.
        p = muninn._user_path("a/b/../c", resolve=True)
        self.assertNotIn("..", p.parts)
        self.assertEqual(p.name, "c")

    def test_rejects_control_chars(self):
        with self.assertRaises(muninn._UnsafeInput):
            muninn._user_path("a\nb")

    def test_no_resolve_keeps_relative(self):
        p = muninn._user_path("a/b", resolve=False)
        self.assertFalse(p.is_absolute())


class StatePathConfinementTests(unittest.TestCase):
    """_state_path_for — the watch-mode S2083 BLOCKER. The state file must
    stay inside the watched directory."""

    def test_state_file_is_inside_watched_dir(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            state = muninn._state_path_for(base)
            self.assertEqual(state.parent, base.resolve())
            self.assertEqual(state.name, ".adsb-state.json")

    def test_symlinked_state_file_is_rejected(self):
        # A planted `.adsb-state.json` symlink pointing OUTSIDE the watched
        # dir must be refused, not followed. The target lives in a separate
        # temp dir so the resolved parent genuinely differs from the watched
        # dir (a target inside the dir would resolve back to it and be safe).
        with tempfile.TemporaryDirectory() as d, \
                tempfile.TemporaryDirectory() as other:
            base = Path(d).resolve()
            outside = Path(other).resolve() / "evil.json"
            outside.write_text("{}")
            link = base / ".adsb-state.json"
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError, AttributeError):
                self.skipTest("symlink creation not permitted on this host")
            with self.assertRaises(muninn._UnsafeInput):
                muninn._state_path_for(base)


class SqliteUriTests(unittest.TestCase):
    """_sqlite_ro_uri — a filename can't override the read-only mode (S8706)."""

    def test_appends_read_only_mode(self):
        uri = muninn._sqlite_ro_uri(Path("BaseStation.sqb"))
        self.assertTrue(uri.startswith("file:"))
        self.assertTrue(uri.endswith("?mode=ro"))

    def test_question_mark_in_name_is_encoded(self):
        # `x?mode=rwc.sqb` must NOT yield a second, attacker-chosen query
        # parameter — the only ?<query> in the URI is our trailing mode=ro.
        uri = muninn._sqlite_ro_uri(Path("x?mode=rwc.sqb"))
        # Exactly one literal '?' — ours — so the only query param is mode=ro.
        self.assertEqual(uri.count("?"), 1)
        self.assertTrue(uri.endswith("?mode=ro"))
        self.assertIn("%3F", uri)  # the filename's literal ? got encoded
        self.assertNotIn("mode=rwc", uri.split("?")[1])  # never in the query

    def test_hash_in_name_is_encoded(self):
        uri = muninn._sqlite_ro_uri(Path("a#b.sqb"))
        self.assertNotIn("#", uri)
        self.assertIn("%23", uri)


class CronQuotingTests(unittest.TestCase):
    """render_cron_line — cron pipes each line through /bin/sh (S6350)."""

    def test_metacharacter_dir_is_quoted(self):
        import shlex
        d = Path("/data/$(reboot)")
        line = muninn.render_cron_line(d, 5, PY, SCRIPT)
        # The path must appear exactly as shlex.quote would render it, i.e.
        # wrapped in quotes so cron's /bin/sh treats it as one literal token
        # and never expands the $(...) substitution. (str(d) so the assertion
        # is consistent with the host's path separator under test.)
        self.assertIn(shlex.quote(str(d)), line)
        self.assertNotIn(f" {d} --upload", line)  # never a bare, unquoted token

    def test_plain_dir_unquoted_for_readability(self):
        # shlex.quote leaves a clean path untouched, so existing units render
        # byte-identically to before the hardening.
        d = Path("/run/dump1090-fa")
        line = muninn.render_cron_line(d, 5, PY, SCRIPT)
        self.assertIn(str(d), line)

    def test_newline_in_dir_is_rejected(self):
        with self.assertRaises(muninn._UnsafeInput):
            muninn.render_cron_line(Path("/data\nMAILTO=evil"), 5, PY, SCRIPT)


class SystemdQuotingTests(unittest.TestCase):
    """render_systemd_units — ExecStart word-splits and honours % (S6350)."""

    def test_dir_with_space_is_single_quoted_arg(self):
        units = muninn.render_systemd_units(
            "watch", Path("/srv/adsb captures"), "aircraft.json", 5,
            PY, SCRIPT)
        self.assertIn('--watch "/srv/adsb captures"', units["service"])

    def test_double_quote_in_dir_is_rejected(self):
        with self.assertRaises(muninn._UnsafeInput):
            muninn.render_systemd_units(
                'watch', Path('/srv/a"b'), "aircraft.json", 5, PY, SCRIPT)

    def test_percent_in_dir_is_rejected(self):
        # % is a systemd specifier — must not reach a rendered unit unguarded.
        with self.assertRaises(muninn._UnsafeInput):
            muninn.render_systemd_units(
                "watch", Path("/srv/a%b"), "aircraft.json", 5, PY, SCRIPT)

    def test_unsafe_glob_is_rejected(self):
        with self.assertRaises(muninn._UnsafeInput):
            muninn.render_systemd_units(
                "watch", Path("/run/dump1090-fa"), "*.json; rm -rf ~", 5,
                PY, SCRIPT)


class SchtasksQuotingTests(unittest.TestCase):
    """render_schtasks_create — action string re-parsed by Task Scheduler."""

    PYW = r"C:\Python311\python.exe"
    SCRIPTW = Path(r"C:\Tools\adsb-to-wdgwars\muninn.py")

    def test_double_quote_in_dir_is_rejected(self):
        with self.assertRaises(muninn._UnsafeInput):
            muninn.render_schtasks_create(
                "watch", Path('C:\\a"b'), "aircraft.json", 5,
                self.PYW, self.SCRIPTW)

    def test_unsafe_glob_is_rejected(self):
        with self.assertRaises(muninn._UnsafeInput):
            muninn.render_schtasks_create(
                "periodic", Path(r"C:\dump1090"), "*.json & calc.exe", 5,
                self.PYW, self.SCRIPTW)

    def test_safe_inputs_still_render(self):
        cmd = muninn.render_schtasks_create(
            "watch", Path(r"C:\dump1090"), "aircraft.json", 5,
            self.PYW, self.SCRIPTW)
        i = cmd.index("/TR")
        action = cmd[i + 1]
        self.assertIn(r'"C:\dump1090"', action)
        self.assertIn('--watch-glob "aircraft.json"', action)


class DecoderDirSymlinkTests(unittest.TestCase):
    """_guess_decoder_dirs excludes symlinked candidates (S5443)."""

    def test_returns_only_real_non_symlink_dirs(self):
        # Smoke test: whatever this host has, every returned path is a real
        # directory and not a symlink.
        for p in muninn._guess_decoder_dirs():
            self.assertTrue(p.is_dir())
            self.assertFalse(p.is_symlink())


if __name__ == "__main__":
    unittest.main()

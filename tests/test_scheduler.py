"""Scheduler renderer tests.

Pure-function tests for the systemd / cron / schtasks renderers added
in v2.0.9. No side effects: never writes a real unit, never touches
crontab, never invokes schtasks. The installer functions (which DO
touch the system) are exercised live during release verification on
Ubuntu 24.04 / Windows / macOS.

LingerTests below is the exception: it covers the v2.1.1 systemd-lingering
check/enable step with `subprocess.run`, `shutil.which`, and `getpass.getuser`
all mocked. Real `loginctl` is never invoked by this suite either.

Run: python -m unittest tests/test_scheduler.py
"""
from __future__ import annotations
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402


PY = "/usr/bin/python3"
SCRIPT = Path("/opt/adsb-to-wdgwars/muninn.py")
INPUT = Path("/run/dump1090-fa")
GLOB = "aircraft.json"


class SystemdRendererTests(unittest.TestCase):

    def test_watch_returns_service_only_no_timer(self):
        units = muninn.render_systemd_units(
            "watch", INPUT, GLOB, 5, PY, SCRIPT)
        self.assertIn("service", units)
        self.assertIn("timer", units)
        self.assertIsNone(units["timer"],
                          "watch mode runs as long-lived service, "
                          "shouldn't generate a timer")

    def test_watch_service_uses_watch_flags(self):
        units = muninn.render_systemd_units(
            "watch", INPUT, GLOB, 5, PY, SCRIPT)
        # Path slash style follows the test runner's platform, assert on
        # the platform-agnostic substrings instead of a hardcoded form.
        self.assertIn("--watch ", units["service"])
        self.assertIn(INPUT.name, units["service"])
        self.assertIn("--watch-glob 'aircraft.json'", units["service"])
        self.assertIn("--upload", units["service"])

    def test_watch_service_restarts_on_failure(self):
        units = muninn.render_systemd_units(
            "watch", INPUT, GLOB, 5, PY, SCRIPT)
        self.assertIn("Restart=on-failure", units["service"])

    def test_periodic_returns_service_and_timer(self):
        units = muninn.render_systemd_units(
            "periodic", INPUT, GLOB, 15, PY, SCRIPT)
        self.assertIsNotNone(units["service"])
        self.assertIsNotNone(units["timer"])

    def test_periodic_service_is_oneshot(self):
        units = muninn.render_systemd_units(
            "periodic", INPUT, GLOB, 15, PY, SCRIPT)
        self.assertIn("Type=oneshot", units["service"])
        # Watch flags must NOT appear on the one-shot service.
        self.assertNotIn("--watch", units["service"])

    def test_periodic_service_uses_no_save(self):
        # Periodic mode targets rolling files in runtime dirs (e.g.
        # /run/readsb) the feeder user can't write to. The one-shot service
        # must upload from memory and skip the local audit-trail write.
        units = muninn.render_systemd_units(
            "periodic", INPUT, GLOB, 15, PY, SCRIPT)
        self.assertIn("--no-save", units["service"])

    def test_watch_service_does_not_use_no_save(self):
        # Watch mode manages files in a user-writable dir, so the audit
        # JSON write is fine. --no-save must not leak into watch.
        units = muninn.render_systemd_units(
            "watch", INPUT, GLOB, 5, PY, SCRIPT)
        self.assertNotIn("--no-save", units["service"])

    def test_periodic_timer_interval(self):
        units = muninn.render_systemd_units(
            "periodic", INPUT, GLOB, 15, PY, SCRIPT)
        self.assertIn("OnUnitActiveSec=15min", units["timer"])
        self.assertIn("Persistent=true", units["timer"])

    def test_periodic_timer_30_minutes(self):
        units = muninn.render_systemd_units(
            "periodic", INPUT, GLOB, 30, PY, SCRIPT)
        self.assertIn("OnUnitActiveSec=30min", units["timer"])

    def test_marker_present_for_uninstall(self):
        # Every Muninn-managed unit needs the marker for clean uninstall
        for mode, interval in (("watch", 5), ("periodic", 5)):
            units = muninn.render_systemd_units(
                mode, INPUT, GLOB, interval, PY, SCRIPT)
            self.assertIn(muninn.SCHEDULE_MARKER, units["service"],
                          f"{mode} service missing marker")
            if units["timer"] is not None:
                self.assertIn(muninn.SCHEDULE_MARKER, units["timer"],
                              f"{mode} timer missing marker")

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            muninn.render_systemd_units(
                "nonsense", INPUT, GLOB, 5, PY, SCRIPT)


class CronRendererTests(unittest.TestCase):

    def test_default_5min_interval(self):
        line = muninn.render_cron_line(INPUT, 5, PY, SCRIPT)
        self.assertTrue(line.startswith("*/5 * * * *"),
                        f"unexpected cron start: {line!r}")

    def test_1min_uses_star_not_slash(self):
        # `*/1 * * * *` is valid but `* * * * *` is more idiomatic
        line = muninn.render_cron_line(INPUT, 1, PY, SCRIPT)
        self.assertTrue(line.startswith("* * * * *"),
                        f"1min interval should be plain '*', got: {line!r}")

    def test_15min_interval(self):
        line = muninn.render_cron_line(INPUT, 15, PY, SCRIPT)
        self.assertTrue(line.startswith("*/15 * * * *"))

    def test_includes_python_and_script(self):
        line = muninn.render_cron_line(INPUT, 5, PY, SCRIPT)
        self.assertIn(PY, line)
        self.assertIn(str(SCRIPT), line)
        self.assertIn(str(INPUT), line)
        self.assertIn("--upload", line)

    def test_includes_marker_for_uninstall(self):
        line = muninn.render_cron_line(INPUT, 5, PY, SCRIPT)
        self.assertIn(muninn.SCHEDULE_MARKER, line)

    def test_includes_no_save(self):
        # cron is periodic-only against runtime dirs. Must skip the local
        # write so an unwritable /run/<decoder> dir doesn't fail every tick.
        line = muninn.render_cron_line(INPUT, 5, PY, SCRIPT)
        self.assertIn("--no-save", line)

    def test_logs_to_user_home(self):
        line = muninn.render_cron_line(INPUT, 5, PY, SCRIPT)
        self.assertIn(">> $HOME/.muninn-cron.log 2>&1", line)


class SchtasksRendererTests(unittest.TestCase):

    PYW = r"C:\Python311\python.exe"
    SCRIPTW = Path(r"C:\Tools\adsb-to-wdgwars\muninn.py")
    INPUTW = Path(r"C:\Tools\dump1090")
    GLOBW = "aircraft.json"

    def test_watch_uses_onstart(self):
        cmd = muninn.render_schtasks_create(
            "watch", self.INPUTW, self.GLOBW, 5, self.PYW, self.SCRIPTW)
        self.assertEqual(cmd[0], "schtasks")
        self.assertEqual(cmd[1], "/Create")
        self.assertIn("ONSTART", cmd)
        self.assertNotIn("MINUTE", cmd)

    def test_watch_uses_watch_task_name(self):
        cmd = muninn.render_schtasks_create(
            "watch", self.INPUTW, self.GLOBW, 5, self.PYW, self.SCRIPTW)
        i = cmd.index("/TN")
        self.assertEqual(cmd[i + 1], muninn.WINDOWS_TASK_WATCH)

    def test_periodic_uses_minute_with_modifier(self):
        cmd = muninn.render_schtasks_create(
            "periodic", self.INPUTW, self.GLOBW, 10, self.PYW, self.SCRIPTW)
        self.assertIn("MINUTE", cmd)
        # /MO 10 sets the every-10-minutes modifier
        i = cmd.index("/MO")
        self.assertEqual(cmd[i + 1], "10")

    def test_periodic_uses_periodic_task_name(self):
        cmd = muninn.render_schtasks_create(
            "periodic", self.INPUTW, self.GLOBW, 5, self.PYW, self.SCRIPTW)
        i = cmd.index("/TN")
        self.assertEqual(cmd[i + 1], muninn.WINDOWS_TASK_PERIODIC)

    def test_periodic_action_no_watch_flags(self):
        cmd = muninn.render_schtasks_create(
            "periodic", self.INPUTW, self.GLOBW, 5, self.PYW, self.SCRIPTW)
        i = cmd.index("/TR")
        action = cmd[i + 1]
        self.assertNotIn("--watch", action)
        self.assertIn("--upload", action)

    def test_periodic_action_uses_no_save(self):
        cmd = muninn.render_schtasks_create(
            "periodic", self.INPUTW, self.GLOBW, 5, self.PYW, self.SCRIPTW)
        i = cmd.index("/TR")
        action = cmd[i + 1]
        self.assertIn("--no-save", action)

    def test_force_flag_for_idempotent_overwrite(self):
        # /F lets schtasks /Create replace an existing task without
        # prompting, required for idempotency on re-run.
        cmd = muninn.render_schtasks_create(
            "periodic", self.INPUTW, self.GLOBW, 5, self.PYW, self.SCRIPTW)
        self.assertIn("/F", cmd)

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            muninn.render_schtasks_create(
                "nonsense", self.INPUTW, self.GLOBW, 5, self.PYW, self.SCRIPTW)


class DryRunRendererTests(unittest.TestCase):
    """The --dry-run flag must appear in every install variant when
    dry_run=True. When False, --dry-run must NOT leak in."""

    PYW = r"C:\Python311\python.exe"
    SCRIPTW = Path(r"C:\Tools\adsb-to-wdgwars\muninn.py")

    def test_systemd_watch_dry_run_appends_flag(self):
        units = muninn.render_systemd_units(
            "watch", INPUT, GLOB, 5, PY, SCRIPT, dry_run=True)
        self.assertIn("--dry-run", units["service"])
        self.assertIn("[DRY-RUN]", units["service"])

    def test_systemd_watch_no_dry_run_no_flag(self):
        units = muninn.render_systemd_units(
            "watch", INPUT, GLOB, 5, PY, SCRIPT, dry_run=False)
        self.assertNotIn("--dry-run", units["service"])
        self.assertNotIn("[DRY-RUN]", units["service"])

    def test_systemd_periodic_dry_run(self):
        units = muninn.render_systemd_units(
            "periodic", INPUT, GLOB, 5, PY, SCRIPT, dry_run=True)
        self.assertIn("--dry-run", units["service"])
        # Timer doesn't carry the dry-run flag. Only the service does
        self.assertNotIn("--dry-run", units["timer"])

    def test_systemd_default_no_dry_run(self):
        # Backwards-compat: existing callers passing no dry_run kwarg
        # get the original behavior (no flag).
        units = muninn.render_systemd_units(
            "watch", INPUT, GLOB, 5, PY, SCRIPT)
        self.assertNotIn("--dry-run", units["service"])

    def test_cron_dry_run(self):
        line = muninn.render_cron_line(INPUT, 5, PY, SCRIPT, dry_run=True)
        self.assertIn("--upload --dry-run", line)

    def test_cron_no_dry_run(self):
        line = muninn.render_cron_line(INPUT, 5, PY, SCRIPT, dry_run=False)
        self.assertNotIn("--dry-run", line)

    def test_schtasks_watch_dry_run(self):
        cmd = muninn.render_schtasks_create(
            "watch", Path(r"C:\dump1090"), "aircraft.json", 5,
            self.PYW, self.SCRIPTW, dry_run=True)
        # Action is one of the elements after /TR
        i = cmd.index("/TR")
        action = cmd[i + 1]
        self.assertIn("--dry-run", action)

    def test_schtasks_periodic_dry_run(self):
        cmd = muninn.render_schtasks_create(
            "periodic", Path(r"C:\dump1090"), "aircraft.json", 5,
            self.PYW, self.SCRIPTW, dry_run=True)
        i = cmd.index("/TR")
        action = cmd[i + 1]
        self.assertIn("--upload --dry-run", action)

    def test_schtasks_no_dry_run(self):
        cmd = muninn.render_schtasks_create(
            "periodic", Path(r"C:\dump1090"), "aircraft.json", 5,
            self.PYW, self.SCRIPTW, dry_run=False)
        i = cmd.index("/TR")
        action = cmd[i + 1]
        self.assertNotIn("--dry-run", action)


class GuessGlobTests(unittest.TestCase):

    def test_returns_aircraft_json_default(self):
        # On a non-existent path we still return a safe default
        result = muninn.muninn._guess_glob_for_dir if hasattr(muninn, "muninn") \
            else muninn._guess_glob_for_dir
        glob = result(Path("/nonexistent/path"))
        self.assertEqual(glob, "aircraft.json")


def _cp(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class LingerTests(unittest.TestCase):
    """v2.1.1: `--schedule`'s systemd branch checks/enables lingering so
    the installed unit survives logout/reboot. Every scenario mocks
    subprocess.run / shutil.which. No real loginctl call is made."""

    def setUp(self):
        self._getuser = mock.patch.object(
            muninn.getpass, "getuser", return_value="pilot").start()
        self.addCleanup(mock.patch.stopall)

    def test_already_enabled_takes_no_action(self):
        with mock.patch.object(muninn.shutil, "which", return_value="/usr/bin/loginctl"), \
             mock.patch.object(muninn.subprocess, "run",
                               return_value=_cp("Linger=yes\n")) as run, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = muninn.ensure_linger_enabled()
        self.assertTrue(result)
        # Only the read-only query, never an enable-linger call.
        run.assert_called_once()
        self.assertIn("show-user", run.call_args.args[0])
        self.assertIn("already enabled", err.getvalue())

    def test_successful_unprivileged_enable(self):
        responses = [_cp("Linger=no\n"),      # initial check
                    _cp(returncode=0),        # enable-linger
                    _cp("Linger=yes\n")]      # re-verify
        with mock.patch.object(muninn.shutil, "which", return_value="/usr/bin/loginctl"), \
             mock.patch.object(muninn.subprocess, "run",
                               side_effect=responses) as run, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = muninn.ensure_linger_enabled()
        self.assertTrue(result)
        self.assertEqual(run.call_count, 3)
        self.assertIn("enable-linger", run.call_args_list[1].args[0])
        self.assertIn("enabled linger", err.getvalue())
        self.assertNotIn("WARNING", err.getvalue())

    def test_failed_enable_prints_prominent_warning_with_sudo_command(self):
        # enable-linger reports rc=1 (e.g. polkit denied it without root),
        # and the re-verify confirms it did NOT take effect.
        responses = [_cp("Linger=no\n"),
                    _cp(returncode=1, stderr="Interactive authentication required."),
                    _cp("Linger=no\n")]
        with mock.patch.object(muninn.shutil, "which", return_value="/usr/bin/loginctl"), \
             mock.patch.object(muninn.subprocess, "run", side_effect=responses), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = muninn.ensure_linger_enabled()
        self.assertFalse(result)
        out = err.getvalue()
        self.assertIn("WARNING", out)
        self.assertIn("sudo loginctl enable-linger pilot", out)

    def test_enable_exit_code_zero_is_not_trusted_without_reverify(self):
        # The critical correctness case: enable-linger EXITS 0 but the
        # property still reads "no" on re-check. Must warn, not report
        # success. A bare exit-code check would get this wrong.
        responses = [_cp("Linger=no\n"),
                    _cp(returncode=0),          # looks like success...
                    _cp("Linger=no\n")]         # ...but didn't actually take
        with mock.patch.object(muninn.shutil, "which", return_value="/usr/bin/loginctl"), \
             mock.patch.object(muninn.subprocess, "run", side_effect=responses), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = muninn.ensure_linger_enabled()
        self.assertFalse(result)
        self.assertIn("WARNING", err.getvalue())

    def test_loginctl_vanishes_between_check_and_enable_attempt(self):
        # Rare race: loginctl was present for the initial query but the
        # enable-linger call itself raises FileNotFoundError. Must degrade
        # to the warning, not crash the schedule install.
        responses = [_cp("Linger=no\n"), FileNotFoundError()]
        with mock.patch.object(muninn.shutil, "which", return_value="/usr/bin/loginctl"), \
             mock.patch.object(muninn.subprocess, "run", side_effect=responses), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = muninn.ensure_linger_enabled()
        self.assertFalse(result)
        self.assertIn("WARNING", err.getvalue())

    def test_loginctl_missing_degrades_to_warning_without_crashing(self):
        with mock.patch.object(muninn.shutil, "which", return_value=None), \
             mock.patch.object(muninn.subprocess, "run") as run, \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = muninn.ensure_linger_enabled()
        self.assertFalse(result)
        run.assert_not_called()
        out = err.getvalue()
        self.assertIn("WARNING", out)
        self.assertIn("loginctl not found", out)
        self.assertIn("sudo loginctl enable-linger pilot", out)

    def test_ambiguous_current_state_warns_rather_than_guessing(self):
        # loginctl present and exits 0, but output doesn't parse as
        # Linger=yes/no. Must not assume either way.
        with mock.patch.object(muninn.shutil, "which", return_value="/usr/bin/loginctl"), \
             mock.patch.object(muninn.subprocess, "run",
                               return_value=_cp("unexpected garbage\n")), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = muninn.ensure_linger_enabled()
        self.assertFalse(result)
        self.assertIn("WARNING", err.getvalue())

    def test_linger_state_helper_handles_missing_binary_directly(self):
        with mock.patch.object(muninn.subprocess, "run",
                               side_effect=FileNotFoundError):
            self.assertIsNone(muninn._linger_state("pilot"))

    def test_linger_state_helper_handles_nonzero_exit(self):
        with mock.patch.object(muninn.subprocess, "run",
                               return_value=_cp(returncode=1)):
            self.assertIsNone(muninn._linger_state("pilot"))


if __name__ == "__main__":
    unittest.main()

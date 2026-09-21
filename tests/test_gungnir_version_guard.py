"""The pinned-gungnir guard.

requirements.txt pins an exact gungnir release so a fresh install runs the
bytes this Muninn was tested against. Nothing enforced that at runtime, and
an older copy already in site-packages wins silently.

2026-09-20: a machine ran gungnir 0.1.0 against a v0.1.6 pin. 0.1.0 has no
check_deliberate_skip, so every re-upload of a payload the server had
already seen came back as a failed upload -- 43 of 109 runs in one August
sample. The symptom looks like a server fault and took hours to trace to an
import path. This guard turns that into one line at startup.

What is held down here:

1. An older gungnir warns, and the message names the import path, because
   "which copy" is the actual question when this happens.
2. The pinned version, and anything newer, says nothing.
3. An unreadable version on either side says nothing rather than guessing.
4. It warns, never exits. Our opinion of someone's site-packages must not
   be the thing that stops their feeder uploading.

Run: python -m unittest tests/test_gungnir_version_guard.py
"""
from __future__ import annotations
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402


class GungnirVersionGuardTests(unittest.TestCase):
    def _run(self, version):
        buf = io.StringIO()
        with mock.patch.object(muninn.gungnir, "__version__", version,
                               create=True):
            with redirect_stderr(buf):
                muninn._check_gungnir_version()
        return buf.getvalue()

    def test_older_gungnir_warns_and_names_the_path(self):
        out = self._run("0.1.0")
        self.assertIn("0.1.0", out)
        self.assertIn(muninn.REQUIRED_GUNGNIR, out)
        self.assertIn("Imported from:", out,
                      "the whole problem is WHICH copy is loaded")
        self.assertIn("requirements.txt", out, "say how to fix it")

    def test_pinned_version_is_silent(self):
        self.assertEqual(self._run(muninn.REQUIRED_GUNGNIR), "")

    def test_newer_version_is_silent(self):
        self.assertEqual(self._run("0.2.0"), "")
        self.assertEqual(self._run("1.0.0"), "")

    def test_unreadable_version_is_silent(self):
        for bad in ("", "unknown", "not-a-version"):
            with self.subTest(version=bad):
                self.assertEqual(self._run(bad), "")

    def test_guard_never_exits(self):
        # A feeder must keep uploading whatever we think of its deps.
        try:
            self._run("0.0.1")
        except SystemExit as e:
            self.fail(f"the guard must warn, not exit (raised {e!r})")

    def test_a_real_run_fires_the_guard(self):
        # Everything above calls the function directly, which says nothing
        # about whether main() ever reaches it. --show-config is the
        # cheapest real run: no network, no input file.
        buf = io.StringIO()
        with mock.patch.object(muninn, "REQUIRED_GUNGNIR", "9.9.9"):
            with mock.patch.object(sys, "argv",
                                   ["muninn.py", "--config",
                                    "--no-version-check"]):
                with redirect_stderr(buf):
                    try:
                        muninn.main()
                    except SystemExit:
                        pass
        self.assertIn("9.9.9", buf.getvalue(),
                      "main() must run the guard before doing any work")

    def test_required_version_matches_the_pin(self):
        # The guard is worthless if it drifts from the pin it enforces.
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn(f"/v{muninn.REQUIRED_GUNGNIR}.tar.gz", req,
                      "REQUIRED_GUNGNIR and the requirements.txt pin must "
                      "name the same release")


if __name__ == "__main__":
    unittest.main()

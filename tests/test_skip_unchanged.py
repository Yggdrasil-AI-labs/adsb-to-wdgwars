"""Already-sent gate: don't spend a sync on data the server already has.

A fixed station on a timer re-uploads the same aircraft every cycle. The
server counts those as syncs carrying nothing new, and the Uplink page now
says so ("N syncs in a row with nothing new in them"). Reported by a feeder
operator 2026-09-20 with a streak of 28, which is seven hours at his
15-minute interval: an overnight window, not a fault.

v2.6.0 sends only the aircraft that are not held, and holds everything in
an accepted upload for gungnir.holds.ACCEPTED_TTL (30 days). Up to v2.5.1
one unheld aircraft sent the whole snapshot, which on a busy receiver
meant the gate almost never fired. What these tests hold down:

1. A repeat payload skips the POST entirely; one new ICAO in it sends
   only that ICAO.
2. The hold lasts a month and then expires, so suppression is never
   permanent.
3. A record with no ICAO is never suppressed (we upload rather than drop
   an aircraft over a bookkeeping key we couldn't read).
4. --dry-run neither reads nor writes the state.
5. --no-skip-unchanged restores the old full-snapshot behavior.
6. A failed upload records nothing, so it is retried.

The transport is mocked throughout -- these tests must never touch the
network or the operator's real config dir.

Run: python -m unittest tests/test_skip_unchanged.py
"""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402


def rec(icao: str, lat: float = 32.9, lon: float = -117.2) -> dict:
    return muninn._norm_record(icao, lat=lat, lon=lon)


class SkipUnchangedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "holds.json"
        # The gate lives in gungnir.holds now. Point its state file at a
        # temp dir: no test may touch the operator's real config dir.
        p = mock.patch.object(muninn.gungnir.holds, "_path",
                              side_effect=lambda tool: self.state)
        p.start()
        self.addCleanup(p.stop)
        # Isolation floor: no test may read the operator's real hwm.json.
        # _upload's own patch overrides this per call.
        h = mock.patch.object(muninn.gungnir.hwm, "read", return_value=None)
        h.start()
        self.addCleanup(h.stop)
        self.addCleanup(self._tmp.cleanup)

    def _upload(self, records, **kw):
        """One upload with the transport stubbed to succeed."""
        with mock.patch.object(muninn.gungnir.transport, "send",
                               return_value=0) as send:
            rc = muninn.upload(records, "key", "https://example.invalid", **kw)
        return rc, send

    def _at(self, offset):
        """Patch muninn's clock to ``offset`` seconds from now."""
        return mock.patch.object(muninn.time, "time",
                                 return_value=__import__("time").time() + offset)

    def test_repeat_payload_skips_the_post(self):
        records = [rec("ABC123"), rec("DEF456")]
        rc, send = self._upload(records)
        self.assertEqual(rc, 0)
        self.assertEqual(send.call_count, 1, "first upload must be sent")

        rc, send = self._upload(records)
        self.assertEqual(rc, 0)
        self.assertEqual(send.call_count, 0,
                         "a payload of only already-sent aircraft must not "
                         "reach the transport at all")

    def test_one_new_aircraft_sends_only_that_aircraft(self):
        # The v2.6.0 change. A busy receiver always has a newcomer, and
        # sending the whole snapshot for it re-offered ~140 aircraft the
        # server already had, every cycle.
        first = [rec("ABC123"), rec("DEF456")]
        self._upload(first)

        rc, send = self._upload(first + [rec("999AAA")])
        self.assertEqual(rc, 0)
        self.assertEqual(send.call_count, 1)
        sent = [r["icao"] for r in send.call_args.kwargs["aircraft"]]
        self.assertEqual(sent, ["999AAA"],
                         "only the aircraft not already held may go up")

    def test_the_hold_lasts_a_month_then_expires(self):
        # The server's "new" means new to the account, ever, and a station
        # near an airport sees the same tails daily. A day hold re-offered
        # them every morning as a sync with nothing new in it.
        records = [rec("ABC123")]
        self._upload(records)
        for label, offset in (("next day", 86400 + 60),
                              ("next week", 7 * 86400)):
            with self.subTest(label), self._at(offset):
                _, send = self._upload(records)
                self.assertEqual(send.call_count, 0)

        with self._at(muninn.gungnir.holds.ACCEPTED_TTL + 1):
            _, send = self._upload(records)
        self.assertEqual(send.call_count, 1,
                         "suppression must expire, never be permanent")

    def test_the_delta_is_what_gets_held(self):
        # A newcomer gets the full month from the upload that carried it,
        # and an aircraft already held keeps its original hold rather than
        # being refreshed by an upload it was not in.
        self._upload([rec("ABC123")])
        before = muninn.gungnir.holds.load("muninn")["ABC123"]
        with self._at(3600):
            self._upload([rec("ABC123"), rec("DEF456")])
        state = muninn.gungnir.holds.load("muninn")
        self.assertEqual(state["ABC123"], before)
        self.assertGreater(state["DEF456"], before)

    def test_holds_from_an_earlier_version_are_honoured(self):
        # A v2.5.1 state file holds for an hour or a day. Those entries stay
        # valid expiry times: honoured while they last, then re-sent once
        # and moved onto the month-long hold.
        now = __import__("time").time()
        muninn.gungnir.holds.save("muninn", {"ABC123": now + 3600})
        _, send = self._upload([rec("ABC123")])
        self.assertEqual(send.call_count, 0)
        with self._at(3601):
            _, send = self._upload([rec("ABC123")])
        self.assertEqual(send.call_count, 1)
        self.assertGreater(muninn.gungnir.holds.load("muninn")["ABC123"],
                           now + 7 * 86400)

    def test_record_without_icao_is_never_suppressed(self):
        r = rec("ABC123")
        r.pop("icao")
        self._upload([r])
        _, send = self._upload([r])
        self.assertEqual(send.call_count, 1,
                         "an unreadable bookkeeping key must upload, not drop")

    def test_dry_run_neither_reads_nor_writes_state(self):
        records = [rec("ABC123")]
        rc, send = self._upload(records, dry_run=True)
        self.assertEqual(send.call_count, 1)
        self.assertFalse(self.state.exists(),
                         "a dry run must not record anything as sent")

        # And a dry run is never itself suppressed.
        self._upload(records)
        _, send = self._upload(records, dry_run=True)
        self.assertEqual(send.call_count, 1)

    def test_flag_off_restores_always_upload(self):
        records = [rec("ABC123")]
        self._upload(records, skip_unchanged=False)
        _, send = self._upload(records, skip_unchanged=False)
        self.assertEqual(send.call_count, 1)
        self.assertFalse(self.state.exists())

    def test_flag_off_sends_even_with_state_already_recorded(self):
        # The half that matters to someone reaching for the escape hatch:
        # they already have state from earlier runs and want this cycle to
        # go out regardless. Asserting only that the flag records no state
        # (above) passes even if the flag were ignored on the skip branch.
        records = [rec("ABC123")]
        self._upload(records)
        _, send = self._upload(records)
        self.assertEqual(send.call_count, 0, "state primed")

        _, send = self._upload(records, skip_unchanged=False)
        self.assertEqual(send.call_count, 1,
                         "--no-skip-unchanged must override existing state, "
                         "not merely decline to add to it")

    def test_a_successful_upload_is_recorded_whatever_the_watermark(self):
        # The hold no longer depends on the server's counters at all, so a
        # missing or stale hwm.json must not change what gets recorded.
        for label, hwm in (
                ("missing", None),
                ("stale", {"last_upload_ts": 1.0,
                           "counters": {"aircraft_imported": 99}})):
            with self.subTest(hwm=label):
                self.state.unlink(missing_ok=True)
                with mock.patch.object(muninn.gungnir.hwm, "read",
                                       return_value=hwm):
                    self._upload([rec("ABC123")])
                    _, send = self._upload([rec("ABC123")])
                self.assertEqual(send.call_count, 0)

    def test_partially_accounted_payload_is_still_marked_sent(self):
        # Measured against the live API 2026-09-20: an aircraft upload comes
        # back with aircraft_imported + aircraft_already_seen and every other
        # counter at zero, and in the field that pair lands one short of the
        # payload on roughly half of a busy feeder's cycles. The shortfall is
        # in no counter, so no amount of reading more of them closes it.
        #
        # v2.3.0 required the counters to account for the payload before
        # recording it, which meant that feeder recorded nothing, ever, and
        # the gate never engaged. A successful upload is now recorded.
        records = [rec("ABC123"), rec("DEF456"), rec("777AAA")]
        hwm = {"last_upload_ts": __import__("time").time() + 5,
               "counters": {"aircraft_imported": 1,
                            "aircraft_already_seen": 1}}
        with mock.patch.object(muninn.gungnir.hwm, "read", return_value=hwm):
            self._upload(records)
            self.assertEqual(sorted(muninn.gungnir.holds.load("muninn")),
                             ["777AAA", "ABC123", "DEF456"])
            _, send = self._upload(records)
        self.assertEqual(send.call_count, 0,
                         "the gate must engage on a real feeder's counters, "
                         "not just on ones that add up")

    def test_state_file_is_replaced_atomically(self):
        # A concurrent reader must never see a half-written file, so the
        # write lands on a temp path and is renamed over the target.
        seen = []
        real = Path.write_text

        def spy(self_path, *a, **kw):
            seen.append(Path(self_path).name)
            return real(self_path, *a, **kw)

        with mock.patch.object(Path, "write_text", spy):
            muninn.gungnir.holds.save("muninn", {"ABC123": 1.0})
        self.assertTrue(seen and seen[0].endswith(".tmp"),
                        f"expected a temp-file write, got {seen}")
        self.assertEqual(muninn.gungnir.holds.load("muninn"), {"ABC123": 1.0})
        self.assertEqual(list(self.state.parent.glob("*.tmp")), [],
                         "no temp file may be left behind")

    def test_failed_upload_records_nothing(self):
        # The watermark here would account for the payload in full, so the
        # only thing standing between a failed upload and a recorded one is
        # the rc check.
        records = [rec("ABC123")]
        hwm = {"last_upload_ts": __import__("time").time() + 5,
               "counters": {"aircraft_imported": 1,
                            "aircraft_already_seen": 0}}
        with mock.patch.object(muninn.gungnir.hwm, "read", return_value=hwm):
            with mock.patch.object(muninn.gungnir.transport, "send",
                                   return_value=1):
                rc = muninn.upload(records, "key", "https://example.invalid")
        self.assertEqual(rc, 1)
        self.assertFalse(self.state.exists(),
                         "a failed upload must be retried, not marked sent")

    def test_corrupt_state_file_degrades_to_sending(self):
        self.state.write_text("{not json")
        records = [rec("ABC123")]
        _, send = self._upload(records)
        self.assertEqual(send.call_count, 1,
                         "an unreadable state must cost one redundant "
                         "upload, never a suppressed one")

    def test_wrongly_typed_state_degrades_to_sending(self):
        # Valid JSON, unusable content. This one gets past json.loads, so it
        # is the case where a timestamp that isn't a number would otherwise
        # reach the arithmetic in _prune_sent_state and take the upload down.
        for bad in ('{"ABC123": "yesterday"}', '["ABC123"]', '"nope"'):
            with self.subTest(bad=bad):
                self.state.write_text(bad)
                self.assertEqual(muninn.gungnir.holds.load("muninn"), {})
                _, send = self._upload([rec("ABC123")])
                self.assertEqual(send.call_count, 1)

    def test_stream_mode_is_exempt(self):
        # Stream mode flushes only the aircraft that changed since the last
        # flush, a finer-grained answer to the same problem. Stacking the
        # hourly window on top would suppress live position updates for an
        # aircraft that stays in view, so it opts out at the call site --
        # and must keep opting out even with the default left alone.
        import argparse
        rows = {"ABC123": {"icao": "ABC123", "lat": 32.9, "lon": -117.2,
                           "alt_ft": 0, "speed_kt": 0, "heading": 0,
                           "callsign": "", "first_seen": muninn._now_iso()}}
        args = argparse.Namespace(
            upload=True, stdout=False, no_save=True, out_dir=None,
            batch_size=1000, dry_run=False, api_url="https://example.invalid",
            no_skip_unchanged=False)
        with mock.patch.object(muninn.gungnir.transport, "send",
                               return_value=0) as send:
            for _ in range(3):
                muninn._flush_stream_records(rows, {"ABC123"}, "key", args)
        self.assertEqual(send.call_count, 3,
                         "stream flushes must not be suppressed by the "
                         "snapshot-feeder gate")
        self.assertFalse(self.state.exists())

    def test_state_is_pruned(self):
        # Values are hold expiry times, so pruning needs no TTL of its own.
        now = __import__("time").time()
        state = {f"{i:06X}": now - 10 for i in range(5)}
        state["FRESH1"] = now + muninn.gungnir.holds.SENT_TTL
        muninn.gungnir.holds.save("muninn", state)
        pruned = muninn.gungnir.holds.prune(muninn.gungnir.holds.load("muninn"), now)
        self.assertEqual(list(pruned), ["FRESH1"])

    def test_a_v23x_state_file_degrades_to_sending(self):
        # v2.3.x stored send times. Read as expiry times they are all in the
        # past, so the first load prunes them: one redundant upload, then
        # correct. What must never happen is the reverse, a stale file
        # suppressing an upload it should not.
        now = __import__("time").time()
        self.state.write_text(json.dumps({"ABC123": now}))  # v2.3.x shape
        _, send = self._upload([rec("ABC123")])
        self.assertEqual(send.call_count, 1,
                         "an old-format state must never suppress a sync")


if __name__ == "__main__":
    unittest.main()

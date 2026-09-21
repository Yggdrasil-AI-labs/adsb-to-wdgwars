"""Already-sent gate: don't spend a sync on data the server already has.

A fixed station on a timer re-uploads the same aircraft every cycle. The
server counts those as syncs carrying nothing new, and the Uplink page now
says so ("N syncs in a row with nothing new in them"). Reported by a feeder
operator 2026-09-20 with a streak of 28, which is seven hours at his
15-minute interval: an overnight window, not a fault.

The gate suppresses the request when every ICAO in the payload went up
inside SENT_TTL_SECONDS. What these tests hold down:

1. A repeat payload skips the POST entirely; one new ICAO in it does not.
2. The TTL expires, so suppression can never be permanent.
3. A record with no ICAO is never suppressed (we upload rather than drop
   an aircraft over a bookkeeping key we couldn't read).
4. --dry-run neither reads nor writes the state.
5. --no-skip-unchanged restores the old always-upload behavior.
6. The state defers to the server: when hwm.json shows the server imported
   more aircraft than we classified as new, the state is dropped rather
   than trusted, because over-suppressing is the failure that costs score.

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
        self.state = Path(self._tmp.name) / "sent-aircraft.json"
        p = mock.patch.object(muninn, "_sent_state_path",
                              return_value=self.state)
        p.start()
        self.addCleanup(p.stop)
        muninn._sent_state_write_warned = False
        muninn._sent_state_unverified_warned = False
        # Isolation floor: no test may read the operator's real hwm.json.
        # _upload's own patch overrides this per call.
        h = mock.patch.object(muninn.gungnir.hwm, "read", return_value=None)
        h.start()
        self.addCleanup(h.stop)
        self.addCleanup(self._tmp.cleanup)

    def _upload(self, records, hwm="auto", **kw):
        """One upload with the transport stubbed.

        ``hwm`` is what gungnir would have written after the send. The
        default is a server that accounted for the whole payload, because
        that is what a successful single-chunk upload looks like and the
        gate records nothing without it. Pass an explicit dict, or None for
        "no watermark", to test the other paths."""
        if hwm == "auto":
            hwm = {"last_upload_ts": __import__("time").time() + 5,
                   "counters": {"aircraft_imported": 0,
                                "aircraft_already_seen": len(records)}}
        with mock.patch.object(muninn.gungnir.hwm, "read", return_value=hwm):
            with mock.patch.object(muninn.gungnir.transport, "send",
                                   return_value=0) as send:
                rc = muninn.upload(records, "key", "https://example.invalid",
                                   **kw)
        return rc, send

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

    def test_one_new_aircraft_sends_the_whole_payload(self):
        first = [rec("ABC123"), rec("DEF456")]
        self._upload(first)

        second = first + [rec("999AAA")]
        rc, send = self._upload(second)
        self.assertEqual(rc, 0)
        self.assertEqual(send.call_count, 1)
        sent = send.call_args.kwargs["aircraft"]
        self.assertEqual(len(sent), 3,
                         "scoring behavior on a real upload is unchanged: we "
                         "still send the full snapshot, not just the new one")

    def _mixed_hwm(self, n):
        """A response that imported something, so the payload gets the short
        hold: the server does not say WHICH aircraft were new."""
        return {"last_upload_ts": __import__("time").time() + 5,
                "counters": {"aircraft_imported": 1,
                             "aircraft_already_seen": max(n - 1, 0)}}

    def test_ttl_expiry_lets_it_send_again(self):
        records = [rec("ABC123")]
        hwm = self._mixed_hwm(1)
        self._upload(records, hwm=hwm)
        _, send = self._upload(records, hwm=hwm)
        self.assertEqual(send.call_count, 0)

        with mock.patch.object(muninn.time, "time",
                               return_value=__import__("time").time()
                               + muninn.SENT_TTL_SECONDS + 1):
            _, send = self._upload(records, hwm=hwm)
        self.assertEqual(send.call_count, 1,
                         "suppression must expire, never be permanent")

    def test_zero_import_response_earns_the_long_hold(self):
        # aircraft_imported == 0 is the server saying it already holds every
        # aircraft in the payload. That is the case a fixed station hits all
        # day, and an hourly hold does nothing for it because aircraft turn
        # over completely inside half an hour.
        records = [rec("ABC123")]
        self._upload(records)  # default hwm: imported 0, all already seen

        two_hours = __import__("time").time() + 2 * muninn.SENT_TTL_SECONDS
        with mock.patch.object(muninn.time, "time", return_value=two_hours):
            _, send = self._upload(records)
        self.assertEqual(send.call_count, 0,
                         "an aircraft the server confirmed it has must stay "
                         "held well past the one-hour mark")

        past_day = __import__("time").time() + muninn.SERVER_HAS_TTL_SECONDS + 1
        with mock.patch.object(muninn.time, "time", return_value=past_day):
            _, send = self._upload(records)
        self.assertEqual(send.call_count, 1,
                         "even the long hold must expire")

    def test_mixed_response_keeps_the_short_hold(self):
        records = [rec("ABC123"), rec("DEF456")]
        self._upload(records, hwm=self._mixed_hwm(2))
        two_hours = __import__("time").time() + 2 * muninn.SENT_TTL_SECONDS
        with mock.patch.object(muninn.time, "time", return_value=two_hours):
            _, send = self._upload(records, hwm=self._mixed_hwm(2))
        self.assertEqual(send.call_count, 1,
                         "the server imported something and did not say "
                         "what, so nothing here earns the long hold")

    def test_a_later_short_mark_does_not_shorten_a_long_hold(self):
        # The second payload has to contain something NEW, or the gate skips
        # it and the overwrite path never runs -- which is how the first
        # version of this test passed against code that did overwrite.
        self._upload([rec("ABC123")])  # zero-import: ABC123 held for a day

        mixed = [rec("ABC123"), rec("DEF456")]
        _, send = self._upload(mixed, hwm=self._mixed_hwm(2))
        self.assertEqual(send.call_count, 1, "DEF456 is new, so this sends")

        state = muninn._load_sent_state()
        now = __import__("time").time()
        self.assertGreater(state["ABC123"], now + muninn.SENT_TTL_SECONDS,
                           "a confirmed aircraft reappearing in a mixed "
                           "payload must keep its day-long hold")
        self.assertLessEqual(state["DEF456"], now + muninn.SENT_TTL_SECONDS,
                             "the genuinely unconfirmed one stays on the "
                             "short hold")

        two_hours = now + 2 * muninn.SENT_TTL_SECONDS
        with mock.patch.object(muninn.time, "time", return_value=two_hours):
            _, send = self._upload([rec("ABC123")])
        self.assertEqual(send.call_count, 0)

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

    def test_server_import_count_overrides_our_state(self):
        # ABC123 is already on record, so a payload of both counts as one
        # new aircraft. The server then reports importing two: something we
        # wrote off as already-sent scored after all, so our bookkeeping is
        # over-suppressing and the state must go.
        #
        # Note this can only fire on an upload that was actually made. A
        # payload suppressed in full never reaches the server, so nothing
        # can contradict it -- SENT_TTL_SECONDS is the only recovery there,
        # which is why the TTL exists at all.
        self._upload([rec("ABC123")])
        records = [rec("ABC123"), rec("DEF456")]
        hwm = {"last_upload_ts": __import__("time").time() + 5,
               "counters": {"aircraft_imported": 2,
                            "aircraft_already_seen": 0}}
        _, send = self._upload(records, hwm=hwm)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(muninn._load_sent_state(), {},
                         "a server that scores what we called stale must "
                         "clear the state, not be overruled by it")
        _, send = self._upload(records)
        self.assertEqual(send.call_count, 1)

    def test_server_agreeing_exactly_keeps_the_state(self):
        # imported == expected_new is the server agreeing with us. Only a
        # count ABOVE what we classified as new means we suppressed
        # something real, so equality must not throw the state away.
        self._upload([rec("ABC123")])
        records = [rec("ABC123"), rec("DEF456")]
        hwm = {"last_upload_ts": __import__("time").time() + 5,
               "counters": {"aircraft_imported": 1,
                            "aircraft_already_seen": 1}}
        self._upload(records, hwm=hwm)
        self.assertIn("DEF456", muninn._load_sent_state(),
                      "the server agreeing must not clear the state")

    def test_multi_chunk_upload_does_not_trigger_the_self_heal(self):
        # gungnir's hwm.record runs per chunk and keeps only the last one,
        # so on a multi-chunk upload its counters describe a fraction of
        # what we sent. Comparing a total against that fraction would clear
        # the state on every large upload.
        self._upload([rec("ABC123")])
        records = [rec("ABC123"), rec("DEF456"), rec("777AAA")]
        hwm = {"last_upload_ts": __import__("time").time() + 5,
               "counters": {"aircraft_imported": 99,
                            "aircraft_already_seen": 0}}
        self._upload(records, hwm=hwm, batch_size=1)
        state = muninn._load_sent_state()
        self.assertIn("ABC123", state,
                      "a per-chunk watermark must not trigger the self-heal "
                      "and wipe state the upload did not contradict")
        self.assertIn("DEF456", state,
                      "the upload still succeeded, so it is still recorded")

    def test_unreadable_watermark_still_records_a_successful_upload(self):
        # The watermark only drives the over-suppression check. Not being
        # able to read it (none written, or one from another run) means the
        # check cannot fire, not that the upload did not happen -- rc said
        # it did. Tying the recording to the watermark disabled the gate for
        # multi-chunk uploads too, which was never the intent.
        for label, hwm in (
                ("missing", None),
                ("stale", {"last_upload_ts": 1.0,
                           "counters": {"aircraft_imported": 99,
                                        "aircraft_already_seen": 0}})):
            with self.subTest(hwm=label):
                self.state.unlink(missing_ok=True)
                records = [rec("ABC123")]
                self._upload(records, hwm=hwm)
                self.assertIn("ABC123", muninn._load_sent_state())
                _, send = self._upload(records, hwm=hwm)
                self.assertEqual(send.call_count, 0)

    def test_stale_watermark_does_not_clear_existing_state(self):
        self._upload([rec("ABC123")])
        hwm = {"last_upload_ts": 1.0,
               "counters": {"aircraft_imported": 99,
                            "aircraft_already_seen": 0}}
        self._upload([rec("ABC123"), rec("DEF456")], hwm=hwm)
        self.assertIn("ABC123", muninn._load_sent_state(),
                      "a watermark predating this upload is a different "
                      "run's and must not clear the state")

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
        self._upload(records, hwm=hwm)
        self.assertEqual(sorted(muninn._load_sent_state()),
                         ["777AAA", "ABC123", "DEF456"])
        _, send = self._upload(records, hwm=hwm)
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
            muninn._save_sent_state({"ABC123": 1.0})
        self.assertTrue(seen and seen[0].endswith(".tmp"),
                        f"expected a temp-file write, got {seen}")
        self.assertEqual(muninn._load_sent_state(), {"ABC123": 1.0})
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
                self.assertEqual(muninn._load_sent_state(), {})
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
        state["FRESH1"] = now + muninn.SENT_TTL_SECONDS
        muninn._save_sent_state(state)
        pruned = muninn._prune_sent_state(muninn._load_sent_state(), now)
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

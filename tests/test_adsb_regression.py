"""ADS-B regression tests.

Extracted from the v1.9.0 Zigbee test suite (which was deleted in
v1.10.0 along with Zigbee support itself). These six checks remain
valuable as ongoing ADS-B regression coverage.

Run: python -m unittest tests/test_adsb_regression.py
"""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402


class AdsbRegressionTests(unittest.TestCase):
    """Sanity checks that detect_format + the normalisation + the
    download payload shape stay stable."""

    def test_detect_format_avr(self):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        f.write("*8D4840D6202CC371C32CE0576098;\n")
        f.close()
        self.assertEqual(muninn.detect_format(Path(f.name)), "avr")

    def test_detect_format_sbs1(self):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        f.write("MSG,3,1,1,A8A5DD,1,2026/05/09,12:00:00.000,2026/05/09,12:00:00.000,,30000,,,42.123,-81.456,,,,,,0\n")
        f.close()
        self.assertEqual(muninn.detect_format(Path(f.name)), "sbs1")

    def test_detect_format_json(self):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        f.write('{"aircraft": []}')
        f.close()
        self.assertEqual(muninn.detect_format(Path(f.name)), "json")

    def test_norm_record_unchanged(self):
        r = muninn._norm_record(
            "A8A5DD", callsign="TEST",
            lat=42.0, lon=-81.0, alt_ft=30000, speed_kt=420, heading=270,
        )
        self.assertEqual(r["icao"], "A8A5DD")
        self.assertEqual(r["callsign"], "TEST")
        self.assertEqual(r["lat"], 42.0)
        self.assertEqual(r["type"], "ADSB")

    def test_parse_json_handles_ground_alt(self):
        """dump1090/readsb encode on-ground aircraft as alt_baro="ground".
        Reported by Badger 2026-05-26; previously crashed _ingest with
        ValueError: invalid literal for int() with base 10: 'ground'."""
        import json
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"now": 1.0, "aircraft": [
            {"hex": "A12345", "lat": 39.0, "lon": -82.0,
             "alt_baro": "ground", "gs": 0, "track": 0, "flight": "GROUNDY"},
            {"hex": "B67890", "lat": 39.1, "lon": -82.1,
             "alt_baro": 3500, "gs": 180, "track": 90, "flight": "INAIR"},
        ]}, f)
        f.close()
        rows = muninn.parse_json(Path(f.name))
        self.assertEqual(rows["A12345"]["alt_ft"], 0)
        self.assertEqual(rows["B67890"]["alt_ft"], 3500)

    def test_to_dump1090_fa_shape_unchanged(self):
        rec = muninn._norm_record(
            "A8A5DD", callsign="TEST", lat=42.0, lon=-81.0,
            alt_ft=30000, speed_kt=420, heading=270,
        )
        payload = muninn._to_dump1090_fa([rec])
        self.assertIn("aircraft", payload)
        self.assertNotIn("meshcore_nodes", payload)
        self.assertNotIn("networks", payload)
        self.assertEqual(len(payload["aircraft"]), 1)

    def test_mayhem_spd_label_ground_speed(self):
        """Some PortaPack/H4M Mayhem firmware label ground speed "Spd:NNN"
        instead of GS:/TAS:/IAS:. Confirmed against a real H4M ADSB.TXT
        capture 2026-07-19: all 113 aircraft decoded with gs:0 because
        "Spd:" was not matched by _MAYHEM_SPEED. Lock the label in."""
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        f.write("8DA2D92A990D810DE80C056DE73E ICAO:A2D92A FDX1234 "
                "Alt:36000 Lat:41.5000000 Lon:-81.5000000 Type:1 "
                "Hdg:180 Spd:410 Vrate:0 Sil:2\n")
        f.close()
        rows = muninn.parse_mayhem(Path(f.name))
        self.assertIn("A2D92A", rows)
        self.assertEqual(rows["A2D92A"]["speed_kt"], 410)
        self.assertEqual(rows["A2D92A"]["callsign"], "FDX1234")

    def test_generic_csv_does_not_clobber_with_degraded_row(self):
        """Reported by piratepat_ (Discord) 2026-07-23: a uConsole/Watch Dogs
        Go generic-CSV dump had three rows for the same ICAO, and the row
        order wasn't chronological (an earlier-timestamped row landed later
        in the file). The old parse_csv did a blind `rows[icao] = rec` per
        row, so whichever row was iterated last won outright, in the real
        sample that was a degraded observation with speed_kt=0/heading=0,
        silently discarding the good velocity data from an earlier row.
        Fixed to merge like parse_avr/parse_sbs1 already do: only overwrite
        alt_ft/speed_kt/heading/callsign when the new row's value is
        truthy."""
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        )
        f.write(
            "timestamp,icao,callsign,lat,lon,alt_ft,speed_kt,heading,squawk\n"
            "1784767754,ADD667,AAL1045,28.7092,-81.42237,39000,428,344,3336\n"
            "1784768498,A38C60,,28.46683,-81.48053,11375,293,349,3054\n"
            "1784767763,A36980,,28.20703,-81.75858,38000,450,315,\n"
            "1784767718,ADD667,AAL1045,28.64015,-81.39927,39000,431,344,\n"
            "1784767718,ADD667,AAL1045,28.64015,-81.39927,39000,0,0,\n"
        )
        f.close()
        rows = muninn.parse_csv(Path(f.name))
        self.assertEqual(rows["ADD667"]["speed_kt"], 431)
        self.assertEqual(rows["ADD667"]["heading"], 344)
        self.assertEqual(rows["ADD667"]["lat"], 28.64015)
        self.assertEqual(rows["A38C60"]["speed_kt"], 293)
        self.assertEqual(rows["A36980"]["heading"], 315)

    def test_mayhem_leading_timestamp_column(self):
        """Some H4M firmware prepends a YYYYMMDDHHMMSS timestamp column to
        each line. Detection sniffs s[:14] (alnum either way) and all field
        extraction is label-anchored, so the timestamped variant must still
        route to mayhem and parse the callsign/speed without picking up the
        timestamp as a bare token."""
        line = ("19800121210654 8DA71234990D820DE80C056DE73E ICAO:A71234 "
                "UPS5678 Alt:28000 Lat:41.4000000 Lon:-81.7000000 Type:1 "
                "Hdg:270 Spd:305 Vrate:-64 Sil:2\n")
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        f.write(line)
        f.close()
        self.assertEqual(muninn.detect_format(Path(f.name)), "mayhem")
        rows = muninn.parse_mayhem(Path(f.name))
        self.assertIn("A71234", rows)
        self.assertEqual(rows["A71234"]["speed_kt"], 305)
        self.assertEqual(rows["A71234"]["callsign"], "UPS5678")
        self.assertEqual(rows["A71234"]["alt_ft"], 28000)


class DetectionLooksPastTheFirstLineTests(unittest.TestCase):
    """Detection used to decide from the first non-blank line alone, with an
    unconditional CSV fallback, so one odd leading line misclassified a whole
    file. A valid PortaPack Mayhem capture then read as CSV, decoded zero
    aircraft, and advised the user to pass --csv-format, which is advice for a
    format the file is not in.

    The parser was never the problem: `--format mayhem` recovered every case
    below, so these pin the detection side.
    """

    GOOD = ("8D111111990D7B0DE80C056DE73E ICAO:111111 TEST111 Alt:34000 "
            "Lat:11.111111 Lon:-11.111111 Hdg:291 GS:368\n")

    def _write(self, body: str, suffix: str = ".txt") -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False,
                                        encoding="utf-8", newline="")
        f.write(body)
        f.close()
        return Path(f.name)

    def test_capture_beginning_mid_frame_is_still_mayhem(self):
        # SD card pulled while writing, or a fragment copied out of a longer
        # log: the first line is the tail of a frame and matches nothing.
        p = self._write("0DE80C056DE73E Lat:11.11\n" + self.GOOD)
        self.assertEqual(muninn.detect_format(p), "mayhem")
        self.assertEqual(len(muninn.parse_mayhem(p)), 1)

    def test_leading_banner_line_is_still_mayhem(self):
        p = self._write("PortaPack Mayhem ADS-B log\n\n" + self.GOOD)
        self.assertEqual(muninn.detect_format(p), "mayhem")

    def test_blank_leading_lines_are_still_mayhem(self):
        p = self._write("\n\n" + self.GOOD)
        self.assertEqual(muninn.detect_format(p), "mayhem")

    def test_a_real_csv_is_still_csv(self):
        # The fallback has to keep working: a header plus rows carries no
        # per-line signature and must not be adopted by another format.
        p = self._write("hex,flight,lat,lon,altitude\n"
                        "A11111,TEST111,11.1,-11.1,34000\n", suffix=".csv")
        self.assertEqual(muninn.detect_format(p), "csv")

    def test_empty_file_is_still_empty(self):
        self.assertEqual(muninn.detect_format(self._write("")), "empty")
        self.assertEqual(muninn.detect_format(self._write("\n\n\n")), "empty")

    def test_a_first_line_signature_still_wins_immediately(self):
        # A file whose first line IS a signature must return on that line
        # rather than scanning on and finding a different format below it.
        p = self._write("MSG,3,,,A11111,,,,,,,,35000,,,11.1,-11.1,,,,,\n"
                        "8D111111990D7B0DE80C056DE73E ICAO:111111 Alt:1\n")
        self.assertEqual(muninn.detect_format(p), "sbs1")

    def test_scan_window_is_bounded(self):
        # A genuine CSV with many unrecognised rows must not be read to the end
        # of a multi-gigabyte file just to reach the same verdict.
        p = self._write("col_a,col_b\n" + ("1,2\n" * 5000), suffix=".csv")
        self.assertEqual(muninn.detect_format(p), "csv")
        self.assertLessEqual(muninn.DETECT_SCAN_LINES, 200)

    def test_classify_line_reports_no_signature_as_none(self):
        self.assertIsNone(muninn._classify_line("just some text"))
        self.assertEqual(muninn._classify_line(self.GOOD.strip()), "mayhem")


if __name__ == "__main__":
    unittest.main(verbosity=2)

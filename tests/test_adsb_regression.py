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


if __name__ == "__main__":
    unittest.main(verbosity=2)

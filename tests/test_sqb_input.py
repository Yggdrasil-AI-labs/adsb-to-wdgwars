"""Tests for RTL1090 / BaseStation .sqb (SQLite) input support.

Run: python -m unittest tests/test_sqb_input.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402
from tests.fixtures import build_sample_sqb  # noqa: E402


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample.sqb"


class SqbInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Always regenerate from the builder so tests fail loudly if the
        # builder drifts out of sync with the parser expectations.
        build_sample_sqb.build(FIXTURE)

    def test_detect_format_by_magic_bytes(self):
        # A real SQLite file is detected regardless of extension because
        # the first 16 bytes are the canonical SQLite header.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            build_sample_sqb.build(tmp_path)
            self.assertEqual(muninn.detect_format(tmp_path), "sqb")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_detect_format_by_extension(self):
        self.assertEqual(muninn.detect_format(FIXTURE), "sqb")

    def test_parse_sqb_emits_expected_record_count(self):
        rows = muninn.parse_sqb(FIXTURE)
        # 2 (A11111 first+last) + 1 (B22222 first only) + 1 (C33333 last only)
        self.assertEqual(len(rows), 4)

    def test_parse_sqb_extracts_both_endpoints_per_flight(self):
        rows = muninn.parse_sqb(FIXTURE)
        records = list(rows.values())
        by_icao = {}
        for r in records:
            by_icao.setdefault(r["icao"], []).append(r)

        # A11111 had StartTime/First* AND EndTime/Last* — two records.
        self.assertEqual(len(by_icao["A11111"]), 2)
        latlons = sorted((r["lat"], r["lon"]) for r in by_icao["A11111"])
        self.assertEqual(latlons, sorted([(42.1, -81.1), (42.5, -81.9)]))

        # B22222 only had a start point — one record.
        self.assertEqual(len(by_icao["B22222"]), 1)
        self.assertEqual(by_icao["B22222"][0]["lat"], 43.0)
        self.assertEqual(by_icao["B22222"][0]["lon"], -82.0)

        # C33333 start was 0/0 (no fix) — only the end record survives.
        self.assertEqual(len(by_icao["C33333"]), 1)
        self.assertEqual(by_icao["C33333"][0]["lat"], 41.5)

    def test_parse_sqb_preserves_callsigns(self):
        rows = muninn.parse_sqb(FIXTURE)
        callsigns = {r["callsign"] for r in rows.values()}
        self.assertIn("TST111", callsigns)
        self.assertIn("TST222", callsigns)
        self.assertIn("TST333", callsigns)

    def test_parse_sqb_default_timestamps_are_treated_as_utc(self):
        rows = muninn.parse_sqb(FIXTURE)
        # The fixture's first flight StartTime is "2024-08-15 14:32:11.123".
        # With no --sqb-tz, we treat the naive string as UTC and drop the
        # fractional seconds.
        recs = [r for r in rows.values() if r["icao"] == "A11111"]
        seens = sorted(r["first_seen"] for r in recs)
        self.assertEqual(seens, [
            "2024-08-15 14:32:11",
            "2024-08-15 15:10:42",
        ])

    def test_parse_sqb_tz_override_converts_to_utc(self):
        # America/New_York is UTC-4 in August (EDT). 14:32:11 EDT -> 18:32:11 UTC.
        # On Windows / minimal installs, zoneinfo needs the `tzdata` PyPI
        # package; we skip rather than fail when the system has no zone
        # database available.
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo("America/New_York")
        except Exception as e:
            self.skipTest(f"no IANA zone database available: {e}")
        rows = muninn.parse_sqb(FIXTURE, tz_override="America/New_York")
        recs = [r for r in rows.values() if r["icao"] == "A11111"]
        seens = sorted(r["first_seen"] for r in recs)
        self.assertEqual(seens, [
            "2024-08-15 18:32:11",
            "2024-08-15 19:10:42",
        ])

    def test_parse_sqb_speed_and_track_carried_on_first_record(self):
        rows = muninn.parse_sqb(FIXTURE)
        first = next(
            r for k, r in rows.items()
            if r["icao"] == "A11111" and k.endswith("-first")
        )
        self.assertEqual(first["speed_kt"], 420)
        self.assertEqual(first["heading"], 270)

    def test_parse_sqb_last_record_drops_speed_and_track(self):
        # BaseStation does not store Last(GroundSpeed|Track), so we
        # deliberately surface 0 rather than carrying forward First*.
        rows = muninn.parse_sqb(FIXTURE)
        last = next(
            r for k, r in rows.items()
            if r["icao"] == "A11111" and k.endswith("-last")
        )
        self.assertEqual(last["speed_kt"], 0)
        self.assertEqual(last["heading"], 0)

    def test_parse_sqb_schema_drift_optional_columns_missing(self):
        # Build a DB that omits FirstGroundSpeed / FirstTrack /
        # LastAltitude. parse_sqb should still succeed and treat the
        # missing values as 0.
        with tempfile.NamedTemporaryFile(suffix=".sqb", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            tmp_path.unlink(missing_ok=True)
            conn = sqlite3.connect(tmp_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE Aircraft (
                        AircraftID INTEGER PRIMARY KEY,
                        ModeS TEXT NOT NULL
                    );
                    CREATE TABLE Flights (
                        FlightID INTEGER PRIMARY KEY,
                        AircraftID INTEGER,
                        StartTime TEXT,
                        EndTime TEXT,
                        Callsign TEXT,
                        FirstLat REAL, LastLat REAL,
                        FirstLon REAL, LastLon REAL,
                        FirstAltitude INTEGER
                    );
                    INSERT INTO Aircraft (AircraftID, ModeS) VALUES (1, 'D44444');
                    INSERT INTO Flights (
                        FlightID, AircraftID, StartTime, EndTime, Callsign,
                        FirstLat, LastLat, FirstLon, LastLon, FirstAltitude
                    ) VALUES (
                        1, 1, '2024-01-01 00:00:00', '2024-01-01 01:00:00',
                        'DRIFT1', 40.0, 41.0, -83.0, -84.0, 5000
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()
            rows = muninn.parse_sqb(tmp_path)
            self.assertEqual(len(rows), 2)
            for r in rows.values():
                self.assertEqual(r["speed_kt"], 0)
                self.assertEqual(r["heading"], 0)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_parse_sqb_empty_flights_exits_nonzero(self):
        with tempfile.NamedTemporaryFile(suffix=".sqb", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            tmp_path.unlink(missing_ok=True)
            conn = sqlite3.connect(tmp_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE Aircraft (
                        AircraftID INTEGER PRIMARY KEY,
                        ModeS TEXT NOT NULL
                    );
                    CREATE TABLE Flights (
                        FlightID INTEGER PRIMARY KEY,
                        AircraftID INTEGER,
                        StartTime TEXT,
                        FirstLat REAL, FirstLon REAL
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()
            with self.assertRaises(SystemExit) as cm:
                muninn.parse_sqb(tmp_path)
            self.assertNotEqual(cm.exception.code, 0)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_parse_sqb_no_flights_table_exits_nonzero(self):
        # Some BaseStation installs only populate Aircraft.
        with tempfile.NamedTemporaryFile(suffix=".sqb", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            tmp_path.unlink(missing_ok=True)
            conn = sqlite3.connect(tmp_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE Aircraft (
                        AircraftID INTEGER PRIMARY KEY,
                        ModeS TEXT NOT NULL
                    );
                    INSERT INTO Aircraft (ModeS) VALUES ('E55555');
                    """
                )
                conn.commit()
            finally:
                conn.close()
            with self.assertRaises(SystemExit) as cm:
                muninn.parse_sqb(tmp_path)
            self.assertNotEqual(cm.exception.code, 0)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_convert_one_dispatch_routes_sqb(self):
        # The auto-dispatch must reach parse_sqb without needing an
        # explicit --format override.
        records = muninn._convert_one(FIXTURE, None, None)
        self.assertEqual(len(records), 4)
        # Every record carries the WDGoWars-required fields.
        for r in records:
            self.assertEqual(r["type"], "ADSB")
            self.assertEqual(len(r["icao"]), 6)


if __name__ == "__main__":
    unittest.main()

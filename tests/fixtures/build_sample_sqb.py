"""Build the synthetic sample.sqb fixture used by tests/test_sqb_input.py.

Run this script to regenerate `tests/fixtures/sample.sqb` from scratch.
The output is a tiny SQLite database with the Kinetic Avionics
BaseStation schema (Aircraft + Flights), covering three intentional
shapes:

  1. A11111  Start AND End both have valid coords  -> 2 records
  2. B22222  Only Start has coords (End is NULL)   -> 1 record
  3. C33333  Start is 0.0/0.0 ("no fix"), End OK   -> 1 record
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def build(path: Path) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE Aircraft (
                AircraftID       INTEGER PRIMARY KEY,
                ModeS            TEXT NOT NULL,
                Registration     TEXT,
                ICAOTypeCode     TEXT,
                Manufacturer     TEXT,
                Type             TEXT,
                Country          TEXT
            );
            CREATE TABLE Flights (
                FlightID         INTEGER PRIMARY KEY,
                SessionID        INTEGER,
                AircraftID       INTEGER,
                StartTime        TEXT,
                EndTime          TEXT,
                Callsign         TEXT,
                FirstLat         REAL,
                LastLat          REAL,
                FirstLon         REAL,
                LastLon          REAL,
                FirstAltitude    INTEGER,
                LastAltitude     INTEGER,
                FirstGroundSpeed REAL,
                FirstTrack       REAL,
                FirstVerticalRate INTEGER,
                FirstSquawk      TEXT,
                NumPosMsgRec     INTEGER
            );
            """
        )

        cur.executemany(
            "INSERT INTO Aircraft (AircraftID, ModeS, Registration) VALUES (?, ?, ?)",
            [
                (1, "A11111", "N111AA"),
                (2, "B22222", "N222BB"),
                (3, "C33333", "N333CC"),
            ],
        )

        cur.executemany(
            """INSERT INTO Flights (
                FlightID, SessionID, AircraftID, StartTime, EndTime, Callsign,
                FirstLat, LastLat, FirstLon, LastLon,
                FirstAltitude, LastAltitude,
                FirstGroundSpeed, FirstTrack,
                FirstVerticalRate, FirstSquawk, NumPosMsgRec
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # Two-endpoint flight — both First and Last are valid.
                (
                    1, 1, 1,
                    "2024-08-15 14:32:11.123",
                    "2024-08-15 15:10:42.456",
                    "TST111",
                    42.10, 42.50,
                    -81.10, -81.90,
                    25000, 8000,
                    420.0, 270.0,
                    1024, "1200", 3500,
                ),
                # End coords missing — only First should produce a record.
                (
                    2, 1, 2,
                    "2024-08-15 16:00:00",
                    None,
                    "TST222",
                    43.00, None,
                    -82.00, None,
                    18000, None,
                    350.0, 90.0,
                    0, "2000", 12,
                ),
                # Start is the BaseStation "no fix" sentinel (0.0/0.0);
                # End has a real position.
                (
                    3, 1, 3,
                    "2024-08-15 17:30:00",
                    "2024-08-15 18:00:00",
                    "TST333",
                    0.0, 41.50,
                    0.0, -80.50,
                    0, 5000,
                    0.0, 0.0,
                    0, "1200", 800,
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "sample.sqb"
    build(out)
    print(f"wrote {out}")

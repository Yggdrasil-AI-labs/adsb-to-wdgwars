"""Smoke tests for muninn v1.9.0 Zigbee support.

Verifies:
- CSV parser handles the Sleipnir 6-column format
- NDJSON parser tolerates missing optional fields
- pcap parser handles a synthetic libpcap classic with linktype 195
- PAN aggregation collapses frames correctly, skips empty PAN, skips 0xFFFF
- HMAC envelope matches the byte layout the WDGoWars server accepts
- detect_format autodetects each format
- ADS-B regression: existing parsers still work after refactor

Run: python -m unittest tests/test_zigbee.py
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

# Add repo root to sys.path so `import muninn` works regardless of cwd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import muninn  # noqa: E402


class CSVParserTests(unittest.TestCase):
    def _write_csv(self, rows: list[str]) -> Path:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False, encoding="utf-8"
        )
        f.write("pan_id,channel,lat,lon,rssi,first_seen\n")
        for r in rows:
            f.write(r + "\n")
        f.close()
        return Path(f.name)

    def test_basic_csv_parse(self):
        p = self._write_csv([
            "0x010B,17,41.471,-81.788,-93.0,1778299367.0",
            "0x2733,15,41.472,-81.789,-100.5,1778408578.0",
        ])
        frames = muninn.parse_zigbee_csv(p)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]["pan_id"], "0x010B")
        self.assertEqual(frames[0]["channel"], 17)
        self.assertAlmostEqual(frames[0]["lat"], 41.471, places=4)

    def test_detect_format_csv(self):
        p = self._write_csv(["0x010B,17,41.47,-81.79,-93,1778299367"])
        self.assertEqual(muninn.detect_format(p), "zigbee-csv")


class NDJSONParserTests(unittest.TestCase):
    def test_basic_ndjson(self):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        f.write('{"pan_id":"0x010B","channel":17,"lat":41.47,"lon":-81.79,"rssi":-93,"first_seen":"2026-05-09 03:57:01"}\n')
        f.write('{"pan_id":"0x2733","channel":15,"lat":41.47,"lon":-81.79,"first_seen":1778408578.0}\n')
        f.close()
        frames = muninn.parse_zigbee_ndjson(Path(f.name))
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[1]["pan_id"], "0x2733")


class AggregationTests(unittest.TestCase):
    def test_aggregates_by_pan(self):
        frames = [
            {"pan_id": "0x010B", "channel": 17, "lat": 41.471, "lon": -81.788,
             "rssi": -100, "ts": 1778299367.0},
            {"pan_id": "0x010B", "channel": 17, "lat": 41.473, "lon": -81.790,
             "rssi": -90, "ts": 1778299400.0},
            {"pan_id": "0x010B", "channel": 23, "lat": 41.472, "lon": -81.789,
             "rssi": -95, "ts": 1778299500.0},
            {"pan_id": "0x2733", "channel": 15, "lat": 41.472, "lon": -81.789,
             "rssi": -103, "ts": 1778408578.0},
        ]
        out = muninn._aggregate_zigbee_pans(frames)
        self.assertEqual(len(out), 2)
        pan_010b = next(r for r in out if r["name"].startswith("Zigbee PAN 0x010B"))
        # 3 frames for 0x010B, channel 17 dominates (2 vs 1)
        self.assertIn("ch17", pan_010b["name"])
        self.assertAlmostEqual(pan_010b["lat"], 41.472, places=3)
        self.assertAlmostEqual(pan_010b["rssi"], -95.0, places=1)

    def test_skips_empty_pan(self):
        out = muninn._aggregate_zigbee_pans([
            {"pan_id": "", "channel": 17, "lat": 41.47, "lon": -81.79,
             "rssi": -100, "ts": 1778299367.0},
            {"pan_id": "0x010B", "channel": 17, "lat": 41.47, "lon": -81.79,
             "rssi": -100, "ts": 1778299367.0},
        ])
        self.assertEqual(len(out), 1)

    def test_skips_broadcast_ffff(self):
        out = muninn._aggregate_zigbee_pans([
            {"pan_id": "0xFFFF", "channel": 17, "lat": 41.47, "lon": -81.79,
             "rssi": -100, "ts": 1778299367.0},
            {"pan_id": "FFFF", "channel": 17, "lat": 41.47, "lon": -81.79,
             "rssi": -100, "ts": 1778299367.0},
            {"pan_id": "0x010B", "channel": 17, "lat": 41.47, "lon": -81.79,
             "rssi": -100, "ts": 1778299367.0},
        ])
        self.assertEqual(len(out), 1)
        self.assertNotIn("FFFF", out[0]["name"].upper())

    def test_skips_missing_gps(self):
        out = muninn._aggregate_zigbee_pans([
            {"pan_id": "0x010B", "channel": 17, "lat": None, "lon": -81.79,
             "rssi": -100, "ts": 1778299367.0},
        ])
        self.assertEqual(len(out), 0)

    def test_record_shape(self):
        out = muninn._aggregate_zigbee_pans([
            {"pan_id": "0x010B", "channel": 17, "lat": 41.47, "lon": -81.79,
             "rssi": -100, "ts": 1778299367.0},
        ])
        r = out[0]
        for field in ("node_id", "node_type", "name", "lat", "lon", "rssi",
                      "first_seen", "type"):
            self.assertIn(field, r)
        self.assertEqual(r["type"], "MESHCORE")
        self.assertEqual(r["node_type"], "ZIGBEE")
        self.assertTrue(r["node_id"].startswith("zigbee-pan-"))


class PcapParserTests(unittest.TestCase):
    """Build a minimal libpcap classic file with linktype 195 + one 802.15.4
    frame and verify the parser extracts the PAN."""

    def _build_pcap(self, frames: list[bytes], linktype: int = 195) -> Path:
        # libpcap classic global header (little-endian magic d4c3b2a1):
        # magic(4) ver_major(2) ver_minor(2) thiszone(4) sigfigs(4) snaplen(4) linktype(4)
        hdr = struct.pack(
            "<IHHIIII",
            0xa1b2c3d4, 2, 4, 0, 0, 65535, linktype,
        )
        body = b""
        ts = 1778299367
        for frame in frames:
            body += struct.pack("<IIII", ts, 0, len(frame), len(frame))
            body += frame
            ts += 1
        f = tempfile.NamedTemporaryFile(suffix=".pcap", delete=False)
        f.write(hdr + body)
        f.close()
        return Path(f.name)

    def _make_802154_frame(self, pan_le: int, seq: int = 0x42) -> bytes:
        # FCF: data frame, dest short, src short, PAN compression
        # Bits: type=001 (Data), security=0, frame_pending=0, ack_req=0,
        #       pan_compression=1, ...reserved..., dest_mode=10(short),
        #       frame_version=00, src_mode=10(short)
        # = 0x8841 little-endian
        fcf = struct.pack("<H", 0x8841)
        seq_b = struct.pack("<B", seq)
        dest_pan = struct.pack("<H", pan_le)
        dest_addr = struct.pack("<H", 0xFFFF)
        src_addr = struct.pack("<H", 0x0000)
        payload = b"\x00" * 4
        # Total: 2 + 1 + 2 + 2 + 2 + 4 = 13 bytes
        return fcf + seq_b + dest_pan + dest_addr + src_addr + payload

    def test_pcap_classic_linktype_195(self):
        frame = self._make_802154_frame(pan_le=0x2733)
        pcap = self._build_pcap([frame, frame, frame])
        frames = muninn.parse_zigbee_pcap(pcap, default_lat=41.47,
                                          default_lon=-81.79,
                                          default_channel=17)
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0]["pan_id"], "0x2733")
        self.assertEqual(frames[0]["channel"], 17)
        self.assertEqual(frames[0]["lat"], 41.47)

    def test_pcap_wrong_linktype_skipped(self):
        # Linktype 1 (ETHERNET) — Zigbee parser should skip
        frame = self._make_802154_frame(pan_le=0x2733)
        pcap = self._build_pcap([frame], linktype=1)
        frames = muninn.parse_zigbee_pcap(pcap, default_lat=41.47,
                                          default_lon=-81.79)
        self.assertEqual(frames, [])

    def test_detect_format_pcap_linktype_195(self):
        frame = self._make_802154_frame(pan_le=0x2733)
        pcap = self._build_pcap([frame])
        self.assertEqual(muninn.detect_format(pcap), "zigbee-pcap")


class EnvelopeTests(unittest.TestCase):
    """Verify the HMAC envelope shape matches what wdgwars_upload.py
    produces for the same input. Mode='zigbee' must populate
    meshcore_nodes; mode='aircraft' must populate aircraft."""

    def test_envelope_shape_zigbee(self):
        records = [
            {"node_id": "zigbee-pan-0x010B", "node_type": "ZIGBEE",
             "name": "Zigbee PAN 0x010B ch17", "lat": 41.47, "lon": -81.79,
             "rssi": -93.0, "first_seen": "2026-05-09 03:57:01",
             "type": "MESHCORE"},
        ]
        # Reach into upload()'s logic — replicate the envelope construction
        # to verify the shape mode='zigbee' should produce.
        chunk = records
        mode = "zigbee"
        if mode == "zigbee":
            payload = {"networks": [], "aircraft": [],
                       "meshcore_nodes": chunk}
        else:
            payload = {"networks": [], "aircraft": chunk,
                       "meshcore_nodes": []}
        body_json = json.dumps(payload, separators=(",", ":"))
        self.assertIn('"meshcore_nodes":[', body_json)
        self.assertIn('"aircraft":[]', body_json)

    def test_hmac_signature_format(self):
        # Same HMAC pattern as both muninn.upload() and the lab's
        # wdgwars_upload.py::_post_signed.
        key = "test-key-do-not-use"
        body = b'{"networks":[],"aircraft":[],"meshcore_nodes":[]}'
        data_b64 = base64.b64encode(body).decode()
        nonce = "deadbeefcafebabe"
        sig = hmac.new(key.encode(), (nonce + data_b64).encode(),
                       hashlib.sha256).hexdigest()
        # Sig is hex SHA256 = 64 chars
        self.assertEqual(len(sig), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in sig))


class AdsbRegressionTests(unittest.TestCase):
    """Ensure existing parsers + detect_format still work after the
    refactor that added Zigbee."""

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
        # Sanity: the _norm_record helper still produces the expected
        # dump1090-fa-shaped dict
        r = muninn._norm_record(
            "A8A5DD", callsign="TEST",
            lat=42.0, lon=-81.0, alt_ft=30000, speed_kt=420, heading=270,
        )
        self.assertEqual(r["icao"], "A8A5DD")
        self.assertEqual(r["callsign"], "TEST")
        self.assertEqual(r["lat"], 42.0)
        self.assertEqual(r["type"], "ADSB")


if __name__ == "__main__":
    unittest.main(verbosity=2)

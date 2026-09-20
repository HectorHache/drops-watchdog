#!/usr/bin/env python3
"""Unit tests: KV interaction pull — watermark, dedupe, favorite application, malformed payloads."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import kv as kvm


def _interaction(ts, kind, cid, value=""):
    return {"ts": ts, "kind": kind, "campaignId": cid, "value": value}


def _seed_campaign(conn, cid="c1", gname="Delta Force"):
    dbm.upsert_campaign(conn, {
        "id": cid, "game_id": "g1", "game_name": gname, "title": "Drop",
        "status": "ACTIVE", "start_at": "2026-09-01T10:00:00+00:00",
        "end_at": "2026-09-05T10:00:00+00:00", "image_url": None,
        "details_url": None, "channels_summary": None, "rewards": [],
    })


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class TestPull(unittest.TestCase):
    def test_normal_payload(self):
        resp = {"ok": True, "interactions": [{"ts": 1}]}
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(resp).encode())):
            out = kvm._pull("https://x/api", 0)
        self.assertEqual(out, [{"ts": 1}])

    def test_ok_false_returns_empty(self):
        resp = {"ok": False, "interactions": [{"ts": 1}]}
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(json.dumps(resp).encode())):
            out = kvm._pull("https://x/api", 0)
        self.assertEqual(out, [])

    def test_json_array_payload_does_not_crash(self):
        # a JSON array is a malformed response — must degrade, not AttributeError
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"[1,2,3]")):
            out = kvm._pull("https://x/api", 0)
        self.assertEqual(out, [])

    def test_scalar_payload_does_not_crash(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b'"ok"')):
            out = kvm._pull("https://x/api", 0)
        self.assertEqual(out, [])

    def test_network_error_returns_empty(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            out = kvm._pull("https://x/api", 0)
        self.assertEqual(out, [])


class TestKvPull(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.init_db(Path(self.tmp.name) / "drops.db")
        _seed_campaign(self.conn, "c1", "Delta Force")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _run_pull(self, interactions, endpoint="https://x/api"):
        with mock.patch.object(kvm, "_pull", return_value=interactions) as m:
            applied = kvm.kv_pull(self.conn, endpoint)
        return applied, m

    def test_watermark_advances_and_applied_count(self):
        applied, m = self._run_pull([
            _interaction(100, "like", "c1"),
            _interaction(200, "like", "c1"),
        ])
        self.assertEqual(applied, 2)
        self.assertEqual(m.call_args.args[1], 0)  # first pull starts at watermark 0
        self.assertEqual(dbm.meta_get(self.conn, "kv_last_ts"), "200")

        # second pull: same interactions must not be re-applied (watermark moved)
        applied2, m2 = self._run_pull([
            _interaction(100, "like", "c1"),
            _interaction(200, "like", "c1"),
        ])
        self.assertEqual(applied2, 0)
        self.assertEqual(m2.call_args.args[1], 200)

    def test_favorite_applies_to_favorites_table(self):
        applied, _ = self._run_pull([_interaction(100, "favorite", "c1")])
        self.assertEqual(applied, 1)
        row = self.conn.execute("SELECT game_name, weight, source FROM favorites").fetchone()
        self.assertEqual(row["game_name"], "Delta Force")
        self.assertEqual(row["source"], "kv")
        self.assertEqual(row["weight"], 10)

        # re-favoriting the same game must not duplicate the row
        applied2, _ = self._run_pull([_interaction(200, "favorite", "c1")])
        self.assertEqual(applied2, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM favorites").fetchone()["c"], 1)

    def test_favorite_unknown_campaign_ignored(self):
        applied, _ = self._run_pull([_interaction(100, "favorite", "nope")])
        self.assertEqual(applied, 1)  # recorded as event, but...
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM favorites").fetchone()["c"], 0)

    def test_remind_and_like_recorded_as_events(self):
        applied, _ = self._run_pull([
            _interaction(100, "remind", "c1"),
            _interaction(200, "like", "c1"),
        ])
        self.assertEqual(applied, 2)
        kinds = [r["kind"] for r in self.conn.execute(
            "SELECT kind FROM events WHERE kind='kv_interaction'")]
        self.assertEqual(len(kinds), 2)
        payloads = [json.loads(r["payload"]) for r in self.conn.execute(
            "SELECT payload FROM events WHERE kind='kv_interaction'")]
        self.assertEqual({p["kind"] for p in payloads}, {"remind", "like"})

    def test_malformed_interaction_rows_skipped(self):
        applied, _ = self._run_pull([
            {"ts": "not-an-int", "kind": "favorite", "campaignId": "c1"},  # ts ValueError
            {"ts": 100, "kind": "", "campaignId": "c1"},                    # empty kind recorded as-is
            _interaction(200, "like", "c1"),
        ])
        # the unparseable row is skipped; the parseable ones are still applied
        self.assertEqual(applied, 2)


if __name__ == "__main__":
    unittest.main()
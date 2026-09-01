#!/usr/bin/env python3
"""Tests for the Phase 7 learning loop (src/learning.py)."""
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import learning as lrn
import watchdog as wd


def _now():
    return datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


class LearningTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "test.db"
        self.conn = dbm.init_db(self.dbp)
        self.now = _now()
        # two campaigns: one active (Delta Force), one archived
        for cid, gname, status in [("c1", "Delta Force", "ACTIVE"),
                                   ("c2", "Marvel Rivals", "CLOSED")]:
            dbm.upsert_campaign(self.conn, {
                "id": cid, "game_id": "g-" + cid, "game_name": gname,
                "title": gname + " drop", "status": status,
                "start_at": "2026-09-01T08:00:00Z", "end_at": "2026-09-05T20:00:00Z",
                "image_url": None, "details_url": None, "channels_summary": None,
                "rewards": [],
            })
        dbm.upsert_campaign(self.conn, {
            "id": "c2", "game_id": "g-c2", "game_name": "Marvel Rivals",
            "title": "Marvel Rivals drop", "status": "CLOSED",
            "start_at": "2026-08-25T08:00:00Z", "end_at": "2026-08-26T20:00:00Z",
            "image_url": None, "details_url": None, "channels_summary": None,
            "rewards": [],
        })

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _interact(self, campaign_id, kind, age_days=0, ts=None):
        """Insert a kv_interaction event aged `age_days` before `now`."""
        if ts is None:
            ts = (self.now - datetime.timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        dbm.record_event(self.conn, "kv_interaction", campaign_id=campaign_id,
                         payload={"kind": kind, "value": ""})
        # backdate the ts column
        self.conn.execute("UPDATE events SET ts=? WHERE id=last_insert_rowid()", (ts,))
        self.conn.commit()

    def test_empty_history_no_signals(self):
        self.assertEqual(lrn.game_signals(self.conn, self.now), {})

    def test_favorite_like_remind_points(self):
        self._interact("c1", "favorite")
        self._interact("c1", "like")
        self._interact("c1", "remind")
        sig = lrn.game_signals(self.conn, self.now)
        self.assertIn("Delta Force", sig)
        d = sig["Delta Force"]
        self.assertEqual(d["favorites"], 1)
        self.assertEqual(d["likes"], 1)
        self.assertEqual(d["reminds"], 1)
        self.assertAlmostEqual(d["score"], 10 + 3 + 5, places=3)

    def test_recency_decay(self):
        self._interact("c1", "favorite", age_days=30)  # half-life: score halved
        sig = lrn.game_signals(self.conn, self.now)
        self.assertAlmostEqual(sig["Delta Force"]["score"], 5.0, places=3)

    def test_weights_capped_and_floor(self):
        for _ in range(6):
            self._interact("c1", "favorite")
        dbm.meta_set(self.conn, "unused", "1")  # no-op
        conn = self.conn
        conn.execute("INSERT OR IGNORE INTO favorites (game_name, weight, instant_alert, source, created_at) "
                     "VALUES ('Delta Force',10,1,'manual','2026-09-01T00:00:00Z')")
        conn.execute("INSERT OR IGNORE INTO favorites (game_name, weight, instant_alert, source, created_at) "
                     "VALUES ('Marvel Rivals',10,1,'manual','2026-09-01T00:00:00Z')")
        conn.commit()
        w = lrn.favorites_weight(conn, self.now)
        self.assertEqual(w["Marvel Rivals"], 10)          # untouched game stays base
        self.assertGreater(w["Delta Force"], 10)          # learned boost
        self.assertLessEqual(w["Delta Force"], lrn.MAX_WEIGHT)
        n = lrn.update_weights(conn, self.now)
        self.assertGreaterEqual(n, 1)
        self.conn.execute("SELECT weight FROM favorites WHERE game_name='Delta Force'")
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM favorites WHERE game_name='Delta Force'").fetchone())

    def test_unknown_kind_ignored(self):
        self._interact("c1", "weird")
        self.assertEqual(lrn.game_signals(self.conn, self.now), {})


if __name__ == "__main__":
    unittest.main()

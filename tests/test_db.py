#!/usr/bin/env python3
"""Unit tests: sqlite layer roundtrips. Stdlib only."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.init_db(Path(self.tmp.name) / "drops.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _camp(self, cid="c1", status="ACTIVE"):
        return {
            "id": cid, "game_id": "g1", "game_name": "Game One", "title": "Campaign",
            "status": status, "start_at": "2026-09-01T10:00:00+00:00",
            "end_at": "2026-09-02T10:00:00+00:00", "image_url": "https://x/img.jpg",
            "details_url": "https://twitch.tv/drops/campaigns?drop=c1",
            "rewards": [{"name": "Skin A", "required_minutes": 60},
                        {"name": "Skin B", "required_minutes": 120}],
        }

    def test_upsert_and_read(self):
        r = dbm.upsert_campaign(self.conn, self._camp())
        self.assertEqual(r, "inserted")
        r = dbm.upsert_campaign(self.conn, self._camp())
        self.assertEqual(r, "updated")
        camps = dbm.get_campaigns(self.conn)
        self.assertEqual(len(camps), 1)
        row = camps["c1"]
        self.assertEqual(row["game_name"], "Game One")
        self.assertEqual(row["first_seen_at"], row["last_seen_at"])
        rewards = self.conn.execute("SELECT name FROM rewards WHERE campaign_id='c1' ORDER BY sort").fetchall()
        self.assertEqual([x["name"] for x in rewards], ["Skin A", "Skin B"])

    def test_closed_transition(self):
        dbm.upsert_campaign(self.conn, self._camp())
        dbm.set_closed(self.conn, "c1")
        row = dbm.get_campaigns(self.conn)["c1"]
        self.assertEqual(row["status"], "CLOSED")
        self.assertTrue(row["is_closed"])
        self.assertTrue(row["archived_at"])
        # rows survive forever
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM campaigns").fetchone()["c"], 1)

    def test_ledger_and_meta(self):
        self.assertFalse(dbm.ledger_has(self.conn, "c1", "NEW_CAMPAIGN"))
        dbm.ledger_add(self.conn, "c1", "NEW_CAMPAIGN", "telegram:dm")
        self.assertTrue(dbm.ledger_has(self.conn, "c1", "NEW_CAMPAIGN"))
        dbm.meta_set(self.conn, "k", "v")
        self.assertEqual(dbm.meta_get(self.conn, "k"), "v")
        self.assertIsNone(dbm.meta_get(self.conn, "missing"))

    def test_events(self):
        dbm.record_event(self.conn, "new", "c1", {"n": 1})
        dbm.record_event(self.conn, "ended", "c1")
        n = self.conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()

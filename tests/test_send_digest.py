#!/usr/bin/env python3
"""Regression: _send_digest's tail must use the multipart `messages` list.

Before the fix, the tail still referenced the pre-refactor single `msg` variable.
Any run that actually had a digest to send (a new campaign, one ending, one ended
or one changed) raised NameError at the summary line -- after the message had been
delivered and the ledger marked, but before cmd_sync could build and push. The
visible effect was a silently skipped site update plus a cron incident. Runs with
nothing to send returned early and looked fine, which is why only roughly every
other scheduled run failed.

Stdlib only, matching the rest of the suite.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import main
import notify


class TestSendDigestTail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.init_db(Path(self.tmp.name) / "drops.db")
        self.sent = []
        self._orig = (main.wd.classify, main.wd.compose_digests_multipart,
                      main.get_favorites, notify.bot_token, notify.send_digest)
        main.wd.classify = lambda *a, **k: {
            "new": [{"id": "c1"}], "ending_24": [], "ended": [], "changed": [],
            "closed_only": [],
        }
        main.wd.compose_digests_multipart = lambda *a, **k: ["part one", "part two"]
        main.get_favorites = lambda conn: []
        notify.bot_token = lambda: "token"
        notify.send_digest = lambda token, chat, msg: self.sent.append((chat, msg))

    def tearDown(self):
        (main.wd.classify, main.wd.compose_digests_multipart, main.get_favorites,
         notify.bot_token, notify.send_digest) = self._orig
        self.conn.close()
        self.tmp.cleanup()

    def test_non_dry_run_completes_and_records_total_chars(self):
        msg, state = main._send_digest([], {"telegram": {"group_chat_id": "42"}, "timezone": "UTC",
                                        "watchdog": {"ending_24h_hours": 24, "ending_48h_hours": 48,
                                                     "message_cap_chars": 4000}},
                                      self.conn, dry=False)

        # every part is delivered, in order
        self.assertEqual([m for _, m in self.sent], ["part one", "part two"])
        # the documented return contract still holds
        self.assertEqual(msg, "part one")
        self.assertIn("c1", [c["id"] for c in state["new"]])

        # this row is what the crash prevented: it is written after the sends
        row = self.conn.execute(
            "SELECT payload FROM events WHERE kind='digest_sent'").fetchone()
        self.assertIsNotNone(row, "digest_sent event was never recorded")
        self.assertEqual(json.loads(row["payload"])["chars"],
                         len("part one") + len("part two"))
        self.assertTrue(dbm.ledger_has(self.conn, "c1", "NEW_CAMPAIGN"))

    def test_silent_run_still_returns_early(self):
        main.wd.compose_digests_multipart = lambda *a, **k: []
        msg, state = main._send_digest([], {"telegram": {"group_chat_id": "42"}, "timezone": "UTC",
                                        "watchdog": {"ending_24h_hours": 24, "ending_48h_hours": 48,
                                                     "message_cap_chars": 4000}},
                                      self.conn, dry=False)
        self.assertIsNone(msg)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.conn.execute(
            "SELECT payload FROM events WHERE kind='digest_sent'").fetchone())


if __name__ == "__main__":
    unittest.main()

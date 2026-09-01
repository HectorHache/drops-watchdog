#!/usr/bin/env python3
"""Unit tests: normalization, classification, digest composer. Stdlib only."""
import datetime
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import twitch_client as tc
import watchdog as wd

FIXTURE = ROOT / "tests" / "fixtures" / "sample_rich.json"
NOW = datetime.datetime(2026, 9, 1, 17, 30, 0, tzinfo=datetime.timezone.utc)
TZ = "Europe/Madrid"


def load_fixture_campaigns():
    raw = json.loads(FIXTURE.read_text())
    return [tc.normalize(c) for c in raw["payload"]["data"]["currentUser"]["dropCampaigns"]]


class TestNormalize(unittest.TestCase):
    def test_fields_and_rewards(self):
        c = load_fixture_campaigns()[0]
        self.assertEqual(c["id"], "a7bdc2ec-dc51-4a17-9fdd-6f6331e6407b")
        self.assertEqual(c["game_name"], "Delta Force")
        self.assertEqual(c["status"], "ACTIVE")
        self.assertEqual(c["start_at"], "2026-09-01T14:45:00+00:00")
        self.assertEqual(len(c["rewards"]), 2)
        self.assertEqual(c["rewards"][0]["name"], "Spray Paint - Hol' Up")
        self.assertEqual(c["rewards"][0]["required_minutes"], 15)
        self.assertTrue(c["image_url"].startswith("https://"))
        self.assertTrue("details_url" in c and "drop=" in c["details_url"])

    def test_missing_image_falls_back_to_boxart_or_empty(self):
        camps = load_fixture_campaigns()
        self.assertTrue(camps[1]["image_url"].startswith("https://"))  # boxArt fallback
        self.assertEqual(camps[3]["image_url"], "")                     # neither


class TestClassify(unittest.TestCase):
    def test_first_sight_all_new(self):
        camps = load_fixture_campaigns()
        state = wd.classify({}, camps, NOW)
        self.assertEqual(len(state["new"]), 3)          # 3 active
        self.assertEqual(len(state["closed_only"]), 1)  # Rise Online EXPIRED never seen
        self.assertEqual(len(state["ended"]), 0)
        self.assertEqual(state["active_count"], 3)

    def test_ending_windows(self):
        camps = load_fixture_campaigns()
        state = wd.classify({}, camps, NOW)
        # Delta (1h59m) + Kirka (5h59m) + Brawlhalla (10h29m) all < 24h; none 24-48h
        self.assertEqual(len(state["ending_24"]), 3)
        self.assertEqual(len(state["ending_48"]), 0)

    def test_transition_to_ended(self):
        camps = load_fixture_campaigns()
        known = {
            camps[0]["id"]: {**camps[0], "status": "ACTIVE"},
            camps[1]["id"]: {**camps[1], "status": "ACTIVE"},
            camps[3]["id"]: {**camps[3], "status": "ACTIVE"},   # was ACTIVE, now EXPIRED
        }
        live = [camps[0], camps[1], camps[3]]
        state = wd.classify(known, live, NOW)
        self.assertEqual(len(state["ended"]), 1)
        self.assertEqual(state["ended"][0]["id"], camps[3]["id"])

    def test_no_false_prune_on_missing(self):
        camps = load_fixture_campaigns()
        known = {camps[0]["id"]: {**camps[0]}}
        live = []  # empty/glitch fetch
        state = wd.classify(known, live, NOW)
        self.assertEqual(state["ended"], [])
        self.assertEqual(state["disappeared"], [camps[0]["id"]])  # event only, no alert

    def test_material_change_end_at(self):
        camps = load_fixture_campaigns()
        known = {camps[0]["id"]: {**camps[0], "end_at": "2026-09-01T23:59:59+00:00"}}
        live = [camps[0]]  # end moved from 19:29 to 23:59
        state = wd.classify(known, live, NOW)
        self.assertEqual(len(state["changed"]), 1)


class TestCompose(unittest.TestCase):
    def test_silent_when_nothing_changed(self):
        state = {"new": [], "ending_24": [], "ending_48": [], "ended": [],
                 "changed": [], "active_count": 5}
        self.assertIsNone(wd.compose_digest(state, NOW, TZ))

    def test_escaping(self):
        camps = load_fixture_campaigns()
        state = {"new": [camps[2]], "ending_24": [], "ending_48": [], "ended": [],
                 "changed": [], "active_count": 3}
        msg = wd.compose_digest(state, NOW, TZ)
        self.assertIn("Brawlhalla &amp; Friends", msg)      # & escaped
        self.assertIn("&lt;Gold&gt;", msg)                   # < > escaped in reward
        self.assertNotIn("<Gold>", msg)
        self.assertIn("NEW (1)", msg)

    def test_sections_present(self):
        camps = load_fixture_campaigns()
        state = {"new": [camps[2]], "ending_24": [camps[0], camps[1]],
                 "ending_48": [], "ended": [camps[3]],
                 "changed": [], "active_count": 3}
        msg = wd.compose_digest(state, NOW, TZ)
        self.assertIn("ENDING &lt;24H (2)", msg)
        self.assertIn("ENDED (1)", msg)
        self.assertIn("Delta Force", msg)
        self.assertIn("drops.hache.app", msg)

    def test_cap(self):
        camps = load_fixture_campaigns()
        many = [camps[0]] * 200  # force overflow
        state = {"new": many, "ending_24": [], "ending_48": [], "ended": [],
                 "changed": [], "active_count": 200}
        msg = wd.compose_digest(state, NOW, TZ, cap=1200)
        self.assertLessEqual(len(msg), 1260)
        self.assertIn("+ more", msg)

    def test_silent_when_only_already_alerted_in_window(self):
        # campaigns still in <24h but ALL already alerted + nothing else new -> silent
        camps = load_fixture_campaigns()
        state = {"new": [], "ending_24": [camps[0], camps[1]], "ending_48": [],
                 "ended": [], "changed": [], "active_count": 3}
        msg = wd.compose_digest(state, NOW, TZ, ledger_has=lambda cid, kind: True)
        self.assertIsNone(msg)

    def test_triggers_on_newly_entered_24h(self):
        camps = load_fixture_campaigns()
        state = {"new": [], "ending_24": [camps[0], camps[1]], "ending_48": [],
                 "ended": [], "changed": [], "active_count": 3}
        alerted = {camps[0]["id"]}  # only the first was already alerted
        msg = wd.compose_digest(state, NOW, TZ,
                                ledger_has=lambda cid, kind: cid in alerted)
        self.assertIsNotNone(msg)
        self.assertIn("ENDING &lt;24H (2)", msg)  # full actionable list still shown

    def test_favorites_star(self):
        camps = load_fixture_campaigns()
        state = {"new": [], "ending_24": [camps[0]], "ending_48": [], "ended": [],
                 "changed": [], "active_count": 1}
        msg = wd.compose_digest(state, NOW, TZ, favorites={"Delta Force"})
        self.assertIn("⭐", msg)


if __name__ == "__main__":
    unittest.main()

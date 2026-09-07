#!/usr/bin/env python3
"""Unit tests for Phase 2: matcher, feasibility calculation, and multipart digest splitting."""
import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matcher as mt
import watchdog as wd


class TestMatcher(unittest.TestCase):
    def test_trademark_and_symbols(self):
        self.assertTrue(mt.matches_game("Apex Legends™", "Apex Legends"))
        self.assertTrue(mt.matches_game("Tom Clancy's The Division® 2", "The Division 2"))

    def test_aliases_and_sequels(self):
        self.assertTrue(mt.matches_game("Counter-Strike 2", "Counter-Strike"))
        self.assertTrue(mt.matches_game("Overwatch 2", "Overwatch"))
        self.assertTrue(mt.matches_game("Dying Light: The Beast", "Dying Light"))
        self.assertTrue(mt.matches_game("Path of Exile 2", "Path of Exile"))

    def test_favorites_and_owned_matching(self):
        favs = {"Counter-Strike", "Warframe", "Rust"}
        self.assertTrue(mt.is_favorite_match("Counter-Strike 2", favs))
        self.assertTrue(mt.is_favorite_match("Warframe", favs))
        self.assertFalse(mt.is_favorite_match("Valorant", favs))

    def test_distinct_games_do_not_match(self):
        self.assertFalse(mt.matches_game("Starfield", "Starbound"))
        self.assertFalse(mt.matches_game("Valorant", "Counter-Strike"))


class TestFeasibility(unittest.TestCase):
    def test_impossible_when_insufficient_time(self):
        now = datetime.datetime(2026, 9, 7, 12, 0, 0, tzinfo=datetime.timezone.utc)
        # 1 hour left, but needs 180 min (3h)
        c = {
            "end_at": "2026-09-07T13:00:00+00:00",
            "rewards": [{"name": "Skin A", "required_minutes": 180}],
        }
        feas = wd.calc_feasibility(c, now)
        self.assertFalse(feas["feasible"])
        self.assertEqual(feas["status"], "impossible")
        self.assertIn("Impossible", feas["note"])

    def test_tight_window(self):
        now = datetime.datetime(2026, 9, 7, 12, 0, 0, tzinfo=datetime.timezone.utc)
        # 2 hours left, needs 90 min (30 min buffer)
        c = {
            "end_at": "2026-09-07T14:00:00+00:00",
            "rewards": [{"name": "Skin A", "required_minutes": 90}],
        }
        feas = wd.calc_feasibility(c, now)
        self.assertTrue(feas["feasible"])
        self.assertEqual(feas["status"], "tight")
        self.assertIn("Tight window", feas["note"])

    def test_ample_buffer(self):
        now = datetime.datetime(2026, 9, 7, 12, 0, 0, tzinfo=datetime.timezone.utc)
        # 24 hours left, needs 60 min
        c = {
            "end_at": "2026-09-08T12:00:00+00:00",
            "rewards": [{"name": "Skin A", "required_minutes": 60}],
        }
        feas = wd.calc_feasibility(c, now)
        self.assertTrue(feas["feasible"])
        self.assertEqual(feas["status"], "ok")
        self.assertIn("Claimable", feas["note"])


class TestMultipartDigest(unittest.TestCase):
    def test_splits_oversized_digest(self):
        now = datetime.datetime(2026, 9, 7, 12, 0, 0, tzinfo=datetime.timezone.utc)
        # Create many synthetic new campaigns to force overflow
        many_camps = []
        for i in range(40):
            many_camps.append({
                "id": f"camp-{i}",
                "game_name": f"Game Title Number {i}",
                "title": f"Special Launch Event Drop Campaign {i}",
                "status": "ACTIVE",
                "start_at": "2026-09-07T10:00:00+00:00",
                "end_at": "2026-09-08T10:00:00+00:00",
                "details_url": f"https://www.twitch.tv/drops/campaigns?drop={i}",
                "rewards": [{"name": f"Exclusive Reward {i}", "required_minutes": 60}],
            })
        state = {
            "new": many_camps,
            "ending_24": [],
            "ending_48": [],
            "ended": [],
            "changed": [],
            "active_count": 40,
        }
        parts = wd.compose_digests_multipart(state, now, "Europe/Madrid", cap=1500)
        self.assertGreater(len(parts), 1)
        self.assertIn("[Part 1/", parts[0])
        self.assertIn("[Part 2/", parts[1])
        for p in parts:
            self.assertLessEqual(len(p), 1800)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Unit tests: site builder — drops.json structure/determinism, RSS feed, favicon sync."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import site_builder as sb


def _camp(cid, gname, status="ACTIVE", start="2026-09-01T10:00:00+00:00",
          end="2099-01-01T10:00:00+00:00", rewards=None):
    return {
        "id": cid, "game_id": "g", "game_name": gname, "title": gname + " drop",
        "status": status, "start_at": start, "end_at": end,
        "image_url": None, "details_url": "https://twitch.tv/drops/campaigns?drop=" + cid,
        "channels_summary": None,
        "rewards": rewards or [{"name": "Skin", "required_minutes": 60}],
    }


class TestBuildDropsJson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.init_db(Path(self.tmp.name) / "drops.db")
        dbm.upsert_campaign(self.conn, _camp("a1", "Alpha"))
        dbm.upsert_campaign(self.conn, _camp("b1", "Beta", status="CLOSED",
                                             start="2026-08-01T10:00:00+00:00",
                                             end="2026-08-02T10:00:00+00:00"))
        # expired-but-not-CLOSED row (end passed, status still ACTIVE) -> archived ENDED
        dbm.upsert_campaign(self.conn, _camp("c1", "Gamma", status="ACTIVE",
                                             start="2026-07-01T10:00:00+00:00",
                                             end="2026-07-02T10:00:00+00:00"))
        dbm.meta_set(self.conn, "op", "unused")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_active_archive_split(self):
        data = sb.build_drops_json(self.conn, None)
        self.assertEqual([c["id"] for c in data["campaigns"]], ["a1"])
        self.assertEqual([c["id"] for c in data["archive"]], ["b1", "c1"])  # endAt desc
        # display status: every past-end campaign reads ENDED (CLOSED is internal)
        self.assertEqual({c["status"] for c in data["archive"]}, {"ENDED"})

    def test_deterministic_sorted_output(self):
        first = sb.build_drops_json(self.conn, None)
        second = sb.build_drops_json(self.conn, None)
        self.assertEqual(first, second)
        # keys that would break commit-on-change determinism must not exist
        for entry in first["campaigns"]:
            self.assertNotIn("hoursLeft", entry)
            self.assertNotIn("isNew", entry)
        for entry in first["archive"]:
            self.assertNotIn("isFavorite", entry)

    def test_rewards_mapped_with_minutes_alias(self):
        data = sb.build_drops_json(self.conn, None)
        entry = data["campaigns"][0]
        self.assertEqual(entry["rewards"], [{"name": "Skin", "minutes": 60}])

    def test_favorite_and_steam_flags(self):
        self.conn.execute("INSERT OR IGNORE INTO favorites (game_name, weight, instant_alert, source, created_at) "
                          "VALUES ('Alpha',10,1,'manual','2026-09-01T00:00:00Z')")
        self.conn.execute("INSERT OR IGNORE INTO steam_games (appid, name, owner, created_at) "
                          "VALUES (1,'Alpha','mick','2026-09-01T00:00:00Z')")
        self.conn.commit()
        data = sb.build_drops_json(self.conn, None)
        entry = data["campaigns"][0]
        self.assertTrue(entry["isFavorite"])
        self.assertTrue(entry["inSteamLibrary"])
        self.assertIn("Alpha", data["favorites"])

    def test_signals_and_weights_keys_present(self):
        data = sb.build_drops_json(self.conn, None)
        self.assertIn("signals", data)
        self.assertIn("favoriteWeights", data)
        self.assertEqual(data["tz"], "Europe/Madrid")


class TestBuildFeed(unittest.TestCase):
    def test_escaping_in_items(self):
        data = {"campaigns": [{
            "id": "x&<1", "game": "G & H", "title": "Title <Gold>",
            "startAt": "2026-09-01T10:00:00+00:00", "endAt": "2026-09-05T10:00:00+00:00",
            "detailsUrl": "https://twitch.tv/drops/campaigns?drop=x&<1",
            "rewards": [{"name": "A & B"}],
        }]}
        feed = sb.build_feed(data)
        self.assertIn("G &amp; H", feed)
        self.assertIn("Title &lt;Gold&gt;", feed)
        self.assertNotIn("<Gold>", feed)
        self.assertIn("<rss version=\"2.0\">", feed)
        self.assertIn("<guid isPermaLink=\"false\">x&amp;&lt;1</guid>", feed)

    def test_empty_campaigns_yields_valid_channel(self):
        feed = sb.build_feed({"campaigns": []})
        self.assertIn("<channel>", feed)
        self.assertIn("</channel>", feed)


class TestSyncFavicons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.media = Path(self.tmp.name) / "media"
        self.docs = Path(self.tmp.name) / "docs"
        self.media.mkdir()
        self.docs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_copies_present_and_prunes_missing(self):
        (self.media / "favicon.ico").write_bytes(b"ico")
        (self.media / "favicon-16x16.png").write_bytes(b"png16")
        with mock.patch.object(sb, "MEDIA", self.media), \
                mock.patch.object(sb, "DOCS", self.docs):
            copied = sb.sync_favicons()
        self.assertEqual(sorted(copied), ["favicon-16x16.png", "favicon.ico"])
        self.assertTrue((self.docs / "manifest.json").exists())
        self.assertTrue((self.docs / "site.webmanifest").exists())
        self.assertEqual((self.docs / "favicon.ico").read_bytes(), b"ico")

        # source file disappears -> docs copy pruned on next build (keeps docs/ in sync)
        (self.media / "favicon.ico").unlink()
        with mock.patch.object(sb, "MEDIA", self.media), \
                mock.patch.object(sb, "DOCS", self.docs):
            sb.sync_favicons()
        self.assertFalse((self.docs / "favicon.ico").exists())
        self.assertTrue((self.docs / "favicon-16x16.png").exists())


if __name__ == "__main__":
    unittest.main()
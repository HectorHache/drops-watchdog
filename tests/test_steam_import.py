#!/usr/bin/env python3
"""Unit tests: Steam import — credential resolution, API parsing, top-played, auto-pick."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import steam_import as si


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key_file = Path(self.tmp.name) / ".steamMine"
        self.friend_file = Path(self.tmp.name) / ".steamFriend"

    def tearDown(self):
        self.tmp.cleanup()

    def test_api_key_bare_hex(self):
        self.key_file.write_text("abcdef0123456789abcdef0123456789\n")
        with mock.patch.object(si, "STEAM_KEY_FILE", self.key_file):
            self.assertEqual(si.api_key(), "abcdef0123456789abcdef0123456789")

    def test_api_key_json_wrapper(self):
        self.key_file.write_text(json.dumps({"api_key": "k3y"}))
        with mock.patch.object(si, "STEAM_KEY_FILE", self.key_file):
            self.assertEqual(si.api_key(), "k3y")

    def test_api_key_missing_file_raises(self):
        with mock.patch.object(si, "STEAM_KEY_FILE", self.key_file):
            with self.assertRaises(si.SteamError):
                si.api_key()

    def test_friend_steamid_env_wins(self):
        self.friend_file.write_text("11111111111111111\n")
        with mock.patch.object(si, "FRIEND_STEAMID_FILE", self.friend_file), \
                mock.patch.dict("os.environ", {"HOUSEHOLD2_STEAMID": "22222222222222222"}, clear=False):
            self.assertEqual(si.friend_steamid(), "22222222222222222")

    def test_friend_steamid_from_file(self):
        self.friend_file.write_text("33333333333333333\n")
        with mock.patch.object(si, "FRIEND_STEAMID_FILE", self.friend_file), \
                mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(si.friend_steamid(), "33333333333333333")

    def test_friend_steamid_rejects_bad_length(self):
        self.friend_file.write_text("12345\n")
        with mock.patch.object(si, "FRIEND_STEAMID_FILE", self.friend_file), \
                mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(si.SteamError):
                si.friend_steamid()


class TestApiParsing(unittest.TestCase):
    @mock.patch.object(si, "_get")
    def test_resolve_vanity_success(self, get):
        get.return_value = {"response": {"success": 1, "steamid": "7656119"}}
        self.assertEqual(si.resolve_vanity("d4rkwar", "k"), "7656119")

    @mock.patch.object(si, "_get")
    def test_resolve_vanity_failure_returns_none(self, get):
        get.return_value = {"response": {"success": 42}}
        self.assertIsNone(si.resolve_vanity("unknown", "k"))

    @mock.patch.object(si, "_get")
    def test_get_owned_games_filters_and_coerces(self, get):
        get.return_value = {"response": {"games": [
            {"appid": 10, "name": " Alpha ", "playtime_forever": 120, "playtime_2weeks": 30},
            {"appid": 20, "name": "", "playtime_forever": 5},          # nameless -> dropped
            {"appid": None, "name": None, "playtime_forever": 9},      # missing -> dropped
            {"appid": 40, "name": "Beta"},                              # missing playtimes -> 0
        ]}}
        games = si.get_owned_games("sid", "k")
        self.assertEqual(len(games), 2)
        self.assertEqual(games[0]["appid"], 10)
        self.assertEqual(games[0]["name"], "Alpha")
        self.assertEqual(games[0]["playtime_forever"], 120)
        self.assertEqual(games[1]["playtime_forever"], 0)


class TestTopPlayedAndPick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.init_db(Path(self.tmp.name) / "drops.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed_library(self):
        dbm.replace_steam_games(self.conn, "mick", [
            {"appid": 1, "name": "Most Played", "playtime_forever": 600, "playtime_2weeks": 60},
            {"appid": 2, "name": "Medium", "playtime_forever": 100, "playtime_2weeks": 0},
            {"appid": 3, "name": "Fringe", "playtime_forever": 10, "playtime_2weeks": 0},
        ])

    def test_get_top_played_hours_sort(self):
        self._seed_library()
        top = si.get_top_played(self.conn, min_hours=0.0)
        self.assertEqual([g["name"] for g in top], ["Most Played", "Medium", "Fringe"])
        self.assertEqual(top[0]["hours"], 10.0)
        self.assertEqual(top[0]["hours_2weeks"], 1.0)

    def test_get_top_played_min_hours_filter_and_limit(self):
        self._seed_library()
        top = si.get_top_played(self.conn, min_hours=2.0, limit=1)
        self.assertEqual([g["name"] for g in top], ["Most Played"])

    def test_auto_seed_favorites_from_playtime(self):
        self._seed_library()
        si.cmd_steam_pick(self.conn, auto_hours=1.5, limit=10)
        names = {r["game_name"] for r in self.conn.execute("SELECT game_name FROM favorites")}
        self.assertEqual(names, {"Most Played", "Medium"})  # 10h + 1.7h qualify; Fringe (0.2h) not
        src = self.conn.execute("SELECT source FROM favorites WHERE game_name='Most Played'").fetchone()
        self.assertEqual(src["source"], "steam_playtime")

    def test_auto_seed_idempotent(self):
        self._seed_library()
        si.cmd_steam_pick(self.conn, auto_hours=0.5, limit=10)
        si.cmd_steam_pick(self.conn, auto_hours=0.5, limit=10)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) c FROM favorites").fetchone()["c"], 2)

    def test_no_played_games_message(self):
        self.assertEqual(si.get_top_played(self.conn), [])
        self.assertIsNone(si.cmd_steam_pick(self.conn, auto_hours=0.5))

    def test_replace_roundtrip_keeps_playtimes(self):
        self._seed_library()
        rows = dbm.get_steam_played_games(self.conn, "mick", min_minutes=0)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["playtime_forever_min"], 600)
        self.assertEqual(dbm.get_steam_count(self.conn, "mick"), 3)


if __name__ == "__main__":
    unittest.main()
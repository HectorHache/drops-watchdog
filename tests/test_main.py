#!/usr/bin/env python3
"""Unit tests: orchestrator wiring — config parser, favorites CLI, fixture dryrun,
and the pre-seed snapshot regression (material changes must survive seeding)."""
import argparse
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import main
import watchdog as wd

FIXTURE = ROOT / "tests" / "fixtures" / "sample_rich.json"
NOW = datetime(2026, 9, 1, 17, 30, 0, tzinfo=timezone.utc)
CFG = {
    "timezone": "Europe/Madrid",
    "watchdog": {"ending_24h_hours": 24, "ending_48h_hours": 48, "message_cap_chars": 4000},
    "telegram": {"group_chat_id": "42", "dm_chat_id": "7"},
    "auth": {"max_age_days": 25},
}


def _camp(cid="c1", end="2026-09-02T10:00:00+00:00", rewards=None):
    return {
        "id": cid, "game_id": "g1", "game_name": "Delta Force", "title": "Drop",
        "status": "ACTIVE", "start_at": "2026-09-01T10:00:00+00:00", "end_at": end,
        "image_url": None, "details_url": "https://twitch.tv/drops/campaigns?drop=" + cid,
        "channels_summary": None,
        "rewards": rewards or [{"name": "Skin A", "required_minutes": 60}],
    }


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "drops.db"
        self.conn = dbm.init_db(self.dbp)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

class TestParseSimpleYaml(unittest.TestCase):
    """The stdlib YAML-subset parser backing config loading."""

    def test_scalar_types(self):
        text = "\n".join([
            "name: Drops Watchdog",
            "enabled: true",
            "off: false",
            "count: 42",
            "ratio: 3.5",
            "nothing: null",
            "tilde: ~",
        ])
        cfg = main._parse_simple_yaml(text)
        self.assertEqual(cfg["name"], "Drops Watchdog")
        self.assertIs(cfg["enabled"], True)
        self.assertIs(cfg["off"], False)
        self.assertEqual(cfg["count"], 42)
        self.assertEqual(cfg["ratio"], 3.5)
        self.assertIsNone(cfg["nothing"])
        self.assertIsNone(cfg["tilde"])

    def test_nested_blocks(self):
        text = "\n".join([
            "watchdog:",
            "  ending_24h_hours: 24",
            "  message_cap_chars: 3800",
            "site:",
            "  base_url: https://drops.hector.app",
        ])
        cfg = main._parse_simple_yaml(text)
        self.assertEqual(cfg["watchdog"]["ending_24h_hours"], 24)
        self.assertEqual(cfg["watchdog"]["message_cap_chars"], 3800)
        self.assertEqual(cfg["site"]["base_url"], "https://drops.hector.app")
        cfg2 = main._parse_simple_yaml("site:\n  base_url: x")
        self.assertIn("base_url", cfg2["site"])

    def test_comments_ignored(self):
        text = "a: 1  # trailing comment\n# full comment line\nb: 2\n"
        cfg = main._parse_simple_yaml(text)
        self.assertEqual(cfg, {"a": 1, "b": 2})

    def test_quoted_values(self):
        cfg = main._parse_simple_yaml("\n".join([
            "group_chat_id: \"-1003773674983\"",
            "token: 'abc123'",
        ]))
        self.assertEqual(cfg["group_chat_id"], "-1003773674983")
        self.assertEqual(cfg["token"], "abc123")


class TestCmdFavorite(_DbCase):
    """favorite add/rm/list roundtrip against a temp DB (no site push)."""

    def setUp(self):
        super().setUp()
        self.patchers = [
            mock.patch.object(main, "db_path", return_value=self.dbp),
            mock.patch.object(main.sb, "cmd_build"),
            mock.patch.object(main, "commit_on_change", return_value=False),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        super().tearDown()

    def test_add_list_rm(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            main.cmd_favorite(argparse.Namespace(fcmd="add", game="Warframe", dry=True))
            main.cmd_favorite(argparse.Namespace(fcmd="add", game="Warframe", dry=True))  # idempotent
            main.cmd_favorite(argparse.Namespace(fcmd="add", game="Rust", dry=True))
            main.cmd_favorite(argparse.Namespace(fcmd="list", game=None, dry=True))
        out = buf.getvalue()
        self.assertIn("favorites (2)", out)
        self.assertIn("Warframe", out)
        self.assertIn("Rust", out)
        main.cmd_favorite(argparse.Namespace(fcmd="rm", game="Warframe", dry=True))
        rows = self.conn.execute("SELECT game_name FROM favorites").fetchall()
        self.assertEqual([r["game_name"] for r in rows], ["Rust"])

    def test_missing_game_raises(self):
        with self.assertRaises(SystemExit):
            main.cmd_favorite(argparse.Namespace(fcmd="add", game=None, dry=True))


class TestFixtureDryrun(unittest.TestCase):
    """End-to-end offline path: fixture -> normalize -> classify -> digest."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = Path(self.tmp.name) / "drops.db"
        self.patchers = [
            mock.patch.object(main, "load_config", return_value=CFG),
            mock.patch.object(main, "db_path", return_value=self.dbp),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.tmp.cleanup()

    def test_fixture_dryrun_classifies_and_composes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            main.cmd_dryrun(argparse.Namespace(fixture=str(FIXTURE)))
        out = buf.getvalue()
        self.assertIn("4 campaigns (offline, read-only)", out)
        # the fixture's campaigns all ended 2026-09-01 — against today's clock
        # they classify as never-seen closed entries -> silent run (read-only)
        self.assertIn("closed_only=4", out)
        self.assertIn("SILENT RUN", out)

    def test_fixture_dryrun_writes_nothing(self):
        dbm.init_db(self.dbp)  # ensure file exists
        before = self.dbp.read_bytes()
        main.cmd_dryrun(argparse.Namespace(fixture=str(FIXTURE)))
        self.assertEqual(self.dbp.read_bytes(), before)


class TestPipelineChangeDetection(_DbCase):
    """Regression: the scheduled pipeline seeds BEFORE classifying, so change
    detection must use a pre-seed snapshot — post-seed knowledge is the new
    state and can never reveal a material change."""

    def test_seed_then_classify_with_pre_snapshot_fires_changed(self):
        live = [_camp("c1", end="2026-09-03T10:00:00+00:00")]
        dbm.upsert_campaign(self.conn, _camp("c1", end="2026-09-01T10:00:00+00:00"))
        known_pre = dbm.get_campaigns_with_rewards(self.conn)

        # seed rewrites the DB to the live state...
        dbm.upsert_campaign(self.conn, live[0])

        # ...so a post-seed snapshot sees no diff (the pre-fix behavior: silent)
        stale = wd.classify(dbm.get_campaigns_with_rewards(self.conn), live, NOW)
        self.assertEqual(stale["changed"], [])

        # the pre-seed snapshot still sees end_at moved -> changed fires
        fixed = wd.classify(known_pre, live, NOW)
        self.assertEqual(len(fixed["changed"]), 1)

    def test_reward_delta_detected_via_pre_snapshot(self):
        live = [_camp("c1", rewards=[
            {"name": "Skin A", "required_minutes": 60},
            {"name": "Skin B", "required_minutes": 120},
        ])]
        dbm.upsert_campaign(self.conn, live[0])
        known_pre = dbm.get_campaigns_with_rewards(self.conn)

        # Twitch adds a mid-campaign drop on the next run
        grown = [_camp("c1", rewards=[
            {"name": "Skin A", "required_minutes": 60},
            {"name": "Skin B", "required_minutes": 120},
            {"name": "Skin C", "required_minutes": 45},
        ])]
        state = wd.classify(known_pre, grown, NOW)
        self.assertEqual(len(state["changed"]), 1)

        # same reward set -> no change (order-independent)
        shuffled = [_camp("c1", rewards=[
            {"name": "Skin B", "required_minutes": 120},
            {"name": "Skin A", "required_minutes": 60},
        ])]
        state2 = wd.classify(known_pre, shuffled, NOW)
        self.assertEqual(state2["changed"], [])

    def test_send_digest_dry_with_known_snapshot(self):
        # far-future ends: _send_digest uses the real clock, so fixtures must
        # stay ACTIVE regardless of when the suite runs
        live = [_camp("c1", end="2099-01-03T10:00:00+00:00")]
        dbm.upsert_campaign(self.conn, _camp("c1", end="2099-01-01T10:00:00+00:00"))
        known_pre = dbm.get_campaigns_with_rewards(self.conn)

        # simulate the production sequence: seed writes the new state...
        dbm.upsert_campaign(self.conn, live[0])

        # ...post-seed read (known=None) can't see the change -> silent (pre-fix)
        msg_stale, _ = main._send_digest(live, CFG, self.conn, dry=True)
        self.assertIsNone(msg_stale)
        # pre-seed snapshot still sees the end_at shift -> digest fires
        msg_fixed, state = main._send_digest(live, CFG, self.conn, dry=True, known=known_pre)
        self.assertIsNotNone(msg_fixed)
        self.assertIn("CHANGED (1)", msg_fixed)


if __name__ == "__main__":
    unittest.main()

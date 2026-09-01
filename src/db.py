#!/usr/bin/env python3
"""SQLite layer — drops.db is the single source of truth. Stdlib only."""
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
  id            TEXT PRIMARY KEY,
  game_id       TEXT,
  game_name     TEXT NOT NULL,
  title         TEXT NOT NULL,
  status        TEXT NOT NULL,             -- ACTIVE|UPCOMING|EXPIRED|CLOSED
  start_at      TEXT NOT NULL,             -- ISO-8601 UTC
  end_at        TEXT NOT NULL,
  image_url     TEXT,
  details_url   TEXT,
  channels_summary TEXT,
  raw_json      TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  is_closed     INTEGER DEFAULT 0,
  archived_at   TEXT
);
-- NOTE: campaign rows + rewards are NEVER deleted (history kept forever).
CREATE TABLE IF NOT EXISTS rewards (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id   TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  required_minutes INTEGER DEFAULT 0,
  image_url     TEXT,
  sort          INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notification_ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id   TEXT NOT NULL REFERENCES campaigns(id),
  kind          TEXT NOT NULL,             -- NEW_CAMPAIGN|ENDING_24H|ENDING_48H|ENDED|PERSONAL|ERROR
  sent_at       TEXT NOT NULL,
  channel       TEXT NOT NULL,
  payload       TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  kind          TEXT NOT NULL,             -- new|ending_24h|ended|changed|fetch_error|auth_error|disappeared
  campaign_id   TEXT,
  payload       TEXT
);
CREATE TABLE IF NOT EXISTS favorites (
  game_name     TEXT PRIMARY KEY,
  weight        INTEGER DEFAULT 10,
  instant_alert INTEGER DEFAULT 1,
  source        TEXT DEFAULT 'manual',
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv_interactions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  kind          TEXT NOT NULL,             -- favorite|like|remind
  campaign_id   TEXT,
  value         TEXT
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_campaigns_status_end ON campaigns(status, end_at);
CREATE INDEX IF NOT EXISTS idx_ledger_lookup ON notification_ledger(campaign_id, kind);
"""


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- campaigns ----------

def get_campaigns(conn, statuses=None) -> dict:
    q = "SELECT * FROM campaigns"
    params = ()
    if statuses:
        marks = ",".join("?" * len(statuses))
        q += f" WHERE status IN ({marks})"
        params = tuple(statuses)
    return {r["id"]: dict(r) for r in conn.execute(q, params)}


def upsert_campaign(conn, c: dict) -> str:
    """Insert or update a campaign row. Returns 'inserted' | 'updated'."""
    existing = conn.execute("SELECT 1 FROM campaigns WHERE id=?", (c["id"],)).fetchone()
    now = now_iso()
    if existing is None:
        conn.execute(
            """INSERT INTO campaigns
               (id, game_id, game_name, title, status, start_at, end_at, image_url,
                details_url, channels_summary, raw_json, first_seen_at, last_seen_at,
                is_closed, archived_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,NULL)""",
            (c["id"], c.get("game_id"), c["game_name"], c["title"], c["status"],
             c["start_at"], c["end_at"], c.get("image_url"), c.get("details_url"),
             c.get("channels_summary"), c.get("raw_json"), now, now),
        )
        result = "inserted"
    else:
        conn.execute(
            """UPDATE campaigns SET game_id=?, game_name=?, title=?, status=?, start_at=?,
               end_at=?, image_url=?, details_url=?, channels_summary=?, raw_json=?,
               last_seen_at=? WHERE id=?""",
            (c.get("game_id"), c["game_name"], c["title"], c["status"],
             c["start_at"], c["end_at"], c.get("image_url"), c.get("details_url"),
             c.get("channels_summary"), c.get("raw_json"), now, c["id"]),
        )
        result = "updated"
    _replace_rewards(conn, c["id"], c.get("rewards", []))
    conn.commit()
    return result


def _replace_rewards(conn, campaign_id, rewards):
    conn.execute("DELETE FROM rewards WHERE campaign_id=?", (campaign_id,))
    for i, r in enumerate(rewards):
        conn.execute(
            "INSERT INTO rewards (campaign_id, name, required_minutes, image_url, sort) VALUES (?,?,?,?,?)",
            (campaign_id, r.get("name"), r.get("required_minutes") or 0, r.get("image_url"), i),
        )


def set_closed(conn, campaign_id, archived_at=None):
    conn.execute("UPDATE campaigns SET status='CLOSED', is_closed=1, archived_at=? WHERE id=?",
                 (archived_at or now_iso(), campaign_id))
    conn.commit()


# ---------- events / ledger ----------

def record_event(conn, kind, campaign_id=None, payload=None):
    conn.execute("INSERT INTO events (ts, kind, campaign_id, payload) VALUES (?,?,?,?)",
                 (now_iso(), kind, campaign_id, json.dumps(payload) if payload is not None else None))
    conn.commit()


def ledger_has(conn, campaign_id, kind) -> bool:
    return conn.execute(
        "SELECT 1 FROM notification_ledger WHERE campaign_id=? AND kind=?",
        (campaign_id, kind)).fetchone() is not None


def ledger_add(conn, campaign_id, kind, channel, payload=None):
    conn.execute(
        "INSERT INTO notification_ledger (campaign_id, kind, sent_at, channel, payload) VALUES (?,?,?,?,?)",
        (campaign_id, kind, now_iso(), channel, json.dumps(payload) if payload is not None else None))
    conn.commit()


# ---------- meta ----------

def meta_get(conn, key, default=None):
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def meta_set(conn, key, value):
    conn.execute("INSERT INTO meta (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (key, str(value)))
    conn.commit()

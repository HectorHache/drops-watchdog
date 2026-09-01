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
CREATE TABLE IF NOT EXISTS steam_games (
  appid         INTEGER NOT NULL,
  name          TEXT NOT NULL,
  owner         TEXT NOT NULL,             -- mick|wifey
  created_at    TEXT NOT NULL,
  PRIMARY KEY (appid, owner)
);
CREATE INDEX IF NOT EXISTS idx_steam_name ON steam_games(name);
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


def _end_in_future(end_at) -> bool:
    if not end_at:
        return False
    try:
        d = datetime.datetime.fromisoformat(str(end_at).replace("Z", "+00:00"))
    except Exception:
        return False
    return d > datetime.datetime.now(datetime.timezone.utc)


def upsert_campaign(conn, c: dict) -> str:
    """Insert or update a campaign row. Returns 'inserted' | 'updated'."""
    existing = conn.execute("SELECT status FROM campaigns WHERE id=?", (c["id"],)).fetchone()
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
        # Once CLOSED, never resurrect to an open status unless the feed genuinely
        # re-opens it (end_at back in the future). Twitch keeps returning ended
        # campaigns as EXPIRED, which would otherwise flip-flop CLOSED<->EXPIRED
        # every seed and re-fire close_ended + site pushes each run.
        feed_status = c["status"]
        if existing["status"] == "CLOSED":
            end_ok = _end_in_future(c.get("end_at"))
            if feed_status in ("ACTIVE", "UPCOMING") and end_ok:
                pass  # genuine re-open: allow transition back
            else:
                feed_status = "CLOSED"   # keep closed (ended or still EXPIRED)
        conn.execute(
            """UPDATE campaigns SET game_id=?, game_name=?, title=?, status=?, start_at=?,
               end_at=?, image_url=?, details_url=?, channels_summary=?, raw_json=?,
               last_seen_at=? WHERE id=?""",
            (c.get("game_id"), c["game_name"], c["title"], feed_status,
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


def replace_steam_games(conn, owner: str, games: list[dict]):
    """Replace one owner's library (import is authoritative per owner)."""
    conn.execute("DELETE FROM steam_games WHERE owner=?", (owner,))
    now = now_iso()
    for g in games:
        conn.execute(
            "INSERT OR IGNORE INTO steam_games (appid, name, owner, created_at) VALUES (?,?,?,?)",
            (int(g.get("appid", 0) or 0), (g.get("name") or "").strip(), owner, now))
    conn.commit()


def get_steam_games(conn, owners: tuple = ("mick",)) -> set[str]:
    """Owned game NAMES for the given owners (badge matching)."""
    if not owners:
        return set()
    marks = ",".join("?" * len(owners))
    rows = conn.execute(f"SELECT name FROM steam_games WHERE owner IN ({marks})", owners).fetchall()
    return {r["name"] for r in rows}


def get_steam_count(conn, owner: str) -> int:
    return conn.execute("SELECT COUNT(*) c FROM steam_games WHERE owner=?", (owner,)).fetchone()["c"]


def close_ended(conn) -> int:
    """Transition rows whose end_at has passed (or status EXPIRED) -> CLOSED + archived_at.
    Rows are kept forever (history); only the status/archive markers change.
    Returns count closed. Conservative: never touches ACTIVE/UPCOMING with future end."""
    now = now_iso()
    rows = conn.execute(
        "SELECT id FROM campaigns WHERE status IN ('ACTIVE','UPCOMING','EXPIRED') "
        "AND (end_at IS NULL OR end_at <= ?)", (now,)).fetchall()
    for r in rows:
        conn.execute("UPDATE campaigns SET status='CLOSED', is_closed=1, archived_at=? WHERE id=?",
                     (now, r["id"]))
        conn.execute("INSERT OR IGNORE INTO events (ts, kind, campaign_id, payload) VALUES (?,?,?,?)",
                     (now, "closed", r["id"], None))
    if rows:
        conn.commit()
    return len(rows)


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

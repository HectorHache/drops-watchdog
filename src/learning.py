#!/usr/bin/env python3
"""Phase 7 learning loop — per-game interaction signals (stdlib only).

Every ⭐ favorite / 👍 like / 🔔 remind from the site lands in KV, then the
watchdog pulls it into events (kind='kv_interaction', payload.kind). This
module aggregates that history into per-game signal scores with recency
decay, so "games Mick actually engages with" surface first.

Scoring (deterministic, recency-decayed):
  favorite : +10
  like     : +3
  remind   : +5
  decay    : half-life 30 days (score *= 0.5 ** (age_days / 30))

Outputs:
  game_signals()  -> {game_name: {favorites, likes, reminds, score}}
  favorites_weight(conn) -> {game_name: weight} (for site priority sort)
  update_weights(conn)   -> persists favorite weights (favorites.weight)
"""
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import watchdog as wd

POINTS = {"favorite": 10, "like": 3, "remind": 5}
HALF_LIFE_DAYS = 30
BASE_WEIGHT = 10
MAX_WEIGHT = 50


def game_signals(conn, now=None) -> dict:
    """Aggregate kv_interaction events into per-game signals (recency-decayed)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rows = conn.execute(
        """SELECT e.ts, e.campaign_id, e.payload, c.game_name
           FROM events e LEFT JOIN campaigns c ON c.id = e.campaign_id
           WHERE e.kind = 'kv_interaction'""").fetchall()
    signals = {}
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
            kind = str(payload.get("kind") or "")
        except Exception:
            continue
        if kind not in POINTS:
            continue
        gname = r["game_name"]
        if not gname:
            continue
        ts = wd.parse_dt(r["ts"])
        age_days = ((now - ts).total_seconds() / 86400.0) if ts else 0.0
        decay = 0.5 ** (max(age_days, 0.0) / HALF_LIFE_DAYS)
        s = signals.setdefault(gname, {"favorites": 0, "likes": 0, "reminds": 0, "score": 0.0})
        s[kind + "s"] += 1
        s["score"] += POINTS[kind] * decay
    return signals


def favorites_weight(conn, now=None) -> dict:
    """Map game_name -> display weight (base 10 + learned signals, capped)."""
    signals = game_signals(conn, now)
    out = {}
    for r in conn.execute("SELECT game_name FROM favorites"):
        g = r["game_name"]
        out[g] = min(MAX_WEIGHT, BASE_WEIGHT + int(signals.get(g, {}).get("score", 0)))
    return out


def update_weights(conn, now=None):
    """Persist learned weights into favorites.weight (called each sync)."""
    weights = favorites_weight(conn, now)
    n = 0
    for gname, w in weights.items():
        cur = conn.execute("SELECT weight FROM favorites WHERE game_name=?", (gname,)).fetchone()
        if cur and cur["weight"] != w:
            conn.execute("UPDATE favorites SET weight=? WHERE game_name=?", (w, gname))
            n += 1
    if n:
        conn.commit()
    return n


if __name__ == "__main__":
    conn = dbm.init_db(ROOT / "data" / "drops.db")
    sig = game_signals(conn)
    print("signals:", json.dumps(sig, indent=1) if sig else "(none yet — buttons not clicked)")
    upd = update_weights(conn)
    print(f"weights updated: {upd}")

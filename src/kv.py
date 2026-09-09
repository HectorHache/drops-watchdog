#!/usr/bin/env python3
"""KV interaction bridge (Phase 6, improvement #1) — Stdlib only.

The site's ⭐/👍/🔔 buttons POST to the CF Pages Function (/api/interact) which
writes to Cloudflare KV. This module pulls those interactions back into the
local SQLite DB each sync, so the watchdog can act on them with zero always-on
server (KV is the round-trip bus).

Pulls:
  GET https://drops.hector.app/api/interactions?since=<last_pulled_ts>
Applies:
  - favorite(campaignId) -> upsert into favorites (game_name = that campaign's game)
  - remind(campaignId)   -> recorded in kv_interactions (personal-alert path reads it)
  - like                 -> recorded for learning weights (Phase 6 learning loop)
Tracks the watermark in meta.kv_last_ts so each interaction is consumed once.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm

DEFAULT_ENDPOINT = "https://drops.hector.app/api/interactions"


UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _pull(endpoint: str, since: int, timeout: int = 20) -> list[dict]:
    url = f"{endpoint}?since={since}"
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"[kv] pull failed: {e}", file=sys.stderr)
        return []
    return data.get("interactions", []) if data.get("ok") else []


def kv_pull(conn, endpoint: str = DEFAULT_ENDPOINT) -> int:
    """Fetch new interactions -> DB. Returns count of NEW interactions applied."""
    try:
        last = int(dbm.meta_get(conn, "kv_last_ts") or "0")
    except ValueError:
        last = 0
    camps = dbm.get_campaigns(conn)          # campaign_id -> row (for game name)
    inter = _pull(endpoint, last)
    if not inter:
        return 0
    new_ts = last
    applied = 0
    fav_names = {r["game_name"] for r in conn.execute("SELECT game_name FROM favorites")}
    for it in inter:
        try:
            ts = int(it.get("ts") or 0)
            kind = str(it.get("kind") or "")
            cid = str(it.get("campaignId") or "").strip()
            value = str(it.get("value") or "")
        except Exception:
            continue
        if ts <= last:
            continue
        new_ts = max(new_ts, ts)
        # dedupe: already stored?
        if dbm.ledger_has(conn, cid or "-", f"KV_{kind}_{ts}"):
            continue
        dbm.ledger_add(conn, cid or "-", f"KV_{kind}_{ts}", "kv", payload=value[:200])
        dbm.record_event(conn, "kv_interaction", campaign_id=cid or None,
                         payload={"kind": kind, "value": value})
        if kind == "favorite" and cid:
            row = camps.get(cid)
            if row:
                gname = row["game_name"]
                if gname not in fav_names:
                    conn.execute(
                        "INSERT OR IGNORE INTO favorites (game_name, weight, instant_alert, source, created_at) "
                        "VALUES (?,10,1,'kv',?)", (gname, dbm.now_iso()))
                    conn.commit()
                    fav_names.add(gname)
                    print(f"[kv] favorite applied: {gname} (site button)")
        applied += 1
    if new_ts > last:
        dbm.meta_set(conn, "kv_last_ts", str(new_ts))
    print(f"[kv] pulled {len(inter)} interaction(s), {applied} new (watermark -> {new_ts})")
    return applied


if __name__ == "__main__":
    conn = dbm.init_db(ROOT / "data" / "drops.db")
    kv_pull(conn, sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ENDPOINT)

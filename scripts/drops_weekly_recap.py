#!/usr/bin/env python3
"""Weekly recap digest (Phase 6) — campaigns ended this week, top games, avg duration.

Delivers a compact stats digest to Mick's DM via the Heimdall bot token (direct Bot
API, same pattern as the watchdog). Deterministic, stdlib-only, reads drops.db.

Cron: hermes cron create "0 19 * * 0" --name drops-weekly-recap
      --script drops_weekly_recap.py --no-agent --deliver local
      --workdir ~/Documents/Workspaces/drops
"""
import datetime
import html
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import notify as ntf
import watchdog as wd

WINDOW_DAYS = 7


def _esc(s):
    return html.escape(str(s or ""), quote=False)


def build_recap(conn) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=WINDOW_DAYS)
    # campaigns that ended within the window (from events + end_at in window)
    rows = conn.execute(
        """SELECT c.game_name, c.title, c.end_at, c.start_at
           FROM campaigns c
           WHERE c.end_at >= ? AND c.end_at <= ?
             AND c.status IN ('CLOSED','EXPIRED','ACTIVE')
           ORDER BY c.end_at""", (cutoff.isoformat(), now.isoformat())).fetchall()
    if not rows:
        return None
    ended = 0
    game_counts = {}
    durations = []
    for r in rows:
        end = wd.parse_dt(r["end_at"])
        start = wd.parse_dt(r["start_at"])
        if end and end <= now:
            ended += 1
        if end and start:
            durations.append((end - start).total_seconds() / 3600)
        game_counts[r["game_name"]] = game_counts.get(r["game_name"], 0) + 1

    top = sorted(game_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    avg_h = (sum(durations) / len(durations)) if durations else 0.0

    lines = []
    local = now.astimezone(wd.ZoneInfo("Europe/Madrid"))
    lines.append(f"📊 <b>WEEKLY DROPS RECAP</b> — <i>{local.strftime('%d %b')}</i>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🏁 Campaigns ended (7d): <b>{ended}</b>")
    lines.append(f"⏱️ Avg duration        : <b>{avg_h:.1f}h</b>")
    lines.append(f"🎮 Top games:")
    for name, count in top:
        lines.append(f"   ▸ {_esc(name)} — <b>{count}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🌐 <a href=\"https://drops.hache.app\">drops.hache.app</a>")
    return "\n".join(lines)


def main():
    conn = dbm.init_db(ROOT / "data" / "drops.db")
    msg = build_recap(conn)
    if not msg:
        print("no ended campaigns this week — silent")
        return
    try:
        token = ntf.bot_token()
        chat = "2350638"          # Mick's DM
        ntf.send_digest(token, chat, msg)
        print(msg)
        print(f"[recap] sent to DM {chat} ({len(msg)} chars)")
    except Exception as e:
        print(f"[recap] delivery failed: {e}", file=sys.stderr)
        print(msg)  # still show what would have been sent


if __name__ == "__main__":
    main()

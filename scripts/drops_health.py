#!/usr/bin/env python3
"""Weekly health digest (Phase 6) — silent when healthy, DM when something's wrong.

Checks, every run:
  - last successful seed age (staleness: Hermes/Mac down, gateway gap)
  - Twitch auth age vs 25-day max (2FA ~30-day session)
  - recent fetch failures (3+ consecutive)
  - site reachability (drops.hector.app + drops-watchdog.pages.dev)
  - DB integrity (campaigns/events counts sane)

Cron: hermes cron create "0 9 * * 1" --name drops-health
      --script drops_health.py --no-agent --deliver local
      --workdir ~/Documents/Workspaces/drops
"""
import datetime
import html
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import notify as ntf
import twitch_client as tc
import watchdog as wd

MAX_SEED_AGE_H = 6          # no successful fetch in 6h = suspected gap
MAX_AUTH_AGE_DAYS = 25      # Twitch 2FA sessions ≈ 30 days


def _esc(s):
    return html.escape(str(s or ""), quote=False)


def _reachable(url: str, timeout: int = 15) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent":
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def check(conn) -> list[str]:
    issues = []
    now = datetime.datetime.now(datetime.timezone.utc)

    # 1) staleness
    last_seed = dbm.meta_get(conn, "last_seed_at")
    if last_seed:
        try:
            age_h = (now - datetime.datetime.fromisoformat(last_seed.replace("Z", "+00:00"))).total_seconds() / 3600
        except Exception:
            age_h = 99
        if age_h > MAX_SEED_AGE_H:
            issues.append(f"⚠️ Stale: last successful fetch {age_h:.1f}h ago (>{MAX_SEED_AGE_H}h)")

    # 2) auth age — captured_at may carry a human note ("YYYY-MM-DD (phase 0, ...)");
    #    parse only the leading ISO date; unparseable = unknown (no false alert)
    try:
        creds = tc.load_creds()
        captured = str(creds.get("web_auth_token_captured_at") or "")
        m = __import__("re").match(r"(\d{4}-\d{2}-\d{2})(?:T|\s)", captured)
        if m:
            age_d = (time.time() - datetime.datetime.fromisoformat(m.group(1)).timestamp()) / 86400
            if age_d > MAX_AUTH_AGE_DAYS:
                issues.append(f"⚠️ Twitch auth {age_d:.0f} days old (2FA ≈30d) — re-login throwaway in ego-browser")
    except Exception:
        issues.append("⚠️ Twitch credentials missing/unreadable")

    # 3) consecutive fetch failures
    errs = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE kind IN ('fetch_error','error_alert') "
        "AND ts >= ?", ((now - datetime.timedelta(hours=48)).isoformat(),)).fetchone()["c"]
    if errs >= 3:
        issues.append(f"⚠️ {errs} fetch/error events in 48h — check Twitch auth/rate-limit")

    # 4) site reachability
    for name, url in (("drops.hector.app", "https://drops.hector.app/"),
                      ("pages.dev", "https://hector-drops.pages.dev/")):
        if not _reachable(url):
            issues.append(f"⚠️ Site DOWN: {name}")

    # 5) DB sanity
    total = conn.execute("SELECT COUNT(*) c FROM campaigns").fetchone()["c"]
    if total < 50:
        issues.append(f"⚠️ DB suspiciously small: {total} campaigns")
    return issues


def main():
    conn = dbm.init_db(ROOT / "data" / "drops.db")
    issues = check(conn)
    if not issues:
        print("all healthy — silent")
        return
    token = ntf.bot_token()
    chat = "2350638"
    msg = "🚨 <b>DROPS WATCHDOG — HEALTH</b>\n" + "\n".join(f"• {_esc(i)}" for i in issues)
    try:
        ntf.send_digest(token, chat, msg)
        print(msg)
        print(f"[health] sent DM {chat} ({len(msg)} chars)")
    except Exception as e:
        print(f"[health] delivery failed: {e}", file=sys.stderr)
        print(msg)


if __name__ == "__main__":
    main()

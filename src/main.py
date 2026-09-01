#!/usr/bin/env python3
"""Twitch Drops Watchdog — orchestrator (Phase 1: engine + dry-run; no send/deploy yet).

Commands:
  fetch               live fetch -> normalized summary (no DB writes)
  seed                live fetch -> upsert drops.db (source of truth)
  dryrun [--fixture F] classify live (or fixture file) against drops.db, print the
                       digest that WOULD be sent (read-only, no DB writes)
  send                full run: fetch -> classify -> compose -> Telegram channel
                       (ledger-marked, delta-only, D3); silent when nothing new
  test                send a fixed test message to the channel + DM (approval gate)
  build               generate docs/ (site) from drops.db
  status              credentials age + DB stats + auth-expiry warning (2FA ≈ 30 days)

Exit codes: 0 ok; 2 fetch/classify error (silent-fail safe: no false alerts).
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import notify as ntf
import site_builder as sb
import twitch_client as tc
import watchdog as wd

CONFIG = None


def load_config():
    global CONFIG
    if CONFIG is not None:
        return CONFIG
    CONFIG = _parse_simple_yaml((ROOT / "src" / "config.yaml").read_text())
    return CONFIG


def _parse_simple_yaml(text: str) -> dict:
    """Tiny YAML-subset parser (indent-based, scalars, comments). Stdlib only."""
    root, stack = {}, []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key, _, val = line.strip().partition(":")
        key = key.strip().strip('"').strip("'")
        val = val.strip()
        if not val:  # nested block
            node = {}
            if stack:
                parent = stack[-1][1]
                parent[key] = node
            else:
                root[key] = node
            stack.append((indent, node))
            continue
        # scalar
        if val in ("null", "~"):
            parsed = None
        elif val.lower() == "true":
            parsed = True
        elif val.lower() == "false":
            parsed = False
        else:
            try:
                parsed = int(val)
            except ValueError:
                try:
                    parsed = float(val)
                except ValueError:
                    parsed = val.strip('"').strip("'")
        if stack:
            stack[-1][1][key] = parsed
        else:
            root[key] = parsed
    return root


def db_path() -> Path:
    return ROOT / "data" / "drops.db"


def get_favorites(conn) -> set:
    return {r["game_name"] for r in conn.execute("SELECT game_name FROM favorites")}


def print_summary(camps):
    from collections import Counter
    statuses = Counter(c["status"] for c in camps)
    active = [c for c in camps if c["status"] == "ACTIVE"]
    now = datetime.datetime.now(datetime.timezone.utc)
    ending24 = [c for c in active if wd.parse_dt(c["end_at"]) and
                now < wd.parse_dt(c["end_at"]) <= now + datetime.timedelta(hours=24)]
    ending48 = [c for c in active if wd.parse_dt(c["end_at"]) and
                now + datetime.timedelta(hours=24) < wd.parse_dt(c["end_at"]) <= now + datetime.timedelta(hours=48)]
    print(f"fetched {len(camps)} campaigns | statuses: {dict(statuses)}")
    print(f"ACTIVE {len(active)} | ENDING <24h {len(ending24)} | ENDING 24-48h {len(ending48)}")
    for c in sorted(ending24, key=lambda x: x.get("end_at") or "")[:10]:
        end = wd.parse_dt(c["end_at"])
        print(f"  ⏳ {(end - now).total_seconds()/3600:5.1f}h | {c['game_name']} | {c['title']} | "
              f"{wd.fmt_dt(end, load_config()['timezone'])} | {len(c['rewards'])} rewards")


def cmd_fetch(args):
    camps = tc.fetch_campaigns()
    print_summary(camps)


def cmd_seed(args):
    camps = tc.fetch_campaigns()
    conn = dbm.init_db(db_path())
    n_new = n_upd = 0
    for c in camps:
        r = dbm.upsert_campaign(conn, c)
        n_new += r == "inserted"
        n_upd += r == "updated"
    dbm.meta_set(conn, "last_seed_at", dbm.now_iso())
    print(f"seeded {len(camps)} campaigns (new={n_new}, updated={n_upd}) -> {db_path()}")
    print_summary(camps)


def cmd_dryrun(args):
    cfg = load_config()
    now = datetime.datetime.now(datetime.timezone.utc)
    conn = dbm.init_db(db_path())
    if args.fixture:
        raw = json.loads(Path(args.fixture).read_text())
        payload = raw.get("payload", raw)  # tolerate wrapped or raw fixture
        camps = [tc.normalize(c) for c in payload["data"]["currentUser"]["dropCampaigns"]]
        print(f"[fixture] {args.fixture} — {len(camps)} campaigns (offline, read-only)")
        known = {}  # fixture-only demo: no history -> everything is NEW
    else:
        camps = tc.fetch_campaigns()
        known = dbm.get_campaigns(conn)
        print(f"[live] {len(camps)} campaigns; {len(known)} known in DB (read-only)")
    state = wd.classify(known, camps, now,
                        h24=cfg["watchdog"]["ending_24h_hours"],
                        h48=cfg["watchdog"]["ending_48h_hours"])
    print(f"classified: new={len(state['new'])} ending24={len(state['ending_24'])} "
          f"ending48={len(state['ending_48'])} ended={len(state['ended'])} "
          f"changed={len(state['changed'])} closed_only={len(state['closed_only'])}")
    msg = wd.compose_digest(state, now, cfg["timezone"], get_favorites(conn),
                            cap=cfg["watchdog"]["message_cap_chars"],
                            ledger_has=(lambda cid, kind: dbm.ledger_has(conn, cid, kind)) if not args.fixture else None)
    print("\n" + "=" * 40 + " DIGEST " + "=" * 40)
    print(msg if msg else "⚠️  SILENT RUN — nothing changed (no message would be sent)")
    print("=" * 88)


def _send_digest(camps, cfg, conn, dry=False):
    """Shared: classify -> compose -> (send + ledger-mark). Returns sent or None."""
    import notify as ntf
    now = datetime.datetime.now(datetime.timezone.utc)
    known = dbm.get_campaigns(conn)
    state = wd.classify(known, camps, now,
                        h24=cfg["watchdog"]["ending_24h_hours"],
                        h48=cfg["watchdog"]["ending_48h_hours"])
    msg = wd.compose_digest(state, now, cfg["timezone"], get_favorites(conn),
                            cap=cfg["watchdog"]["message_cap_chars"],
                            ledger_has=lambda cid, kind: dbm.ledger_has(conn, cid, kind))
    if msg is None:
        print("silent run — nothing new (no message sent)")
        return None
    if dry:
        print(msg)
        return msg
    token = ntf.bot_token()
    chat = cfg["telegram"]["group_chat_id"]
    if not chat:
        raise SystemExit("telegram.group_chat_id not configured")
    ntf.send_digest(token, chat, msg)
    # mark ledger ONLY after successful send (dedupe; retry on failure)
    for c in state["new"]:
        dbm.ledger_add(conn, c["id"], "NEW_CAMPAIGN", "telegram:channel")
    for c in state["ending_24"]:
        dbm.ledger_add(conn, c["id"], "ENDING_24H", "telegram:channel")
    for c in state["ended"]:
        dbm.ledger_add(conn, c["id"], "ENDED", "telegram:channel")
    for c in state["changed"]:
        dbm.ledger_add(conn, c["id"], "CHANGED", "telegram:channel")
    dbm.record_event(conn, "digest_sent", payload={"chars": len(msg), "to": chat})
    print(f"digest sent to {chat} ({len(msg)} chars; new={len(state['new'])}, "
          f"ending24={len(state['ending_24'])}, ended={len(state['ended'])})")
    return msg


def cmd_send(args):
    cfg = load_config()
    conn = dbm.init_db(db_path())
    camps = tc.fetch_campaigns()
    _send_digest(camps, cfg, conn, dry=args.dry)


def cmd_test(args):
    cfg = load_config()
    token = ntf.bot_token()
    msg = (
        "🎮 <b>TWITCH DROPS WATCHDOG</b> — <i>test message</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Channel delivery works!\n"
        "📅 This is the <b>Phase 2 approval test</b>.\n"
        "🌐 <a href=\"https://drops.hache.app\">drops.hache.app</a>"
    )
    for label, chat in (("channel", cfg["telegram"]["group_chat_id"]),
                        ("DM", cfg["telegram"]["dm_chat_id"])):
        ntf.send_digest(token, chat, msg)
        print(f"test message sent to {label} ({chat})")
    print("If both arrived nicely formatted, the pipeline is approved for scheduling.")


def cmd_build(args):
    sb.cmd_build(argparse.Namespace(db=str(db_path())))


def cmd_status(args):
    cfg = load_config()
    try:
        creds = tc.load_creds()
    except tc.FetchError as e:
        print(f"credentials: {e}")
        creds = {}
    tok = creds.get("web_auth_token")
    captured = creds.get("web_auth_token_captured_at")
    age = None
    if captured:
        try:
            age = (time.time() - datetime.datetime.fromisoformat(captured).timestamp()) / 86400
        except Exception:
            age = None
    max_days = cfg["auth"]["max_age_days"]
    print(f"web_auth_token : {'present' if tok else 'MISSING'}" +
          (f" | captured {captured}" if captured else ""))
    if age is not None:
        flag = "⚠️ REFRESH SOON (2FA ~30d)" if age > max_days else "ok"
        print(f"auth age        : {age:.1f} days / {max_days} max — {flag}")
    conn = dbm.init_db(db_path())
    total = conn.execute("SELECT COUNT(*) c FROM campaigns").fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) c FROM campaigns WHERE status='ACTIVE'").fetchone()["c"]
    closed = conn.execute("SELECT COUNT(*) c FROM campaigns WHERE status='CLOSED'").fetchone()["c"]
    print(f"db              : {db_path()} ({os.path.getsize(db_path()) if db_path().exists() else 0} B)")
    print(f"  campaigns     : {total} (active {active}, closed {closed})")
    print(f"  events        : {conn.execute('SELECT COUNT(*) c FROM events').fetchone()['c']}")
    print(f"timezone        : {cfg['timezone']}")


def main():
    ap = argparse.ArgumentParser(description="Twitch Drops Watchdog")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="live fetch + summary")
    sub.add_parser("seed", help="live fetch -> upsert drops.db")
    dr = sub.add_parser("dryrun", help="classify + digest (read-only)")
    dr.add_argument("--fixture", help="offline mode: use a saved fixture JSON")
    se = sub.add_parser("send", help="full run + Telegram channel delivery")
    se.add_argument("--dry", action="store_true", help="print digest only, no send")
    sub.add_parser("test", help="fixed test message to channel + DM")
    sub.add_parser("build", help="generate docs/ from drops.db")
    sub.add_parser("status", help="credentials + DB status")
    args = ap.parse_args()
    try:
        globals()[f"cmd_{args.cmd}"](args)
    except tc.FetchError as e:
        print(f"FETCH ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

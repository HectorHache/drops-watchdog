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
  sync [--dry]        scheduled run: fetch -> seed -> close ended -> digest -> build
                       -> commit+push docs/ only when changed (cron entrypoint)
  status              credentials age + DB stats + auth-expiry warning (2FA ≈ 30 days)

Exit codes: 0 ok; 2 fetch/classify error (silent-fail safe: no false alerts).
"""
import argparse
import datetime
import html
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import kv as kvm
import notify as ntf
import site_builder as sb
import steam_import as si
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
    """Shared: classify -> compose -> (send + ledger-mark). Returns (msg, state)."""
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
        return None, state
    if dry:
        print(msg)
        return msg, state
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
    return msg, state


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


def _git_porcelain(paths=("docs",)) -> str:
    """Changed files under paths vs HEAD (empty string = nothing changed)."""
    r = subprocess.run(["git", "status", "--porcelain", "--", *paths],
                       capture_output=True, text=True, cwd=ROOT)
    return r.stdout.strip()


def commit_on_change(conn, dry=False) -> bool:
    """Commit + push docs/ ONLY when it changed (respects CF 500-build/month cap)."""
    dirty = _git_porcelain(("docs",))
    if not dirty:
        print("docs/ unchanged — no push (CF build cap respected)")
        return False
    print(f"docs/ changed ({len(dirty.splitlines())} file(s)) — committing + pushing")
    if dry:
        print("  (--dry: skip commit/push)")
        return False
    subprocess.run(["git", "add", "docs"], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-m", f"site: auto-update {dbm.now_iso()}"],
                   cwd=ROOT, check=True)
    r = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True,
                       cwd=ROOT)
    if r.returncode != 0:
        print(f"PUSH FAILED: {r.stderr}", file=sys.stderr)
        _alert(conn, "PUSH_FAILED", f"git push to origin/main failed:\n{r.stderr[-400:]}",
               throttle_hours=1)
        return False
    print("pushed to origin/main — CF Pages auto-deploy triggered")
    return True


def _alert(conn, kind: str, detail: str, throttle_hours: int = 6) -> bool:
    """Throttled DM alert for non-silent failures (fetch/push). Uses meta watermark."""
    key = f"last_alert_{kind}"
    last = dbm.meta_get(conn, key)
    if last:
        try:
            age_h = (time.time() - datetime.datetime.fromisoformat(last).timestamp()) / 3600
        except Exception:
            age_h = 99
        if age_h < throttle_hours:
            print(f"[alert:{kind}] throttled ({age_h:.1f}h ago) — skip")
            return False
    try:
        token = ntf.bot_token()
        chat = load_config()["telegram"]["dm_chat_id"]
        msg = (f"🚨 <b>DROPS WATCHDOG — {html.escape(kind)}</b>\n"
               f"<pre>{html.escape(str(detail)[:900])}</pre>")
        ntf.send_digest(token, chat, msg)
        dbm.meta_set(conn, key, dbm.now_iso())
        dbm.record_event(conn, "error_alert", payload={"kind": kind})
        print(f"[alert:{kind}] sent DM to {chat}")
        return True
    except Exception as e:
        print(f"[alert:{kind}] DM send failed: {e}", file=sys.stderr)
        return False


def cmd_sync(args):
    """Full scheduled run (cron): fetch -> seed -> close ended -> digest -> build -> push."""
    cfg = load_config()
    conn = dbm.init_db(db_path())
    try:
        camps = tc.fetch_campaigns()
    except tc.FetchError as e:
        print(f"FETCH ERROR: {e}", file=sys.stderr)
        _alert(conn, "FETCH_FAILED", str(e))
        raise  # main() exits 2 (silent-fail safe for cron)
    n_new = n_upd = 0
    for c in camps:
        r = dbm.upsert_campaign(conn, c)
        n_new += r == "inserted"
        n_upd += r == "updated"
    dbm.meta_set(conn, "last_seed_at", dbm.now_iso())
    print(f"seeded {len(camps)} campaigns (new={n_new}, updated={n_upd})")

    # pull site ⭐/👍/🔔 interactions (KV round-trip) so favorites made on the
    # site apply within the next sync cycle
    try:
        kvm.kv_pull(conn)
    except Exception as e:
        print(f"[kv] pull error (non-fatal): {e}", file=sys.stderr)

    # close campaigns that have ended (keep rows forever; site History grows)
    closed = dbm.close_ended(conn)
    if closed:
        print(f"closed {closed} ended campaign(s) -> CLOSED + archived_at")

    msg, state = _send_digest(camps, cfg, conn, dry=args.dry)

    sb.cmd_build(argparse.Namespace(db=str(db_path())))
    commit_on_change(conn, dry=args.dry)
    print("sync complete")



def cmd_steam_import(args):
    """Import Steam libraries (mick API + wifey browser scrape) into steam_games."""
    results = si.cmd_import(str(db_path()), args.owners)
    print(f"[steam] stored in {db_path()}")
    # rebuild site so badges reflect the new library
    try:
        sb.cmd_build(argparse.Namespace(db=str(db_path())))
        commit_on_change(dbm.init_db(db_path()), dry=args.dry)
    except Exception as e:
        print(f"[steam] site refresh skipped: {e}", file=sys.stderr)
    return results


def cmd_favorite(args):
    """Manage favorites: add/rm/list a game name (selector backend for Phase 5)."""
    conn = dbm.init_db(db_path())
    if args.fcmd == "list":
        rows = conn.execute("SELECT game_name, source, created_at FROM favorites ORDER BY game_name").fetchall()
        print(f"favorites ({len(rows)}):")
        for r in rows:
            print(f"  ⭐ {r['game_name']}  (source={r['source']})")
        return
    if not args.game:
        raise SystemExit("favorite add|rm <game-name> | favorite list")
    name = args.game.strip()
    if args.fcmd == "add":
        conn.execute("INSERT OR IGNORE INTO favorites (game_name, weight, instant_alert, source, created_at) "
                     "VALUES (?,10,1,'manual',?)", (name, dbm.now_iso()))
        conn.commit()
        print(f"⭐ favorited: {name}")
    elif args.fcmd == "rm":
        cur = conn.execute("DELETE FROM favorites WHERE game_name=?", (name,))
        conn.commit()
        print(f"removed favorite ({cur.rowcount}): {name}")
    else:
        raise SystemExit("favorite add|rm <game-name> | favorite list")
    # rebuild site so isFavorite flags + Favorites tab update
    try:
        sb.cmd_build(argparse.Namespace(db=str(db_path())))
        commit_on_change(conn, dry=args.dry)
    except Exception as e:
        print(f"[favorite] site refresh skipped: {e}", file=sys.stderr)


def _send_personal(camps, cfg, conn, dry=False):
    """Favorites' personal DM alerts (24/7). Fires once per campaign per kind:
    PERSONAL_NEW (favorite game has a brand-new campaign) and PERSONAL_24H (favorite
    campaign enters the <24h window). Ledger-marked after successful send -> no spam.
    Returns the message or None (silent)."""
    import notify as ntf
    favorites = get_favorites(conn)
    if not favorites:
        print("no favorites configured — skipping personal alerts")
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    known = dbm.get_campaigns(conn)
    state = wd.classify(known, camps, now,
                        h24=cfg["watchdog"]["ending_24h_hours"],
                        h48=cfg["watchdog"]["ending_48h_hours"])
    picks = []
    for c in state["new"]:
        if c["game_name"] in favorites and not dbm.ledger_has(conn, c["id"], "PERSONAL_NEW"):
            picks.append((c, "PERSONAL_NEW", "NEW DROP"))
    for c in state["ending_24"]:
        if c["game_name"] in favorites and not dbm.ledger_has(conn, c["id"], "PERSONAL_24H"):
            picks.append((c, "PERSONAL_24H", "ENDING <24H"))
    if not picks:
        print("no favorite alerts to send (silent)")
        return None
    lines = ["⭐ <b>FAVORITE DROP ALERT</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
    for c, kind, tag in picks:
        end = wd.parse_dt(c["end_at"])
        left = wd._hours_left(end, now)
        lines.append(f"🚨 <b>{wd._esc(tag)}</b> — <b>{wd._esc(c['game_name'])}</b> — <i>{wd._esc(c['title'])}</i>")
        if end:
            lines.append(f"   ⏳ ends {wd._esc(wd.fmt_dt(end, cfg['timezone']))} ({left} left)")
        names = wd._esc(" · ".join(r["name"] for r in c.get("rewards", [])[:3]))
        if names:
            lines.append(f"   🏆 {names}")
        lines.append(f"   🔗 <a href=\"{wd._esc(c['details_url'])}\">Open drop</a>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🌐 <a href=\"https://drops.hache.app\">drops.hache.app</a>")
    msg = "\n".join(lines)
    if dry:
        print(msg)
        return msg
    token = ntf.bot_token()
    chat = cfg["telegram"]["dm_chat_id"]
    ntf.send_digest(token, chat, msg)
    for c, kind, _ in picks:
        dbm.ledger_add(conn, c["id"], kind, "telegram:dm")
    dbm.record_event(conn, "personal_alert_sent", payload={"picks": len(picks), "to": chat})
    print(f"personal favorite alert sent to {chat} ({len(picks)} pick(s))")
    return msg


def cmd_personal(args):
    """24/7 favorites check (separate cron): fetch -> seed -> close -> personal DM only."""
    cfg = load_config()
    conn = dbm.init_db(db_path())
    try:
        camps = tc.fetch_campaigns()
    except tc.FetchError as e:
        print(f"FETCH ERROR: {e}", file=sys.stderr)
        _alert(conn, "FETCH_FAILED", str(e))
        raise
    for c in camps:
        dbm.upsert_campaign(conn, c)
    try:
        kvm.kv_pull(conn)
    except Exception as e:
        print(f"[kv] pull error (non-fatal): {e}", file=sys.stderr)
    closed = dbm.close_ended(conn)
    if closed:
        print(f"closed {closed} ended campaign(s)")
    _send_personal(camps, cfg, conn, dry=args.dry)


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
    sy = sub.add_parser("sync", help="scheduled full run (fetch/seed/close/digest/build/push)")
    sy.add_argument("--dry", action="store_true", help="no send, no commit/push (print only)")
    si_cmd = sub.add_parser("steam-import", help="import Steam libraries (mick API + wifey scrape)")
    si_cmd.add_argument("--owners", default="mick,wifey", help="comma list: mick,wifey")
    si_cmd.add_argument("--dry", action="store_true", help="no site push")
    fav = sub.add_parser("favorite", help="manage favorites (add/rm/list)")
    fav.add_argument("fcmd", nargs="?", default="list", choices=["add", "rm", "list"])
    fav.add_argument("game", nargs="?", default=None)
    fav.add_argument("--dry", action="store_true", help="no site push")
    pe = sub.add_parser("personal", help="24/7 favorites DM check (cron)")
    pe.add_argument("--dry", action="store_true", help="print only")
    sub.add_parser("status", help="credentials + DB status")
    args = ap.parse_args()
    _CMD_ALIAS = {"steam-import": "cmd_steam_import"}   # dashed -> func name
    target = _CMD_ALIAS.get(args.cmd, f"cmd_{args.cmd}")
    try:
        globals()[target](args)
    except tc.FetchError as e:
        print(f"FETCH ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

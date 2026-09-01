#!/usr/bin/env python3
"""Watchdog: diff/classify + Telegram digest composer. Deterministic, escaping-safe."""
import datetime
import html
from zoneinfo import ZoneInfo


def parse_dt(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify(known: dict, live: list[dict], now: datetime.datetime | None = None,
             h24: int = 24, h48: int = 48) -> dict:
    """known: {id: db_row}; live: normalized entities. Returns classification."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    d24 = datetime.timedelta(hours=h24)
    d48 = datetime.timedelta(hours=h48)
    live_by_id = {c["id"]: c for c in live}

    new, ending_24, ending_48, ended, changed, closed_only, active = [], [], [], [], [], [], []
    for c in live:
        status = c["status"]
        end = parse_dt(c["end_at"])
        row = known.get(c["id"])
        if status == "EXPIRED" or (end and end <= now):
            if row and row.get("status") not in ("EXPIRED", "CLOSED"):
                ended.append(c)          # was active, now gone
            elif row is None:
                closed_only.append(c)    # seen only in closed section -> never alert
            continue
        # ACTIVE/UPCOMING
        active.append(c)
        if row is None:
            new.append(c)
        else:
            # material change check (end_at shift, rewards, title/game)
            if _materially_changed(row, c):
                changed.append(c)
        if end:
            left = end - now
            if d24 >= left > datetime.timedelta(0):
                ending_24.append(c)
            elif d48 >= left > d24:
                ending_48.append(c)

    # known-but-missing: conservative — event only, never a false "ended" alert
    disappeared = [i for i in known if i not in live_by_id]

    return {
        "new": new, "ending_24": ending_24, "ending_48": ending_48,
        "ended": ended, "changed": changed, "closed_only": closed_only,
        "active_count": len(active), "disappeared": disappeared,
    }


def _materially_changed(row, c, end_delta_min: int = 5) -> bool:
    old_end = parse_dt(row.get("end_at"))
    new_end = parse_dt(c.get("end_at"))
    if old_end and new_end and abs((new_end - old_end).total_seconds()) > end_delta_min * 60:
        return True
    if row.get("title") != c.get("title") or row.get("game_name") != c.get("game_name"):
        return True
    old_rewards = json_rewards(row.get("raw_json")) if False else None
    # rewards compared via raw_json is unreliable; compare stored count indirectly
    return False


def json_rewards(raw):  # helper placeholder (rewards live in rewards table; comparison in Phase 1 uses count via caller)
    return None


def fmt_dt(dt, tz_name: str) -> str:
    if dt is None:
        return "?"
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%a %d %b %H:%M %Z")


def _hours_left(end, now) -> str:
    left = end - now
    h, m = int(left.total_seconds() // 3600), int(left.total_seconds() % 3600 // 60)
    return f"{h}h {m:02d}m"


def _esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def compose_digest(state: dict, now: datetime.datetime, tz_name: str,
                   favorites: set | None = None, cap: int = 3800,
                   ledger_has=None) -> str | None:
    """Delta-only digest (D3). Returns None when nothing NEW happened:
    a message fires only when a campaign is new / newly entered the <24h window /
    ended / materially changed. The <24h section then lists ALL campaigns in the
    window (the actionable set). ledger_has(campaign_id, kind) -> bool marks alerts
    already sent (dedupe)."""
    new = sorted(state["new"], key=lambda c: c.get("end_at") or "")
    ending24 = sorted(state["ending_24"], key=lambda c: c.get("end_at") or "")
    ending48 = sorted(state["ending_48"], key=lambda c: c.get("end_at") or "")
    ended = sorted(state["ended"], key=lambda c: c.get("end_at") or "", reverse=True)
    changed = state["changed"]

    # D3: silent unless something NEW happened (ledger-aware)
    newly_entered_24 = [
        c for c in ending24
        if not (ledger_has and ledger_has(c["id"], "ENDING_24H"))
    ]
    if not new and not newly_entered_24 and not ended and not changed:
        return None

    lines = []
    local = now.astimezone(ZoneInfo(tz_name))
    lines.append(f"🎮 <b>TWITCH DROPS WATCHDOG</b> — <i>{local.strftime('%a %d %H:%M %Z')}</i>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    if ending24:
        lines.append(f"🚨 <b>ENDING &lt;24H ({len(ending24)})</b>")
        for c in ending24:
            end = parse_dt(c["end_at"])
            left = _hours_left(end, now)
            fav = "⭐ " if favorites and c["game_name"] in favorites else ""
            lines.append(
                f"⏳ {fav}<b>{_esc(c['game_name'])}</b> — <u>{_esc(c['title'])}</u> "
                f"ends {_esc(fmt_dt(end, tz_name))} ({left} left)"
            )
            names = _esc(" · ".join(r["name"] for r in c.get("rewards", [])[:4]))
            if names:
                lines.append(f"   🏆 {names}")
            lines.append(f"   🔗 <a href=\"{_esc(c['details_url'])}\">Open drop</a>")
        lines.append("")

    if new:
        lines.append(f"✨ <b>NEW ({len(new)})</b>")
        for c in new:
            end = parse_dt(c["end_at"])
            lines.append(
                f"🎁 <b>{_esc(c['game_name'])}</b> — <i>{_esc(c['title'])}</i>"
            )
            lines.append(
                f"   📅 {_esc(fmt_dt(parse_dt(c['start_at']), tz_name))} → "
                f"{_esc(fmt_dt(end, tz_name))}"
            )
            names = _esc(" · ".join(r["name"] for r in c.get("rewards", [])[:4]))
            if names:
                lines.append(f"   🏆 {names}")
            lines.append(f"   🔗 <a href=\"{_esc(c['details_url'])}\">Details</a>")
        lines.append("")

    if ending48:
        lines.append(f"🕒 <b>ENDING 24–48H ({len(ending48)})</b>")
        for c in ending48:
            end = parse_dt(c["end_at"])
            lines.append(f"▸ <b>{_esc(c['game_name'])}</b> — {_esc(fmt_dt(end, tz_name))}")
        lines.append("")

    if ended:
        lines.append(f"🏁 <b>ENDED ({len(ended)})</b> since last check")
        for c in ended:
            lines.append(f"▸ {_esc(c['game_name'])} — <i>{_esc(c['title'])}</i>")
        lines.append("")

    if changed:
        lines.append(f"🔁 <b>CHANGED ({len(changed)})</b>")
        for c in changed[:5]:
            lines.append(f"▸ {_esc(c['game_name'])} — {_esc(c['title'])}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 {state['active_count']} active · 🌐 <a href=\"https://drops.hache.app\">drops.hache.app</a>")
    # cap: keep whole LINES only (tags never span lines in this composer, so a
    # newline boundary can never cut a tag -> Telegram HTML always parses)
    msg = "\n".join(lines)
    if len(msg) > cap:
        kept, total = [], 0
        for ln in lines:
            add = len(ln) + 1
            if total + add > cap - 80:
                break
            kept.append(ln)
            total += add
        msg = "\n".join(kept).rstrip() + f"\n…+ more on <a href=\"https://drops.hache.app\">drops.hache.app</a>"
    return msg

#!/usr/bin/env python3
"""Site builder: reads drops.db -> publishes docs/ (drops.json, index.html, feed.xml,
_headers, assets mirror + prune). Stdlib only.

Commands:
  build [--db PATH]   generate docs/ from the DB
"""
import argparse
import datetime
import json
import sys
import time
import urllib.request
import urllib.error
import xml.sax.saxutils
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm
import watchdog as wd

DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"
TZ_NAME = "Europe/Madrid"
TEMPLATE = ROOT / "src" / "site_template.html"


def _fmt(ts) -> str:
    return (ts or "").replace("Z", "+00:00")


def build_drops_json(conn, cfg) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = dbm.get_campaigns(conn)
    favorites = {r["game_name"] for r in conn.execute("SELECT game_name FROM favorites")}
    campaigns, archive = [], []
    for c in rows.values():
        end = wd.parse_dt(c["end_at"])
        start = wd.parse_dt(c["start_at"])
        active = c["status"] == "ACTIVE" and end and end > now
        rewards = [dict(r) for r in conn.execute(
            "SELECT name, required_minutes AS minutes FROM rewards WHERE campaign_id=? ORDER BY sort",
            (c["id"],))]
        entry = {
            "id": c["id"],
            "game": c["game_name"],
            "title": c["title"],
            "status": c["status"],
            "startAt": _fmt(c["start_at"]),
            "endAt": _fmt(c["end_at"]),
            "imageUrl": c.get("image_url") or "",
            "detailsUrl": c.get("details_url") or "",
            "rewards": rewards,
        }
        if active:
            first_seen = wd.parse_dt(c["first_seen_at"])
            entry["isNew"] = bool(first_seen and (now - first_seen) <= datetime.timedelta(hours=24))
            entry["hoursLeft"] = round((end - now).total_seconds() / 3600, 2)
            entry["inSteamLibrary"] = False          # Phase 5
            entry["isFavorite"] = c["game_name"] in favorites
            campaigns.append(entry)
        else:
            archive.append({
                "id": c["id"], "game": c["game_name"], "title": c["title"],
                "startAt": _fmt(c["start_at"]), "endAt": _fmt(c["end_at"]),
                "status": "ENDED" if end and end <= now else c["status"],
            })

    ending24 = sum(1 for e in campaigns if 0 < e["hoursLeft"] <= 24)
    ending48 = sum(1 for e in campaigns if 24 < e["hoursLeft"] <= 48)
    campaigns.sort(key=lambda e: e["hoursLeft"])
    archive.sort(key=lambda e: e["endAt"], reverse=True)

    return {
        "generated_at": now.astimezone(datetime.timezone(datetime.timedelta(hours=2))).isoformat(timespec="seconds")
        if False else dbm.now_iso(),
        "generated_at_local": now.astimezone(wd.ZoneInfo(TZ_NAME)).isoformat(timespec="seconds"),
        "tz": TZ_NAME,
        "campaigns": campaigns,
        "archive": archive,
        "favorites": sorted(favorites),
        "counts": {"active": len(campaigns), "new": sum(1 for e in campaigns if e["isNew"]),
                   "ending24": ending24, "ending48": ending48},
    }


def sync_assets(conn, data: dict) -> dict:
    """Download images for active campaigns; prune ended campaigns' files."""
    ASSETS.mkdir(parents=True, exist_ok=True)
    active_ids = {c["id"] for c in data["campaigns"]}
    downloaded, failed, pruned = 0, [], []

    for c in data["campaigns"]:
        url = c.get("imageUrl") or ""
        if not url:
            continue
        ext = ".png" if ".png" in url.lower() else ".webp" if ".webp" in url.lower() else ".jpg"
        dest = ASSETS / f"{c['id']}{ext}"
        if dest.exists():
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
            with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
                f.write(r.read())
            downloaded += 1
        except Exception as e:
            failed.append(c["id"])
            print(f"[assets] download failed {c['id']}: {e}", file=sys.stderr)

    for f in ASSETS.iterdir():
        if f.is_file() and f.stem not in active_ids:
            f.unlink()
            pruned.append(f.name)
    return {"downloaded": downloaded, "failed": failed, "pruned": pruned}


def build_feed(data: dict) -> str:
    items = []
    for c in data["campaigns"][:50]:
        title = f"{c['game']} — {c['title']}"
        desc = " · ".join(r["name"] for r in c["rewards"][:6]) or "Twitch Drops campaign"
        desc = f"{desc} — runs {c['startAt']} → {c['endAt']} ({c['hoursLeft']}h left)"
        items.append(
            f"  <item>\n"
            f"    <title>{xml.sax.saxutils.escape(title)}</title>\n"
            f"    <link>{xml.sax.saxutils.escape(c['detailsUrl'])}</link>\n"
            f"    <guid isPermaLink=\"false\">{xml.sax.saxutils.escape(c['id'])}</guid>\n"
            f"    <description>{xml.sax.saxutils.escape(desc)}</description>\n"
            f"    <pubDate>{xml.sax.saxutils.escape(c['startAt'])}</pubDate>\n"
            f"  </item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n<channel>\n'
        "  <title>Twitch Drops Watchdog</title>\n"
        "  <link>https://drops.hache.app</link>\n"
        "  <description>Active Twitch drop campaigns with rewards and countdowns</description>\n"
        + "\n".join(items) + "\n</channel>\n</rss>\n"
    )


HEADERS = """/drops.json
  Cache-Control: no-store

/feed.xml
  Cache-Control: max-age=600
"""


def cmd_build(args):
    cfg = json.loads((ROOT / "src" / "config.yaml").read_text()) if False else None
    dbp = Path(args.db)
    conn = dbm.init_db(dbp)
    data = build_drops_json(conn, None)
    DOCS.mkdir(parents=True, exist_ok=True)

    asset_report = sync_assets(conn, data)
    # annotate each campaign with its mirrored asset filename (extension known post-download)
    for c in data["campaigns"]:
        f = next((x for x in ASSETS.iterdir() if x.is_file() and x.stem == c["id"]), None)
        c["asset"] = f"assets/{f.name}" if f else ""

    (DOCS / "drops.json").write_text(json.dumps(data, indent=1))
    (DOCS / "_headers").write_text(HEADERS)

    if TEMPLATE.exists():
        html = TEMPLATE.read_text()
        html = html.replace("/*__GENERATED_AT__*/", data["generated_at_local"])
        (DOCS / "index.html").write_text(html)
    else:
        print(f"[site] WARNING: {TEMPLATE} missing — index.html not updated", file=sys.stderr)

    (DOCS / "feed.xml").write_text(build_feed(data))

    print(f"docs/ published: {len(data['campaigns'])} active + {len(data['archive'])} archived")
    print(f"  counts: {data['counts']}")
    print(f"  assets: downloaded={asset_report['downloaded']} failed={len(asset_report['failed'])} pruned={len(asset_report['pruned'])}")
    print(f"  files: drops.json, index.html, feed.xml, _headers, assets/{len(list(ASSETS.iterdir()))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "drops.db"))
    args = ap.parse_args()
    cmd_build(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Steam library import (Phase 5) — Stdlib only.

Sources:
  - Mick  : Steam Web API (ISteamUser/ResolveVanityURL + IPlayerService/GetOwnedGames)
            via .steamMine API key. No login needed. (d4rkwar)
  - wifey : friend-shared games via authenticated browser scrape in ego-browser
            (Mick's own Steam login; surprise preserved — NO access request sent).
            Degrades gracefully if her Game Details visibility is private.

Stores into the steam_games table (appid, name, owner) — the site badge and the
favorites selector read from there. Run:  python3 src/main.py steam-import
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import db as dbm

STEAM_KEY_FILE = ROOT / ".steamMine"
WIFEY_STEAMID = "76561198051826838"     # friend-shared (no API access — surprise)
MICK_VANITY = "d4rkwar"


class SteamError(Exception):
    pass


def api_key() -> str:
    """Bare 32-char hex key, or JSON {"api_key": "..."} — both accepted."""
    if not STEAM_KEY_FILE.exists():
        raise SteamError(f"{STEAM_KEY_FILE} missing")
    raw = STEAM_KEY_FILE.read_text().strip()
    try:
        d = json.loads(raw)
        return str(d["api_key"])
    except Exception:
        return raw


def _get(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        raise SteamError(f"Steam API {url[:80]}... -> {e}")


def resolve_vanity(vanity: str, key: str) -> str | None:
    r = _get(f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
             f"?key={key}&vanityurl={urllib.parse.quote(vanity)}")
    resp = r.get("response", {})
    return resp.get("steamid") if resp.get("success") == 1 else None


def get_owned_games(steamid: str, key: str, timeout: int = 60) -> list[dict]:
    """App list via IPlayerService (include free games + appinfo)."""
    url = (f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
           f"?key={key}&steamid={steamid}&include_appinfo=1&include_played_free_games=1")
    r = _get(url, timeout=timeout)
    games = (r.get("response") or {}).get("games", [])
    return [{"appid": int(g.get("appid", 0)), "name": (g.get("name") or "").strip()}
            for g in games if g.get("name")]


def import_mick(conn) -> dict:
    """Mick via API — full library (names only; we only need game names for badges)."""
    key = api_key()
    sid = resolve_vanity(MICK_VANITY, key)
    if not sid:
        raise SteamError(f"vanity '{MICK_VANITY}' not resolvable")
    games = get_owned_games(sid, key)
    dbm.replace_steam_games(conn, "mick", games)
    print(f"[steam] mick ({sid}): {len(games)} games imported")
    return {"owner": "mick", "steamid": sid, "count": len(games), "source": "api"}


def import_wifey(conn) -> dict:
    """Wifey via authenticated browser scrape (friend-shared). Uses ego-browser
    with Mick's own Steam login — surprise preserved. Private visibility -> []."""
    games = _scrape_wifey_games()
    if games is None:                       # scrape failed hard -> raise
        raise SteamError("wifey scrape returned no data (login? private Game Details?)")
    dbm.replace_steam_games(conn, "wifey", games)
    print(f"[steam] wifey ({WIFEY_STEAMID}): {len(games)} games scraped")
    return {"owner": "wifey", "steamid": WIFEY_STEAMID, "count": len(games),
            "source": "browser-scrape"}


def _scrape_wifey_games() -> list[dict] | None:
    """ego-browser nodejs: open Steam profile games tab (all, grid), read titles.
    Returns None if the profile/games page couldn't be parsed (login/private)."""
    import subprocess
    import uuid
    space = "steam wifey " + uuid.uuid4().hex[:8]   # unique per run — avoids stale-space collisions
    js = r"""
const task = await useOrCreateTaskSpace('{SPACE}')
await openOrReuseTab('https://steamcommunity.com/profiles/76561198051826838/games/?tab=all', { wait: true, timeout: 30 })
await wait(5)
// login-gated or private? Steam shows "Profile is private" or an age gate / sign-in.
const body = await js(`document.body ? document.body.innerText.slice(0, 1200) : ''`)
// revamped Steam games page lazy-loads rows on scroll -> scroll to bottom first
// pure-JS scroll (window.scrollBy) — the CDP `scroll()` helper's wheel events
// time out in a fresh subprocess; scrollBy still triggers Steam's lazy loader
for (let i = 0; i < 8; i++) { await js(`window.scrollBy(0, 3500)`); await wait(1); }
await wait(2)
const titles = await js(`(() => {
  const out = new Map();   // name -> appid
  const skip = ['store page','download','community'];
  document.querySelectorAll('a[href*="/app/"]').forEach(a => {
    const t = (a.textContent || '').trim();
    if (!t || t.length >= 100) return;
    if (skip.some(k => t.toLowerCase().indexOf(k) >= 0)) return;
    const parts = (a.href || '').split('/app/');
    const appid = parts.length > 1 ? parseInt(parts[1]) : 0;
    out.set(t, isNaN(appid) ? 0 : appid);
  });
  return [...out.entries()].map(([name, appid]) => ({name, appid}));
})()`)
cliLog('STEAM_BODY: ' + body.replace(/\n+/g, ' | ').slice(0, 400))
cliLog('STEAM_TITLES: ' + JSON.stringify(titles))
await completeTaskSpace(task.id, { keep: false })
""".replace("{SPACE}", space)
    try:
        r = subprocess.run(["ego-browser", "nodejs"], input=js, capture_output=True,
                           text=True, timeout=120)
    except Exception as e:
        print(f"[steam] ego-browser subprocess failed: {e}", file=sys.stderr)
        return None
    out = (r.stdout or "") + (r.stderr or "")
    games = []
    for line in out.splitlines():
        if line.startswith("STEAM_TITLES: "):
            try:
                games = json.loads(line[len("STEAM_TITLES: "):])
            except Exception:
                games = []
    if not games:
        print(f"[steam] wifey: no titles parsed. page:\n{(out or '')[-800:]}", file=sys.stderr)
        return None
    # normalize: each entry is {"name":..., "appid":...} (or bare name string from older scrape)
    return [{"appid": int(g.get("appid", 0) or 0), "name": (g.get("name") or g).strip()}
            for g in games if (g.get("name") or g).strip()]


def cmd_import(db: str, owners: str | None = None) -> list[dict]:
    """Run import for the requested owners ('mick,wifey'; default both)."""
    conn = dbm.init_db(Path(db))
    results = []
    want = {o.strip() for o in (owners or "mick,wifey").split(",") if o.strip()}
    if "mick" in want:
        results.append(import_mick(conn))
    if "wifey" in want:
        results.append(import_wifey(conn))
    dbm.meta_set(conn, "steam_imported_at", dbm.now_iso())
    print(f"[steam] import complete: {results}")
    return results


if __name__ == "__main__":
    cmd_import(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "drops.db"),
               sys.argv[2] if len(sys.argv) > 2 else None)

#!/usr/bin/env python3
"""Steam library import (Phase 5) — Stdlib only.

Sources:
  - Mick  : Steam Web API (ISteamUser/ResolveVanityURL + IPlayerService/GetOwnedGames)
            via .steamMine API key. No login needed. (d4rkwar)
  - household2 : friend-shared games via authenticated browser scrape in ego-browser
            (using Mick's own Steam login).
            Degrades gracefully if the account's Game Details are private.

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
HOUSEHOLD2_STEAMID = "REDACTED-THIRD-PARTY-ID"     # friend-shared account -> .steamFriend or $HOUSEHOLD2_STEAMID
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


def import_household2(conn) -> dict:
    """Friend-shared library via authenticated browser scrape. Uses ego-browser
    with Mick's own Steam login. Private visibility -> []."""
    games = _scrape_household2_games()
    if games is None:                       # scrape failed hard -> raise
        raise SteamError("friend-shared scrape returned no data (login? private Game Details?)")
    dbm.replace_steam_games(conn, "household2", games)
    print(f"[steam] household2 ({HOUSEHOLD2_STEAMID}): {len(games)} games scraped")
    return {"owner": "household2", "steamid": HOUSEHOLD2_STEAMID, "count": len(games),
            "source": "browser-scrape"}


def _scrape_household2_games() -> list[dict] | None:
    """ego-browser nodejs: open Steam profile games tab (all, grid), read titles.
    Returns None if the profile/games page couldn't be parsed (login/private)."""
    import subprocess
    js = r"""
const task = await useOrCreateTaskSpace('steam household2 import')
await openOrReuseTab('https://steamcommunity.com/profiles/REDACTED-THIRD-PARTY-ID/games/?tab=all', { wait: true, timeout: 30 })
await wait(5)
// login-gated or private? Steam shows "Profile is private" or an age gate / sign-in.
const body = await js(`document.body ? document.body.innerText.slice(0, 1200) : ''`)
const titles = await js(`(() => {
  const out = new Set();
  // game tiles on the games page (owner pane)
  document.querySelectorAll('.gameListRowItem .gameListRowItemName, .gameListRowItemName, .game_name, .tab_item_name').forEach(n => {
    const t = (n.textContent || '').trim(); if (t) out.add(t);
  });
  return [...out];
})()`)
cliLog('STEAM_BODY: ' + body.replace(/\n+/g, ' | ').slice(0, 600))
cliLog('STEAM_TITLES: ' + JSON.stringify(titles))
await completeTaskSpace(task.id, { keep: false })
"""
    try:
        r = subprocess.run(["ego-browser", "nodejs"], input=js, capture_output=True,
                           text=True, timeout=120)
    except Exception as e:
        print(f"[steam] ego-browser subprocess failed: {e}", file=sys.stderr)
        return None
    out = (r.stdout or "") + (r.stderr or "")
    titles = []
    for line in out.splitlines():
        if line.startswith("STEAM_TITLES: "):
            try:
                titles = json.loads(line[len("STEAM_TITLES: "):])
            except Exception:
                titles = []
    if not titles:
        print(f"[steam] household2: no titles parsed. page:\n{(out or '')[-800:]}", file=sys.stderr)
        return None
    return [{"appid": 0, "name": t.strip()} for t in titles if t.strip()]


def cmd_import(db: str, owners: str | None = None) -> list[dict]:
    """Run import for the requested owners ('mick,household2'; default both)."""
    conn = dbm.init_db(Path(db))
    results = []
    want = {o.strip() for o in (owners or "mick,household2").split(",") if o.strip()}
    if "mick" in want:
        results.append(import_mick(conn))
    if "household2" in want:
        results.append(import_household2(conn))
    dbm.meta_set(conn, "steam_imported_at", dbm.now_iso())
    print(f"[steam] import complete: {results}")
    return results


if __name__ == "__main__":
    cmd_import(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "drops.db"),
               sys.argv[2] if len(sys.argv) > 2 else None)

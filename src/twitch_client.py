#!/usr/bin/env python3
"""Twitch drops fetcher — Phase-1 production shape (Phase-0 proven).

Pipeline per run:
  1. mint a browser-grade Client-Integrity token (ego-browser visit, ~5-8 s)
  2. one inline gql call returns ALL campaigns + inline rewards
  3. normalize into entity dicts

Fallbacks:
  - integrity mint fails  -> reuse stored token if fresh enough, else error
  - gql fails             -> return error; caller keeps DB untouched
  - DOM extraction        -> Phase-3 hardening (skeleton hook)

Stdlib only. Credentials from .logTw (600). Never prints secrets.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRED_FILE = ROOT / ".logTw"
WEB_CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_URL = "https://gql.twitch.tv/gql"
INTEGRITY_URL = "https://gql.twitch.tv/integrity"
TMP_TOKEN = Path("/tmp") / f"drops_integrity_{os.getpid()}.json"

RICH_QUERY = """query {
  currentUser {
    id displayName
    dropCampaigns {
      id name status startAt endAt
      game { id name boxArtURL }
      imageURL
      self { isAccountConnected }
      timeBasedDrops { id name requiredMinutesWatched }
    }
  }
}"""


class FetchError(Exception):
    pass


def load_creds():
    if not CRED_FILE.exists():
        raise FetchError("missing .logTw")
    text = CRED_FILE.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # tolerate trailing garbage (e.g. stray brace) — parse the first JSON object
        obj, _ = json.JSONDecoder().raw_decode(text)
        return obj


def save_creds(d):
    CRED_FILE.write_text(json.dumps(d, indent=2))
    os.chmod(CRED_FILE, 0o600)


def _ego_mint_js(tmp_path: str) -> str:
    """JS heredoc for ego-browser: visit twitch.tv, capture the page's own
    /integrity token (browser-grade, Kasada-proofed), write it to a temp file."""
    return f"""
import fs from 'fs'
const task = await useOrCreateTaskSpace('drops watchdog mint')
try {{
  const initialTabs = await listTabs().catch(() => []);
  if (initialTabs && initialTabs.length > 0) {{
    await gotoUrl('https://www.twitch.tv/').catch(() => openOrReuseTab('https://www.twitch.tv/', {{ wait: true, timeout: 30 }}));
  }} else {{
    await openOrReuseTab('https://www.twitch.tv/', {{ wait: true, timeout: 30 }});
  }}
  await cdp('Page.enable', {{}}).catch(()=>{{}})
  await cdp('Page.addScriptToEvaluateOnNewDocument', {{ source: String.raw`
    (() => {{
      if (window.__dropsInteg) return;
      window.__dropsInteg = [];
      const orig = window.fetch;
      window.fetch = function(...args) {{
        const p = orig.apply(this, args);
        try {{
          const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
          if (url.includes('/integrity')) {{
            p.then(r => r.clone().json().then(j => {{ if (j.token) window.__dropsInteg.push(j.token); }})) .catch(()=>{{}});
          }}
        }} catch(e){{}}
        return p;
      }};
    }})();
  ` }})
  await cdp('Page.reload', {{ ignoreCache: true }}).catch(()=>{{}})
  await wait(6)
  const tok = await js(String.raw`(window.__dropsInteg[0] || null)`)
  if (tok) fs.writeFileSync('{tmp_path}', JSON.stringify({{ token: tok }}))
  else cliLog('MINT:NO_TOKEN')
}} catch(e) {{
  cliLog('MINT:ERR ' + e.message)
}} finally {{
  try {{
    const tabs = await listTabs().catch(() => []);
    for (const t of tabs) {{
      await closeTab(t.id).catch(() => {{}});
    }}
    await completeTaskSpace(task.id, {{ keep: false }}).catch(() => {{}});
  }} catch(e){{}}
}}
"""


def mint_integrity(timeout: int = 45) -> str | None:
    """Mint a browser-grade integrity token via ego-browser. Returns token or None."""
    if TMP_TOKEN.exists():
        TMP_TOKEN.unlink()
    # Keep ego lite running hidden without stealing macOS focus
    try:
        subprocess.run(["open", "-j", "-g", "-a", "/Applications/ego lite.app"],
                       capture_output=True, timeout=5)
        subprocess.run(["osascript", "-e", 'tell application "System Events" to set visible of process "ego lite" to false'],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    js = _ego_mint_js(str(TMP_TOKEN))
    try:
        proc = subprocess.run(
            ["ego-browser", "nodejs"], input=js, capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[mint] ego-browser unavailable: {e}", file=sys.stderr)
        return None
    finally:
        try:
            subprocess.run(["osascript", "-e", 'tell application "System Events" to set visible of process "ego lite" to false'],
                           capture_output=True, timeout=5)
        except Exception:
            pass
    if proc.returncode != 0:
        print(f"[mint] ego-browser rc={proc.returncode}: {proc.stderr[:200]}", file=sys.stderr)
        return None
    if "MINT:ERR" in proc.stdout or "MINT:NO_TOKEN" in proc.stdout:
        print(f"[mint] failed: {proc.stdout.strip()[:200]}", file=sys.stderr)
        return None
    try:
        tok = json.loads(TMP_TOKEN.read_text())["token"]
        TMP_TOKEN.unlink(missing_ok=True)
        return tok
    except Exception as e:
        print(f"[mint] no token file: {e}", file=sys.stderr)
        return None

def _post(url, headers, body=None):
    req = urllib.request.Request(url, data=body or b"{}", method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"_http": e.code}


def get_integrity_token(creds, reuse_min: int = 1440) -> str:
    """Fresh browser-minted token, falling back to a stored fresh one (24h reuse default)."""
    stored = creds.get("web_integrity_token")
    stored_ts = float(creds.get("web_integrity_minted_at") or 0)
    if stored and time.time() - stored_ts < reuse_min * 60:
        return stored
    tok = mint_integrity()
    if not tok:
        if stored:
            print("[integrity] mint failed — reusing stored token", file=sys.stderr)
            return stored
        raise FetchError("integrity mint failed and no stored token")
    creds["web_integrity_token"] = tok
    creds["web_integrity_minted_at"] = time.time()
    save_creds(creds)
    return tok


def fetch_raw(creds=None, integrity=None) -> dict:
    """One inline gql call -> raw response dict."""
    creds = creds or load_creds()
    token = creds.get("web_auth_token")
    if not token:
        raise FetchError("no web_auth_token in .logTw")
    integrity = integrity or get_integrity_token(creds)
    headers = {
        "Client-ID": WEB_CID,
        "Content-Type": "application/json",
        "Authorization": "OAuth " + token,
        "Client-Integrity": integrity,
        "Accept-Language": "en-US",
        **{k: v for k, v in (creds.get("device_headers") or {}).items()},
    }
    resp = _post(GQL_URL, headers, json.dumps({"query": RICH_QUERY}).encode())
    if resp.get("_http"):
        raise FetchError(f"gql HTTP {resp['_http']}")
    if resp.get("errors"):
        msgs = [e.get("message") for e in resp["errors"]]
        if "integrity" in json.dumps(msgs).lower():
            # token rejected — force a fresh mint and retry once
            integrity = mint_integrity()
            if not integrity:
                raise FetchError(f"integrity re-mint failed after: {msgs}")
            headers["Client-Integrity"] = integrity
            resp = _post(GQL_URL, headers, json.dumps({"query": RICH_QUERY}).encode())
            if resp.get("errors"):
                raise FetchError(f"gql retry failed: {[e.get('message') for e in resp['errors']]}")
        else:
            raise FetchError(f"gql errors: {msgs}")
    camps = ((resp.get("data") or {}).get("currentUser") or {}).get("dropCampaigns")
    if camps is None:
        raise FetchError("gql response has no dropCampaigns")
    return resp


def normalize(c: dict) -> dict:
    """gql campaign -> entity dict (UTC ISO timestamps, rewards list)."""
    game = c.get("game") or {}
    rewards = []
    for t in c.get("timeBasedDrops") or []:
        rewards.append({
            "name": t.get("name") or "",
            "required_minutes": t.get("requiredMinutesWatched") or 0,
        })
    return {
        "id": c["id"],
        "game_id": game.get("id"),
        "game_name": game.get("name") or "",
        "title": c.get("name") or "",
        "status": c.get("status") or "ACTIVE",
        "start_at": (c.get("startAt") or "").replace("Z", "+00:00"),
        "end_at": (c.get("endAt") or "").replace("Z", "+00:00"),
        "image_url": c.get("imageURL") or game.get("boxArtURL") or "",
        "details_url": f"https://www.twitch.tv/drops/campaigns?drop={c['id']}",
        "channels_summary": None,
        "is_connected": bool((c.get("self") or {}).get("isAccountConnected")),
        "rewards": rewards,
    }


def fetch_campaigns(creds=None, integrity=None) -> list[dict]:
    """Full live fetch -> normalized campaign entities."""
    resp = fetch_raw(creds, integrity)
    camps = resp["data"]["currentUser"]["dropCampaigns"]
    return [normalize(c) for c in camps]

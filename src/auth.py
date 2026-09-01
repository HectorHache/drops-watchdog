#!/usr/bin/env python3
"""Twitch OAuth 2.0 Device-Flow helper (Phase 0 → production).

Commands:
  device   -> request a device code, print the URL + user code, save state to .logTw
  poll     -> exchange the device code for access+refresh tokens (call after authorize)
  refresh  -> refresh the access token using the stored refresh token
  status   -> show token presence/expiry (masked)

Stdlib only. Credentials live in .logTw (chmod 600). Never prints secrets.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRED_FILE = ROOT / ".logTw"
SCOPES = "user:read:email"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
GRANT_DEVICE = "urn:ietf:params:oauth:grant-type:device_code"


def load_creds():
    if not CRED_FILE.exists():
        sys.exit("missing .logTw")
    try:
        return json.loads(CRED_FILE.read_text())
    except Exception as e:
        sys.exit(f".logTw parse error: {e}")


def save_creds(d):
    CRED_FILE.write_text(json.dumps(d, indent=2))
    os.chmod(CRED_FILE, 0o600)


def post(url, data: dict):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            raise


def cmd_device():
    creds = load_creds()
    for k in ("client_id", "client_secret"):
        if not creds.get(k):
            sys.exit(f".logTw missing '{k}'")
    resp = post(DEVICE_URL, {"client_id": creds["client_id"], "scopes": SCOPES})
    if resp.get("error"):
        sys.exit(f"device request failed: {resp.get('error')} — {resp.get('message', '')}")
    creds["device_code"] = resp["device_code"]
    creds["device_expires_at"] = time.time() + int(resp["expires_in"])
    save_creds(creds)
    print(f"user_code       : {resp['user_code']}")
    print(f"verification_uri: {resp['verification_uri']}")
    print(f"expires_in      : {resp['expires_in']} s | poll interval {resp['interval']} s")
    print("(open the URI, enter the code, authorize with the THROWAWAY account)")


def cmd_poll():
    creds = load_creds()
    if "device_code" not in creds:
        sys.exit("no device_code — run 'device' first")
    if time.time() > creds.get("device_expires_at", 0):
        sys.exit("device code expired — run 'device' again")
    resp = post(TOKEN_URL, {
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],   # confidential client
        "device_code": creds["device_code"],
        "grant_type": GRANT_DEVICE,
    })
    if resp.get("error"):
        sys.exit(f"token exchange pending/denied: {resp.get('error')} — {resp.get('message', '')}")
    creds.pop("device_code", None)
    creds.pop("device_expires_at", None)
    creds["access_token"] = resp["access_token"]
    creds["refresh_token"] = resp["refresh_token"]
    creds["expires_at"] = time.time() + int(resp.get("expires_in", 0))
    creds["scope"] = resp.get("scope", "")
    save_creds(creds)
    print(f"TOKENS OK — access len {len(resp['access_token'])} | scope: {resp.get('scope')}")
    print("expires_at:", time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(creds["expires_at"])))


def cmd_refresh():
    creds = load_creds()
    if "refresh_token" not in creds:
        sys.exit("no refresh_token — run device + poll first")
    resp = post(TOKEN_URL, {
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": creds["refresh_token"],
    })
    if resp.get("error"):
        sys.exit(f"refresh failed: {resp.get('error')} — {resp.get('message', '')}")
    creds["access_token"] = resp["access_token"]
    creds["refresh_token"] = resp.get("refresh_token", creds["refresh_token"])
    creds["expires_at"] = time.time() + int(resp.get("expires_in", 0))
    save_creds(creds)
    print(f"REFRESH OK — access len {len(resp['access_token'])}")


def cmd_status():
    c = load_creds()
    mask = lambda v: f"{str(v)[:6]}…({len(str(v))})" if v else None
    print("client_id      :", mask(c.get("client_id")))
    print("access_token   :", mask(c.get("access_token")))
    print("refresh_token  :", mask(c.get("refresh_token")))
    exp = c.get("expires_at")
    print("expires_at     :", time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(exp)) if exp else "n/a")
    print("scope          :", c.get("scope"))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("device", "poll", "refresh", "status"):
        sys.exit(__doc__)
    globals()[f"cmd_{sys.argv[1]}"]()

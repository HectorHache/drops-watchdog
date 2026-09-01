#!/usr/bin/env python3
"""Telegram delivery via the Heimdall bot token (direct Bot API, HTML).

- token read from ~/.hermes/profiles/heimdall/.env (TELEGRAM_BOT_TOKEN) at runtime
- NEVER printed; masked in logs
- plain API calls only (getChat/sendMessage) — no getUpdates (would conflict with
  the running Hermes gateway polling the same token)
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_ENV = Path.home() / ".hermes" / "profiles" / "heimdall" / ".env"


class NotifyError(Exception):
    pass


def bot_token(env_path: Path = DEFAULT_ENV) -> str:
    if "TELEGRAM_BOT_TOKEN" in os.environ:
        return os.environ["TELEGRAM_BOT_TOKEN"]
    if not env_path.exists():
        raise NotifyError(f"heimdall .env not found: {env_path}")
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("TELEGRAM_BOT_TOKEN") and "=" in line:
            tok = line.split("=", 1)[1].strip().strip('"').strip("'")
            if tok:
                return tok
    raise NotifyError("TELEGRAM_BOT_TOKEN not found in heimdall .env")


def api_call(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}


def resolve_chat(token: str, chat_ref: str) -> int | None:
    """Resolve @username -> numeric chat id (safe plain call)."""
    r = api_call(token, "getChat", chat_id=chat_ref)
    if r.get("ok"):
        return r["result"]["id"]
    raise NotifyError(f"getChat {chat_ref} failed: {r.get('description', r)}")


def send_message(token: str, chat_id, text: str, parse_mode: str = "HTML",
                 disable_web_page_preview: bool = True) -> dict:
    r = api_call(token, "sendMessage", chat_id=chat_id, text=text,
                 parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)
    if not r.get("ok"):
        raise NotifyError(f"sendMessage to {chat_id} failed: {r.get('description', r)}")
    return r


def send_digest(token: str, chat_id, msg: str) -> dict:
    return send_message(token, chat_id, msg)

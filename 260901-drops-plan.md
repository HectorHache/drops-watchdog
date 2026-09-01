# Twitch Drops Watchdog + Dashboard — Master Plan (v2)

**Status:** v3.0 — **Phases 0-5 DONE** (Steam both libraries imported; wifey=162 via auth scrape, mick=1339 API; favorites CLI + site tab; Steam badge live; 24/7 personal DM cron; CLOSED-persistence fix). Next: Phase 6 (in-site click-to-favorite via CF Functions + KV, weekly recap, GH Actions mirror, PWA).: Steam import live (mick API = 1339 games, badge on 11/102 campaigns), favorites CLI + site Favorites tab, 24/7 personal DM cron (`twitch-drops-personal`, verified Delta Force <24h DM sent), CLOSED-persistence fix (no flip-flop). Remaining: wifey friend-shared scrape (needs Mick's Steam login + Guard code), in-site selector round-trip (Phase 6 KV).
**Version:** 2.2.0 (supersedes v2.1.0 / v2.0.0 / v1 draft)
**File:** `260901-drops-plan.md` (Mick's naming convention: YYMMDD-<project>-plan.md)
**Date:** 2026-09-01
**Host:** Mac Mini M4 Pro (this machine, TZ `Europe/Madrid` — verified CEST)
**Domain:** `drops.hache.app` (owned: hache.app @ Porkbun — DNS not yet configured, verified NXDOMAIN)
**Telegram:** Heimdall Hermes profile → DM chat `2350638` ("Héctor") + **dedicated drops group** (Phase 2)
**Workspace:** `~/Documents/Workspaces/drops/`
**Cost model:** deterministic Python, zero LLM calls, zero extra daemons

---

## 0. Why this exists

Mick missed short-window Twitch Drop campaigns (some run only a few hours on one day).
Goal: an **hourly watchdog during wake hours (07:00 → 00:00 local)** that tells him:

1. **What's new** (campaign, game, running dates/times in CET/CEST, reward item NAMES — no images needed in Telegram)
2. **What's leaving soon** (≤ 24h remaining — the critical alert)
3. **Never** anything from the "Closed Drop Campaigns" section
4. Plus a **live dashboard at `drops.hache.app`** (images, filters, countdowns, auto-refresh, history archive) and a **favorites/learning layer** that prioritises the games Mick actually likes (Steam-driven, see Phase 5).

## 0.1 Decisions log (locked 2026-09-01)

| # | Topic | Decision |
|---|-------|----------|
| D1 | Hosting | **Cloudflare Pages** (free, private repo, custom domain `drops.hache.app`, auto-deploy on push) |
| D2 | Twitch auth | **Twitch dev app + device-flow token** on a THROWAWAY account (client type: **Confidential** — the watchdog is a server-side client; `auth.py` sends `client_secret` in the token exchange; swap to Public only if Twitch rejects confidential) |
| D3 | Message policy | **Delta-only**: message only when something changed (NEW, entered <24h, ended, material change) — otherwise silent |
| D4 | Favorites | **Steam-driven**: Mick (`steamcommunity.com/id/d4rkwar/`, Steam Web API) + wifey (`profiles/76561198051826838`, friend-shared, authenticated browser scrape — surprise preserved), then an in-site **selector** |
| D5 | Schedule | **07:00 → 23:00 + 00:00** local (Europe/Madrid), DST auto |
| D6 | Site images | **Repo-local hybrid mirror** with auto-prune of ended campaigns' image files (data kept forever) + branded game-name fallback art |
| D7 | Deploys | **Commit-on-change only** (new/ended/changed campaign or new image) → stays far under CF Pages' 500 builds/month free cap; hourly fetch still runs locally |
| D8 | Source | **Inline GraphQL with bearer token as PRIMARY** (no persisted-query hash dependency — Twitch accepts inline queries, proven in recon); persisted hashes = fallback #1; browser DOM = fallback #2 |
| D9 | Escaping | **All campaign content treated as untrusted**: `html.escape()` in Telegram composer; site renders via `textContent`/`createElement`, never `innerHTML` (prevents broken HTML + XSS) |
| D10 | Caching | `docs/_headers` → `drops.json: Cache-Control: no-store` + `?ts=` cache-busting (kills stale-site bugs) |
| D11 | Fetch cadence | **Fetch every 30 min** (07:00–23:30 + 00:00/00:30); messages stay delta-only — catches sub-hour campaigns |
| D12 | Details calls | `DropCampaignDetails` fetched **only for new/materially-changed campaigns** (cache the rest) → ~20–50 calls/day instead of ~750; backoff on 429 |
| D13 | Staleness | Site shows "updated X ago" honestly; worst-case lag ≈ fetch (≤30m) + deploy (~1m) + poll (≤10m) |
| D14 | ToS | Attribution line on site ("data & art © Twitch"), personal-use only, no redistribution; throwaway-account ban → `auth_error` alert → re-auth (~2 min) |
| D15 | Secrets | `.logTw`/`.steamMine` chmod 600 (done); optional macOS Keychain via `security` CLI (config flag) |
| D16 | Locale | **en-US** fixed for the throwaway Twitch account + all rendering (Mick's systems are en-US) |
| D17 | Telegram | **Dedicated private group** (invite-only, anyone can join) = scheduled digests for everyone; **private DM to Mick** = his personal alerts (favorites <24h, etc.). Telegram has NO per-user message visibility in groups → personal alerts go to DM (see §5.1) |

## 0. Why this exists (motivation recap)
[see top of section]

---

## 1. Verified recon findings (2026-09-01 — evidence-based)

| # | Finding | Evidence | Impact |
|---|---------|----------|--------|
| R1 | `gql.twitch.tv/gql` works anonymously (web client-id `kimne78kx3ncx6brgo4mv6wki5h1ko`) | HTTP 200 probe | Foundation |
| R2 | **Campaign data is LOGIN-GATED** — anonymous `ViewerDropsDashboard` (hash `5a4da2ab…17f619`) → `currentUser: null`, 0 campaigns | Direct probe | Auth mandatory |
| R3 | `dropCampaigns` is not a top-level Query field; drops live under `currentUser` | Inline-query probe → schema error | Confirms R2 |
| R4 | Logged-out website renders **zero campaigns** (empty shell) | ego-browser full DOM capture | UI login-gated too |
| R5 | `DropsHighlightService_AvailableDrops` (hash `9a62a09b…0716f`) works anonymously but is per-channel | Probe with real channel id | Secondary source |
| R6 | `DropCampaignDetails` (hash `039277bf…77c1`, vars `channelLogin`+`dropID`) → `timeBasedDrops[]` reward names + minutes | Community docs (TwitchDropsFarmer/StreamDropCollector/Alorf) | Reward extraction |
| R7 | Persisted hashes **rotate**; robust pattern = capture live hash, seed known-good, re-capture on failure | Community evidence | B2: inline-gql primary removes this fragility |
| R8 | **Hermes cron schedules are LOCAL wall-clock, DST-aware** (`asgardian-health` `0 9 * * *` → next `09:00:00+02:00`) | `hermes cron list` | Schedule just works |
| R9 | Hermes `--no-agent` cron scripts proven (stdout = delivered; empty = silent) | `pvault-brief` precedent | Watchdog pattern |
| R10 | Heimdall DM `2350638`, token in `~/.hermes/profiles/heimdall/.env` (600) | channel_directory.json | Delivery target |
| R11 | Python 3.14.7, git 2.55, gh 2.98, ego-browser installed, git identity set | checks | Stack ready |
| R12 | DropHunter: no public API (SPA behind Cloudflare, `/api/*` 404) | probes | Tertiary fallback only |
| R13 | **Twitch gql accepts INLINE queries** (captured `PlaybackAccessToken_Template` sent inline by the site itself; anonymous inline `currentUser` probe returned cleanly) | ego-browser network capture + probes | B2: inline-gql primary possible |

---

## 2. Architecture overview

```
                    ┌─────────────────────────────┐
                    │  Twitch Drops (login-gated) │
                    │  gql.twitch.tv (en-US, bearer) │
                    └──────────────┬──────────────┘
                                   │  every 30 min (07:00–23:30 + 00:00, local)
                                   ▼
                 ┌─────────────────────────────────┐
                 │  watchdog.py  (deterministic)   │
                 │  fetch → normalize → diff → DB  │
                 └───────┬───────────────┬─────────┘
                         │               │
        Telegram (delta-only)       export public state
        group = digests (D17)        (on change only, D7)
        DM    = personal alerts      │
                         ▼           ▼
              ┌──────────────────┐  ┌─────────────────────────┐
              │ Telegram group + │  │ docs/drops.json + index │
              │ Mick's DM (2350638)│  │ + assets/ (pruned)     │
              └──────────────────┘  └───────────┬─────────────┘
                                                │ git commit+push ONLY when changed (D7)
                                                ▼
                                     ┌─────────────────────────┐
                                     │ drops.hache.app (CF Pages)│
                                     │ polls drops.json /10m    │
                                     │ no-store cache (D10)     │
                                     └─────────────────────────┘
```

**Execution paradigm (per Mick — "triggered on each execution, no permanent job"):**
- The **Hermes gateway is already permanently running** (Asgardian Ops multiplex). Its cron ticker fires our job every 30 min; the job **runs, exits**. No new daemon.
- Job = Hermes cron `--no-agent` → `~/.hermes/scripts/twitch_drops_sync.sh` → `src/main.py`.
- SQLite `data/drops.db` is the **single source of truth** — Telegram digest, personal alerts, and the website all read from it. One writer, multiple consumers.

---

## 3. Data source strategy

### 3.1 Authentication (locked D2 + Phase-0 VERIFIED results)
- **Twitch dev app** `drops-watchdog` (throwaway account, Confidential, redirect `http://localhost:17563`) exists; **device flow validated** — BUT gql.twitch.tv **rejects app tokens** (401): Twitch's gql only accepts tokens from the **web client** (`kimne78kx3ncx6brgo4mv6wki5h1ko`). Device token kept as Helix fallback only.
- **Production credentials (Phase-0 proven):**
  1. `web_auth_token` — the `auth-token` cookie from a logged-in browser session (throwaway @ twitch.tv, captured once via ego-browser; lasts weeks–months; re-capture ~monthly).
  2. `Client-Integrity` token (`v4.local.…`) — **must be minted in the browser** (Kasada proof); bare-HTTP tokens are rejected for `dropCampaigns`. Minted per run via a quick ego-browser visit (~5 s).
     > ⚠️ **2FA session reality (Mick 2026-09-01):** with 2FA, Twitch keeps the login cookie **~30 days**, not months → `status` shows auth age vs `auth.max_age_days: 25` and warns; re-login (~2 min) on expiry via `auth_error` alert.
  3. Device headers (`X-Device-Id`, `Client-Session-Id`, `Client-Version`, `Device-ID`) — captured once, fixed per browser session.
  All persisted in `.logTw` (600). Session death → `auth_error` alert → Mick re-logs-in (~2 min).
- **Rewards are inline** (`timeBasedDrops[]`) — **no N+1 details calls** (B7 fully resolved).

### 3.2 Fetch chain (VERIFIED in Phase 0 — robustness-first)
1. **PRIMARY: inline GraphQL** — one call returns everything:
   `currentUser { dropCampaigns { id name status startAt endAt game{id name boxArtURL} imageURL self{isAccountConnected} timeBasedDrops{id name requiredMinutesWatched} } }`
   with `web_auth_token` + fresh browser-minted `Client-Integrity` + device headers. **No persisted-query hash at all** (B2/D8 proven live: 122 campaigns).
2. **Integrity minting:** quick ego-browser visit (any twitch.tv page, ~5 s) per run → fresh `Client-Integrity` token (Kasada proof; bare HTTP tokens are rejected). Also keeps the session alive and provides the DOM fallback.
3. **Fallback #1:** DOM extraction from the same browser visit (rendered cards — game, dates, rewards, images, Closed section).
4. **Tertiary:** DropHunter DOM (auth-free, no reward names — degraded mode).
5. Validation gate: schema-validated fetches only; **malformed/empty data never overwrites the DB** (no false "ended" mass-prunes).
6. 429 → exponential backoff. Integrity-token TTL measurement is a Phase-1 optimization (default: mint per run).

### 3.3 Campaign entity model
- `id` (Twitch campaign id — stable key), `game_id`, `game_name`
- `title` (campaign name), `status`: `ACTIVE | UPCOMING | EXPIRED | CLOSED`
- `start_at`, `end_at` — ISO-8601 **UTC** (render-time conversion to Europe/Madrid via `zoneinfo`)
- `image_url` (campaign art), `details_url` (`twitch.tv/drops/campaigns?drop=…`)
- `rewards[]`: `{ name, required_minutes, image_url? }` — item NAMES (Mick's ask)
- `channels_summary` (all channels vs specific broadcasters, if exposed)
- `first_seen_at`, `last_seen_at`, `raw_json` snapshot

### 3.4 "Closed Drop Campaigns" policy (explicit requirement)
- Seen in Closed section / `status=EXPIRED` / `end_at ≤ now` → marked `CLOSED` (+`archived_at`), **removed from watchdog** (never alerted as new/ending), excluded from the active site feed.
- **Archive rule:** campaigns we ever saw active → kept forever in DB, listed in the site's History section (data-only). Closed-section entries we never saw active → recorded but not invented into history.
- No "ended" alert for campaigns that were never active in our window.

---

## 4. Watchdog logic (deterministic, per 30-min run)

```
1. fetch campaigns (D8 chain, en-US D16)              → live set
2. upsert into campaigns table (keyed by id)          → DB becomes truth
3. classify:
   - NEW        : id not seen before AND not CLOSED
   - ENDING_24H : now < end_at ≤ now+24h   (critical)
   - ENDING_48H : now+24h < end_at ≤ now+48h (watch)
   - ENDED      : end_at ≤ now OR status EXPIRED/CLOSED
   - CHANGED    : same id, materially different (dates/rewards/title)
4. dedupe via notification_ledger (one-time per campaign per alert kind)
5. decide Telegram payload (§5) — delta-only (D3):
   send only if NEW / entered <24h / ended / material change
   else print nothing → Hermes silent no-agent run
6. write events to DB (full audit trail)
7. build docs/ snapshot (§6) + git commit/push ONLY if changed (D7)
8. exit 0
```

**Dedupe rules (anti-spam):**
- "New" announced once (`first_notified_at`). "Ending <24h" announced once (`ending_soon_notified_at`), but the campaign stays in every digest's <24h list while inside the window (always small).
- **Favorites personal alert (improvement #2, D17):** a favorite game entering <24h triggers an **immediate DM to Mick even outside wake hours** (configurable on/off) — the "never miss MY drops" rule.
- Failure handling: fetch failure → keep DB, retry next run, no false alerts; 3+ consecutive failures → error ping; stale `last_success_at` visible on site (D13).

---

## 5. Telegram formatting & delivery (Heimdall)

### 5.1 Delivery channels (D17 — honest Telegram reality)
- **Telegram does NOT support Slack/Discord-style per-user message visibility inside groups.** No bot API flag makes a group message visible to one member only (spoiler/expire features hide from *everyone* or are revealable by *anyone* — not per-user).
- **Design that faithfully delivers the intent:**
  - **Dedicated private group** (invite-only, joinable by anyone): the bot posts the **scheduled digest** — nicely formatted, same template — for everyone.
  - **Private DM to Mick (`2350638`)**: **only his personal triggers** — favorites entering <24h (24/7), his priority pings, personal error/health alerts.
- Same bot (Heimdall), two channels. Group = public heartbeat; DM = private signal.
- Group creation + id resolution is a Phase-2 step (create group → add heimdall bot as admin → id discovered from gateway logs, no `getUpdates` polling (409 conflict with the Hermes gateway); fallback: temporary @RawDataBot trick).
- Optional later: in-group bot commands (`/drops`, `/fav <game>`) via Hermes heimdall profile (needs `TELEGRAM_GROUP_ALLOWED_CHATS` env per multiplex wiring).

### 5.2 Template (HTML + emoji, group & DM share the composer)

```html
🎮 <b>TWITCH DROPS WATCHDOG</b> — <i>Tue 1 Sep · 15:04 CEST</i>
━━━━━━━━━━━━━━━━━━━━━━

🚨 <b>ENDING &lt;24H (1)</b>
⏳ <b>Rogue Trader</b> — ends <u>tonight 23:59</u> (8h 55m left)
   🏆 <i>Voidship Crew Pack</i> · <a href="…">Open drop</a>

✨ <b>NEW (2)</b>
🎁 <b>Metaphor: ReFantazio</b> — <i>Palace Aegis Set</i>
   📅 1 Sep 18:00 → 3 Sep 12:00 CEST (2d 18h)
   🔗 <a href="…">Details</a>
🎁 <b>Warhammer 40K</b> — <i>Chapter Pack</i>
   📅 1 Sep 20:00 → 2 Sep 20:00 CEST
   🔗 <a href="…">Details</a>

🕒 <b>ENDING 24–48H (1)</b> · 🏁 <b>ENDED (1)</b> since last check
━━━━━━━━━━━━━━━━━━━━━━
📊 12 active · 🌐 <a href="https://drops.hache.app">drops.hache.app</a>
```

**Rules:**
- **Escaping (D9):** every campaign-derived string passes `html.escape()` — game names with `&`, `<`, `>` can't break markup.
- Emoji per section; bold/italic/underline/links via Telegram HTML (`&lt;24H`); newlines only (no `<br>`/tables in Telegram HTML).
- All times local Europe/Madrid with explicit CEST/CET suffix (zoneinfo).
- Compact single-line-per-reward; cap ~3800 chars (Telegram 4096) → "+N more on drops.hache.app".
- **Silent empty state (D3):** nothing changed → no message at all.
- Personal DM variant: same template, only the personal sections (⭐ favorites), headed "🔔 PERSONAL".

---

## 6. Website spec — `drops.hache.app`

### 6.1 Delivery
- Watchdog writes `docs/drops.json` + `docs/index.html` (single-file SPA, established pattern) + `docs/feed.xml` (RSS) + `docs/assets/` (images).
- **Commit-on-change (D7):** push only when content changed → well under CF Pages' 500 builds/month.
- **Cache (D10):** `docs/_headers` → `drops.json: Cache-Control: no-store`; page polls `drops.json?ts=<now>` every **10 min**, hash-compares, re-renders in place. "Updated X ago" indicator (D13).
- **Images (D6):** repo-local mirror, auto-pruned the moment a campaign ends (live tree stays clean; git history growth ~1–5 MB/year — accepted, see B5 discussion; R2 is the future escape hatch, NOT used now). Branded game-name fallback SVG for missing art.
- **Attribution (D14):** footer "Data & art © Twitch — personal use monitoring".

### 6.2 `drops.json` schema (public)
```json
{
  "generated_at": "…", "tz": "Europe/Madrid",
  "campaigns": [
    {"id":"…","game":"Rogue Trader","title":"…","status":"ACTIVE",
     "startAt":"…Z","endAt":"…Z","imageUrl":"…","detailsUrl":"…",
     "rewards":[{"name":"Voidship Crew Pack","minutes":120}],
     "isNew":false,"hoursLeft":6.9,"inSteamLibrary":true}
  ],
  "archive": [ {"id":"…","game":"…","title":"…","startAt":"…","endAt":"…","status":"CLOSED"} ],
  "favorites": ["Rust","Overwatch 2"], "favoritesOn": true,
  "counts": {"active":12,"new":2,"ending24":1,"ending48":1}
}
```
- `archive` = full history (data-only, compact rows; rewards only for active campaigns).
- `inSteamLibrary` = badge flag from Phase 5 library mapping.

### 6.3 UI features (incl. agreed improvements)
- **Sections (improvement #7):** the header (filters/search/tabs/toggles) stays FIXED; switching sections/toggles only re-renders the content underneath (no page reload, no scroll jump).
  - `🔥 All Active` · `🚨 <24h` · `⏰ <48h` · `✨ New` · `⭐ Favorites` · `📅 Upcoming` · `🗄️ History`
- **Priority toggle (improvement #6):** a switch "⭐ Priority view ON/OFF" — ON = favorites-weighted ordering (default for Mick), OFF = neutral "what everyone sees" ordering. Cycling it re-sorts the same grid.
- **Filters:** search (game/title/reward), game dropdown (with counts), sort (expiring soonest / newest / longest / A–Z).
- **Cards:** game art, game name, campaign title, dates (CEST/CET), reward NAMES with watch-minutes, live 1s countdown, status badge, "🎮 In your Steam library" badge (improvement #3), **watch-minutes decision aid (improvement #8):** "claim all: 2h 30m" + for favorites "you need 2h — ends in 5h ✓/✗".
- **History section (improvement #7):** data-only archive — "what ran on date X", per-month view, filter by game. From the permanent DB rows.
- **Interaction buttons (improvement #1):** ⭐ favorite / 👍 / "remind me when this ends" → CF Pages **Function** → Cloudflare **KV** (free tier) → watchdog reads KV each sync → learning signals + immediate personal alerts. Zero always-on server.
- **Design (tech freedom):** dark premium gaming UI (Twitch purple on deep slate); vanilla JS core (fast first paint) + optional **Three.js ambient layer** (auto-disabled reduced-motion/low-power/mobile); lazy images, `content-visibility`, hash-diff DOM updates.
- **Extras:** RSS (`/feed.xml`), shareable anchors (`#campaign-<id>`), empty states, favicon, **ES/EN toggle** (improvement #11), **PWA-lite** (manifest + offline data.json cache), weekly stats mini-panel (campaigns ended this week, top games, avg duration).

---

## 7. Hosting & deployment (locked D1 + B1/D7)

- **Cloudflare Pages**, free tier, **private GitHub repo**.
- **Deploy policy (D7):** git push only when `docs/` content changed (new/ended/changed campaign, new image, favorites change). Typical 2–10 pushes/day → far under the 500 builds/month cap. Local DB fetch still runs every 30 min regardless.
- **Images:** in the repo (`docs/assets/`), pruned on end — **no R2, no second storage system** (one-size-fits-all: everything lives in the private repo; the only two systems are GitHub (source) + CF Pages (deploy) — no cross-system data dependency). If repo growth ever matters (years), a one-off history rewrite or R2 migration is a config change, not an architecture change.
- **Setup (Phase 3, interactive — B9):** GitHub↔Cloudflare link → Pages project (root = `docs/`, no build command) → custom domain `drops.hache.app` → CNAME at Porkbun → TLS auto. ~15 min of Mick's clicks, wizard-driven.
- **GitHub Actions mirror (improvement #9, optional):** scheduled workflow reusing the same Python (Twitch token as Actions secret; diff-state in-repo) keeps the site fresh even if the Mac is off.

---

## 8. Database schema (SQLite — `data/drops.db`)

```sql
CREATE TABLE campaigns (
  id            TEXT PRIMARY KEY,          -- Twitch campaign id
  game_id       TEXT,
  game_name     TEXT NOT NULL,
  title         TEXT NOT NULL,
  status        TEXT NOT NULL,             -- ACTIVE|UPCOMING|EXPIRED|CLOSED
  start_at      TEXT NOT NULL,             -- ISO-8601 UTC
  end_at        TEXT NOT NULL,
  image_url     TEXT,
  details_url   TEXT,
  channels_summary TEXT,
  raw_json      TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  is_closed     INTEGER DEFAULT 0,
  archived_at   TEXT                       -- set when campaign ends (history retention)
);
-- NOTE: campaign rows + rewards are NEVER deleted (history kept forever). Only
-- docs/assets/ image FILES for ended campaigns are pruned. See §6.1.
CREATE TABLE rewards (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id   TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  required_minutes INTEGER DEFAULT 0,
  image_url     TEXT,
  sort          INTEGER DEFAULT 0
);
CREATE TABLE notification_ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id   TEXT NOT NULL REFERENCES campaigns(id),
  kind          TEXT NOT NULL,             -- NEW_CAMPAIGN|ENDING_24H|ENDING_48H|ENDED|PERSONAL|ERROR
  sent_at       TEXT NOT NULL,
  channel       TEXT NOT NULL,             -- telegram:group|telegram:dm
  payload       TEXT
);
CREATE TABLE events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  kind          TEXT NOT NULL,             -- new|ending_24h|ended|changed|fetch_error|auth_error
  campaign_id   TEXT,
  payload       TEXT
);
CREATE TABLE favorites (
  game_name     TEXT PRIMARY KEY,
  weight        INTEGER DEFAULT 10,
  instant_alert INTEGER DEFAULT 1,         -- 24/7 personal DM on <24h (improvement #2)
  source        TEXT DEFAULT 'manual',     -- manual|steam|learned
  created_at    TEXT NOT NULL
);
CREATE TABLE kv_interactions (             -- CF KV mirror (improvement #1): site→watchdog signals
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  kind          TEXT NOT NULL,             -- favorite|like|remind
  campaign_id   TEXT,
  value         TEXT
);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);  -- schema_version, gql info, auth state,
                                                 -- last_scrape_at, consecutive_failures, notified flags
CREATE INDEX idx_campaigns_status_end ON campaigns(status, end_at);
CREATE INDEX idx_ledger_lookup ON notification_ledger(campaign_id, kind);
```

---

## 9. Scheduling (verified local-time)

```bash
hermes cron create "0,30 7-23,0 * * *" \
  --name twitch-drops-sync \
  --script twitch_drops_sync.sh \
  --no-agent \
  --deliver local \
  --workdir ~/Documents/Workspaces/drops
```
- Runs every 30 min, 07:00 → 23:30 + 00:00/00:30 local (Europe/Madrid); **DST handled automatically** (R8 proof). Sleep 01:00–06:30 = nothing.
- `twitch_drops_sync.sh` (in `~/.hermes/scripts/`) wraps `python3 ~/Documents/Workspaces/drops/src/main.py`.
- Fallback if Hermes cron ever misbehaves: launchd TZ-pinned user agent (precedent exists).
- Manual: `hermes cron run <id>` + `hermes cron runs <id>`.

---

## 10. Repository layout (`~/Documents/Workspaces/drops/` — private git repo)

```
drops/
├── 260901-drops-plan.md   ← this file (canonical)
├── README.md              ← quickstart + architecture summary
├── .logTw                 ← Twitch app credentials + tokens (GITIGNORED, chmod 600 ✓)
├── .steamMine             ← Steam API key (GITIGNORED, chmod 600 ✓)
├── src/
│   ├── config.yaml        ← profile, group/dm ids, thresholds, favorites, endpoints, locale en-US
│   ├── auth.py            ← device-flow authorize + token refresh + Keychain option (D15)
│   ├── twitch_client.py   ← inline-gql primary + persisted fallback + hash capture + DOM fallback
│   ├── db.py              ← sqlite layer (schema, upserts, queries)
│   ├── watchdog.py        ← diff/classify/dedupe/events
│   ├── notify.py          ← Telegram HTML composer (escaping D9) + group/DM sender
│   ├── kv.py              ← CF Pages Function/KV read (improvement #1)
│   ├── site_builder.py    ← docs/ generation (json, html, feed, assets, prune, _headers)
│   ├── steam_import.py    ← Phase 5: API (Mick) + browser scrape (wifey) → steam_games.json
│   └── main.py            ← orchestrator (fetch → db → watchdog → notify → build → push)
├── scripts/
│   ├── run_once.sh        ← manual dry-run / one-shot
│   └── install.sh         ← repo init, deps check, cron registration, .env template
├── data/                  ← drops.db + assets (GITIGNORED; DB rows kept forever)
├── docs/                  ← published site (Pages source): index.html, drops.json, feed.xml, assets/, _headers, favicon
├── tests/                 ← unittest (stdlib) + fixtures from live captures
└── docs_research/         ← recon notes, source probes, superseded drafts
```

**Secrets policy (D15):** `.logTw`/`.steamMine` local-only, chmod 600 (verified); nothing secret enters the repo; optional macOS Keychain via `security`.

---

## 11. Roadmap & phases (gate-first)

| Phase | Deliverable | Acceptance gate |
|-------|------------|-----------------|
| **0 — Pin the source** ✅ **DONE (2026-09-01)** | Device flow validated (confidential OK); discovered web-auth-token + browser-minted integrity = the real gql credentials; inline rich query proven (122 campaigns, rewards inline, EXPIRED status = Closed section); fixtures saved; dry-run classification correct | **GATE MET** — full log in `docs_research/phase0-results.md` |
| **1 — Core engine** ✅ **DONE (2026-09-01)** | `src/{db,twitch_client,watchdog,main}.py` (stdlib-only): live fetch (browser-minted integrity + inline gql), normalize, upsert DB, classify (NEW/<24h/<48h/ENDED/CLOSED), ledger-aware delta digest (D3) with full escaping (D9); 18 unit tests; `seed`/`dryrun [--fixture]`/`status` commands | **GATE MET** — 18/18 tests green; live dry-run: 123 campaigns fetched in ~8 s, digest rendered, DB seeded |
| **2 — Telegram** ✅ **DONE (2026-09-01)** | Channel **t.me/dropwatchdog** (id `-1003773674983`, type channel, Heimdall admin) resolved via `getChat`; `notify.py` (Bot API HTML via heimdall `.env` token, no getUpdates conflict); `send`/`test` commands; ledger marking post-send; line-safe truncation (mid-tag cut bug fixed); D9 escaping | **GATE MET** — test message → channel+DM OK; real digest 3740 chars → channel OK; re-run silent (dedupe proven, 20× ENDING_24H ledger) |
| **3 — Site** 🔧 **engine DONE (2026-09-01) — hosting pending Mick** | `site_builder.py` (drops.json + feed.xml + _headers no-store + assets mirror/prune + template copy); single-file SPA `site_template.html` (sections All/<24h/<48h/New/Favs/History, priority toggle, search/game/sort, live 1s countdowns, watch-minutes, hash anchors, hash-diff re-render, 10-min poll, Three.js ambient w/ graceful fallback, textContent-only rendering); 101 images mirrored (0 failures, docs/ = 3.2 MB); local git repo committed (main) | **GATE MET (engine)** — ego-browser E2E: 101 cards, tabs+counts, <24h filter=20, history=22 rows, search=2, countdown ticking, prio toggle, anchors, ZERO console errors; **pending** CF Pages wizard (repo + DNS) |
| **4 — Schedule + hardening** | 30-min Hermes cron (D11), commit-on-change (D7), error alerts, staleness, logging, README | 2 real days of runs verified: phone+site reflect changes; failure drills pass; build quota month projection <500 |
| **5 — Favorites (Steam)** ✅ **DONE (2026-09-01)** | `steam_import.py` (Mick API **DONE** = 1339 games; wifey browser scrape **PENDING** — needs Mick's Steam login + Guard); favorites CLI + site Favorites tab **DONE**; Steam library badge **DONE** (11/102 live); **24/7 personal DM alert DONE** (cron `twitch-drops-personal` `0,30 * * * *`, Delta Force alert verified in DM); in-site selector round-trip = Phase 6 KV | Favorite drop triggers instant DM even off-hours ✓; selector round-trips → Phase 6; badge shows ✓ |
| **6 — Interaction + learning** | CF Pages Function + KV (⭐/👍/remind) → watchdog reads → weights; weekly recap digest; weekly health digest; GH Actions mirror; PWA/ES-EN polish | KV interaction → next sync picks it up; learning loop demoed on historical data |

---

## 12. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Twitch API change / hash rotation | Inline-gql primary (D8); persisted + DOM fallbacks; validation gate |
| Throwaway-account ban / token expiry | Auto-refresh; `auth_error` alert; re-auth ~2 min; no main-account touch |
| CF Pages 500 builds/month cap | Commit-on-change (D7); typical 2–10/day |
| Telegram group privacy limitations | D17: group = public digest, DM = personal; no fake per-user visibility claims |
| False "ended" mass-prune | Schema-validated fetches only; DB never overwritten by empty payloads |
| Broken HTML / XSS from campaign names | Full escaping server+client (D9) |
| Stale site data | no-store headers (D10), cache-bust, "updated X ago" (D13) |
| Sub-hour campaign missed | 30-min fetch cadence (D11) |
| Rate limits on details calls | Details only on change (D12); backoff |
| Repo growth from image history | ~1–5 MB/year accepted; yearly rewrite or R2 escape hatch |
| Mac Mini off / Hermes down | GH Actions mirror (optional); health digest detects gaps |
| Twitch ToS | Attribution, personal-use, throwaway account (D14) |

---

## 13. What's needed from Mick (remaining)

**Phase 0 (next):**
1. Device-flow authorize (~10 s): I generate the code + URL; open it **logged into the throwaway Twitch account** (your own browser is fine), click Authorize. Tokens then auto-refresh forever.
2. Green light to run Phase 0.

**Phase 2:**
3. Create the **Telegram group** (e.g. "Twitch Drops Watchdog"), add the **Heimdall bot** as admin. I'll resolve the group id from gateway logs (no getUpdates polling). Private/invite-only until it's polished; make joinable after.

**Phase 3:**
4. GitHub: either authorize me to create the private repo via `gh`, or create it yourself and add me. 
5. Cloudflare Pages wizard (~15 min of clicks — GitHub↔CF link, project, domain, DNS CNAME at Porkbun). I'll drive you through it.

**Phase 5:**
6. **Steam login in ego-browser** (you have 2FA — have the Steam Guard code ready when I prompt). Needed only for wifey's scrape; your own profile works via API key alone.

**Nothing else pending.** en-US locked (D16); secrets files verified 600 (D15).

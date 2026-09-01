# Twitch Drops Watchdog & Dashboard System Blueprint
**Version:** 1.0.0-PROPOSAL (⚠️ SUPERSEDED by `PLAN.md` v2.0.0 — v2 contains verified recon that corrects this draft's anonymous-GraphQL assumption)  
**Author:** AI Master Architect  
**Target Host:** Mac Mini M4 Pro (`macmini.tailf74921.ts.net`)  
**Domain Target:** `drops.hache.app`  
**Telegram Bot Target:** Heimdall (Hermes Bot Profile `heimdall`)  
**Timezone:** Europe/Madrid / Europe/Amsterdam (CET UTC+1 / CEST UTC+2 auto-switching)  
**Workspace:** `~/Documents/Workspaces/drops/`

---

## 1. Executive Summary

This project establishes an automated, deterministic (zero-LLM-cost) Twitch Drop Campaign watchdog and live web dashboard designed to ensure Mick never misses short-window or high-value drop events.

The system is split into two tightly synchronized components:
1. **Telegram Alerts via Heimdall (Hermes)**: Hourly digest sent during active wake-up hours (08:00 to 00:00 local CET/CEST), highlighting:
   - **✨ Newly Discovered Campaigns** (with game title, active window, and reward item names).
   - **🚨 Ending Soon (<24h Remaining)** watchdog alerts to claim items before expiration.
   - Automatic silent pruning of ended ("Closed") campaigns.
   - Zero spam during sleep hours (01:00 to 07:00).
2. **Web Dashboard (`drops.hache.app`)**: A fast, responsive, modern single-page dashboard:
   - Displays all active and upcoming drop campaigns with game box art, running dates/times, and extracted item reward icons/names.
   - Real-time client-side countdown timers (`⏳ 05h 23m 10s remaining`).
   - Interactive filtering by Game, Status (`All Active`, `Almost Gone <24h`, `Ending Soon <48h`, `Upcoming`), and fast text search.
   - Background 10-minute delta refresh without requiring a manual page reload.
   - Hosted via GitHub Pages pointing to `drops.hache.app` (or direct Mac Mini Caddy hosting).
3. **Personalization & Preference Engine (Roadmap)**:
   - Favorites / Priority games list.
   - Instant targeted one-off Telegram alerts when favorite titles start a drop campaign.

---

## 2. End-to-End System Architecture

```
                                  ┌────────────────────────┐
                                  │   Twitch Drops Source  │
                                  │ (GQL / Web Catalog)    │
                                  └───────────┬────────────┘
                                              │
                      Trigger on Schedule     │ (Fetch & Parse)
                      (Every 1h / 10m delta)  │
                                              ▼
                             ┌────────────────────────────────┐
                             │    twitch_drops_sync.py        │
                             │ (Deterministic Python Engine)  │
                             └───────┬───────────────┬────────┘
                                     │               │
                     Update / Check  │               │ Export Public State
                                     ▼               ▼
                     ┌───────────────────┐    ┌────────────────────┐
                     │     drops.db      │    │  data/drops.json   │
                     │  (SQLite Storage) │    │  (Static Payload)  │
                     └─────────┬─────────┘    └─────────┬──────────┘
                               │                        │
       Wake Hours (08:00-00:00)│                        │ Git Push / Serve
       Diff & Watchdog Check   ▼                        ▼
    ┌──────────────────────────────┐        ┌──────────────────────────────┐
    │     Heimdall Telegram Bot    │        │       drops.hache.app        │
    │  (Rich HTML Formatted DM)    │        │ (SPA Web Dashboard on Pages) │
    └──────────────────────────────┘        └──────────────────────────────┘
```

### Execution Paradigm: Trigger-on-Demand (No Heavy Daemons)
- **Zero background idle CPU burn**: Instead of running a permanent long-lived Node/Python process in an infinite sleep loop, the updater executes as a clean **Hermes Cron job (`--no-agent`)** or lightweight scheduled trigger.
- **Run Time**: Each sync completes in ~1.5 to 3.0 seconds, updates SQLite `drops.db`, exports `drops.json`, evaluates notification triggers, sends Telegram messages via Heimdall if actionable, and exits immediately.

---

## 3. Data Ingestion & Extraction Engine

### 3.1 Data Source Options & Fallback Strategy

| Ingestion Method | Mechanism | Pros | Fallback Triggers |
| :--- | :--- | :--- | :--- |
| **Primary: Direct Twitch GraphQL** | Query `ViewerDropsDashboard` / `DropCampaignDetails` with standard headers | Extremely fast (<500ms), structured JSON, full reward metadata & box arts | If Twitch issues an integrity challenge |
| **Secondary: Authenticated GQL** | Query with optional user OAuth token (`TWITCH_OAUTH_TOKEN` in `.env`) | 100% bypasses integrity challenges, returns active + upcoming campaigns + user claim status | If OAuth token not configured |
| **Tertiary: Headless Extractor** | Minimal headless scraper script (`ego-browser` / Playwright) visiting `twitch.tv/drops/campaigns` | Bypasses all bot detection, dumps exact DOM rendered cards | If network/GQL changes |

### 3.2 Extracted Entity Model

For every campaign, the ingestion engine extracts:
- `id` (UUID): Unique Twitch campaign identifier (e.g., `8f7b3a12-...`).
- `game_id` & `game_name`: e.g., `Overwatch 2`, `Rust`, `World of Warcraft`.
- `name`: Campaign title (e.g., `Season 15 Launch Drops`).
- `box_art_url`: 3:4 high-res cover art (`https://static-cdn.jtvnw.net/ttv-boxart/...`).
- `start_at` & `end_at`: ISO 8601 UTC timestamps.
- `status`: `ACTIVE`, `UPCOMING`, `EXPIRED`.
- `rewards`: Array of reward items:
  - `name`: Item reward title (e.g., `Cyber Dragon Weapon Skin`, `500 In-Game Coins`).
  - `required_minutes`: Watch time required (e.g., `60`, `120`, `240`).
  - `image_url`: Badge / preview asset URL.
- `channels_policy`: All channels vs specific broadcasters.
- `details_url`: Direct Twitch campaign link (`https://www.twitch.tv/drops/campaigns?drop=...`).

### 3.3 Closed & Expired Campaigns Policy
- Campaigns matching the "Closed Drop Campaigns" section or where `end_at < current_time` are:
  1. Marked `status = 'CLOSED'` in `drops.db`.
  2. Purged from active watchdog memory.
  3. Excluded from `drops.json` active feed (or archived in a collapsed history view).
  4. Never alerted as "new" or "ending soon".

---

## 4. Timezone & Scheduling Engine

### 4.1 Timezone Handling (CET / CEST)
- **Timezone**: `Europe/Madrid` / `Europe/Amsterdam` via Python standard library `zoneinfo.ZoneInfo("Europe/Madrid")`.
- **Automatic Daylight Saving Transition**:
  - Winter (CET): UTC + 1:00
  - Summer (CEST): UTC + 2:00
- All stored timestamps in SQLite remain ISO 8601 UTC for mathematical comparisons (`datetime.now(timezone.utc)`).
- All display strings for Telegram and Web are formatted in local CET/CEST (e.g., `Tue, Sep 1, 18:00 CEST`).

### 4.2 Wake-Up Hours Window & Cron Schedule
- **Wake Hours**: 08:00 to 00:00 (Midnight). Runs hourly at minute 0:
  - `08:00`, `09:00`, `10:00`, `11:00`, `12:00`, `13:00`, `14:00`, `15:00`, `16:00`, `17:00`, `18:00`, `19:00`, `20:00`, `21:00`, `22:00`, `23:00`, `00:00`.
- **Sleep Hours (Silent)**: 01:00 to 07:00. The script suppresses outgoing Telegram messages unless a critical priority game drop is detected (configurable).
- **Hermes Cron Registration**:
  ```bash
  hermes cron create "0 8-23,0 * * *" \
    --name twitch-drops-sync \
    --script twitch_drops_sync.py \
    --no-agent \
    --deliver local \
    --workdir ~/Documents/Workspaces/drops
  ```

---

## 5. Telegram Notification Architecture & Formatting

### 5.1 Telegram Bot Integration via Heimdall
- **Bot Profile**: `heimdall` (`~/.hermes/profiles/heimdall/.env`).
- **Target Chat ID**: `2350638` ("Héctor" DM).
- **Delivery Mode**: Direct Telegram Bot API POST with `parse_mode="HTML"`, disabling web page preview to keep messages compact and visually clean.

### 5.2 Notification Deduplication Ledger
To prevent duplicate pings across hourly runs:
1. `first_notified_at`: Set when a campaign is first announced under "New Campaigns".
2. `ending_soon_notified_at`: Set when a campaign enters the ≤24h window and is announced in "Ending Soon".
3. A campaign is only announced **once** in the "New" section, and **once** in the "Ending Soon" section.

### 5.3 Telegram Message Template & Visual Hierarchy

```html
🎮 <b>TWITCH DROPS WATCHDOG</b> ── <i>01 Sep, 18:00 CEST</i>
━━━━━━━━━━━━━━━━━━━━━━━━━━

🚨 <b>ENDING SOON (&lt; 24H REMAINING)</b>
──────────────────────────
⏳ <b>Overwatch 2</b> ── <i>Ends in 6h 15m (Tonight 00:15 CEST)</i>
• <b>Event:</b> Season 15 Kickoff Drops
• <b>Rewards:</b> Cyberpunk Kiriko Skin, 5x Battle Pass Tier Skips
🔗 <a href="https://www.twitch.tv/drops/campaigns">Claim Drop on Twitch</a>

⏳ <b>Rust</b> ── <i>Ends in 18h 30m (Tomorrow 12:30 CEST)</i>
• <b>Event:</b> Creator Series Round 28
• <b>Rewards:</b> Custom Metal Door, Glowing AK-47 Skin
🔗 <a href="https://www.twitch.tv/drops/campaigns">Claim Drop on Twitch</a>

✨ <b>NEWLY DISCOVERED CAMPAIGNS</b>
──────────────────────────
🎁 <b>World of Warcraft</b>
• <b>Event:</b> The War Within Premiere
• <b>Active:</b> 01 Sep 19:00 CEST ➔ 08 Sep 19:00 CEST (7 days)
• <b>Rewards:</b> Ghastly Charger Mount, 100x Trader's Tender
🔗 <a href="https://www.twitch.tv/drops/campaigns">View Campaign Details</a>

━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>Active Watchdog:</b> 14 campaigns tracked | 2 ending soon
🌐 <b>Live Dashboard:</b> <a href="https://drops.hache.app">drops.hache.app</a>
```

### 5.4 Empty State Behavior
- If a run occurs and there are **no new campaigns** and **no newly entering <24h campaigns**, the script runs silently (no spam to Telegram).

---

## 6. Web Dashboard (`drops.hache.app`) Architecture

### 6.1 Frontend Stack & Design Spec
- **Architecture**: Modern, lightweight, zero-bloat Single Page Application (HTML5 + Vanilla ES Modules / Tailwind / Lucide Icons).
- **Aesthetic**: Premium dark gaming UI (matching Chat·hache / modern dashboard aesthetics):
  - Dark slate/zinc background (`#090d16` / `#0f172a`).
  - Subtle glowing accent borders (Purple `#9146FF` Twitch brand accent, Emerald for active, Amber for ending soon, Crimson for <24h).
  - Glassmorphic translucent cards (`backdrop-blur-md bg-white/5`).
  - High-res responsive game cover art with smooth loading states.
- **Client-Side Live Features**:
  - Monotonic 1-second interval countdown timers on every card (`Ends in: 04h 18m 22s`).
  - Background auto-polling: Polls `data/drops.json` every 10 minutes with `fetch(..., { cache: 'no-cache' })`. When the JSON payload hash changes, the UI updates smoothly in-place without page refresh.

### 6.2 Filter & Search Capabilities
- **Quick Status Tabs**:
  - `🔥 All Active` (Default view).
  - `🚨 Almost Gone (<24h)` (Urgent claim filter).
  - `⏰ Ending Soon (<48h)` (Weekend planning filter).
  - `📅 Upcoming` (Future scheduled drops).
  - `⭐ Favorites` (Personalized watchlist).
- **Game Category Dropdown**: Filter by specific game (e.g. `Overwatch 2 (3)`, `Rust (1)`, `Apex Legends (2)`).
- **Instant Search Input**: Real-time debounce filter matching game name, campaign title, and item reward names.
- **Sort Dropdown**:
  - `Expiring Soonest` (Default).
  - `Newly Added`.
  - `Longest Duration`.
  - `Alphabetical (A-Z)`.

### 6.3 Campaign Card Layout Preview
```
┌─────────────────────────────────────────────────────────────┐
│ [ Game Box Art ]  Overwatch 2              [🚨 6H REMAINING]│
│                   Season 15 Launch Drops                    │
│                                                             │
│  📅 01 Sep, 18:00 CEST  ➔  02 Sep, 00:15 CEST                │
│                                                             │
│  🎁 REWARDS:                                                │
│  • [Icon] Cyberpunk Kiriko Skin (Watch 4h)                  │
│  • [Icon] 5x Battle Pass Tier Skips (Watch 2h)              │
│                                                             │
│  [  Watch on Twitch ↗  ]      [ ⭐ Add to Favorites ]       │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. Hosting & Deployment Blueprint (`drops.hache.app`)

### 7.1 Architecture Options for `drops.hache.app`

#### Option A: GitHub Pages + Private Sync Repo (Recommended by Mick)
- **Repository**: `mick/drops` (or private GitHub repo).
- **Custom Domain**: `drops.hache.app` configured in GitHub Pages settings + `CNAME` file containing `drops.hache.app`.
- **DNS Record**: CNAME record `drops.hache.app -> mick.github.io` (or Porkbun DNS).
- **Sync Mechanism**:
  1. Mac Mini runs `twitch_drops_sync.py` hourly.
  2. Updates `drops.db` and generates `data/drops.json`.
  3. If data changed, performs an atomic `git commit -m "chore: update drops delta [skip ci]" && git push origin main`.
  4. GitHub Pages serves the updated static assets & `data/drops.json` immediately.

#### Option B: Mac Mini Local Caddy Server + Tailscale / Cloudflare Tunnel
- **Host**: Mac Mini M4 Pro serving locally via Caddy web server (port 8443 / 443).
- **Custom Domain**: Direct Caddy reverse proxy matching `chat.hache.app` setup.

---

## 8. Database Schema (`drops.db`)

SQLite single-file storage located at `~/Documents/Workspaces/drops/data/drops.db`.

```sql
-- Main campaigns table
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL,
    game_name TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,               -- 'ACTIVE', 'UPCOMING', 'CLOSED'
    start_at TEXT NOT NULL,             -- ISO 8601 UTC
    end_at TEXT NOT NULL,               -- ISO 8601 UTC
    box_art_url TEXT,
    details_url TEXT,
    channels_summary TEXT,              -- 'All channels' or broadcaster list
    raw_json TEXT,                      -- Complete upstream JSON payload
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_closed INTEGER DEFAULT 0
);

-- Reward items per campaign
CREATE TABLE IF NOT EXISTS rewards (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    required_minutes INTEGER DEFAULT 0,
    image_url TEXT,
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

-- Notification delivery ledger (prevents duplicate spam)
CREATE TABLE IF NOT EXISTS notification_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    notification_type TEXT NOT NULL,    -- 'NEW_CAMPAIGN', 'ENDING_SOON_24H', 'PRIORITY_ALERT'
    sent_at TEXT NOT NULL,
    channel TEXT NOT NULL,              -- 'telegram:heimdall'
    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
);

-- User favorite games & priorities
CREATE TABLE IF NOT EXISTS user_favorites (
    game_name TEXT PRIMARY KEY,
    is_favorite INTEGER DEFAULT 1,
    instant_alert INTEGER DEFAULT 1,    -- Send immediate one-off ping on drop launch
    added_at TEXT NOT NULL
);

-- Indexing for high-speed queries
CREATE INDEX IF NOT EXISTS idx_campaigns_status_end ON campaigns(status, end_at);
CREATE INDEX IF NOT EXISTS idx_campaigns_game ON campaigns(game_name);
CREATE INDEX IF NOT EXISTS idx_ledger_lookup ON notification_ledger(campaign_id, notification_type);
```

---

## 9. Personalization & Preference Engine (Roadmap)

### 9.1 Favorites & Priority Watchlist
- Users can toggle favorite status per game on the web dashboard (saved to `localStorage` + synced via API/DB).
- The sync script queries `user_favorites`:
  - When a favorited game launches a new campaign, the Telegram alert receives a **⭐ PRIORITY DROP DETECTED** banner.
  - Option to send an immediate one-off ping regardless of the hourly batch window.

### 9.2 Learning System (Future Iteration)
- Records clicked campaigns on the web dashboard to infer game preferences automatically over time.
- Ranks most-viewed game drops at the top of the feed and in the Telegram digest header.

---

## 10. Step-by-Step Implementation Roadmap

```
Phase 0: Reconnaissance & Architecture (COMPLETE)
  └─ GQL query mapping, timezone engine spec, Telegram HTML design, SQLite schema.

Phase 1: Ingestion & Database Sync Engine
  └─ Build `src/fetcher.py` (Twitch Drops extractor with GQL + OAuth fallback).
  └─ Build `src/storage.py` (SQLite schema, upserts, status diffs, closed campaign purging).
  └─ Build `src/exporter.py` (generates static `web/data/drops.json`).

Phase 2: Telegram Watchdog & Heimdall Delivery
  └─ Build `src/notifier.py` (Telegram HTML builder, CET timezone formatting, deduplication ledger).
  └─ Build main orchestrator `twitch_drops_sync.py`.
  └─ Test dry-run execution and verify message formatting in Heimdall DM.

Phase 3: Web Dashboard Frontend (`drops.hache.app`)
  └─ Build single-file reactive web app `web/index.html` + `web/app.js` + `web/style.css`.
  └─ Implement client-side timers, search, status filters, game category chips.
  └─ Implement 10-minute live delta poller.

Phase 4: Cron Job Scheduling & Domain Deployment
  └─ Register Hermes `--no-agent` cron job for 08:00–00:00 CET hourly runs.
  └─ Configure GitHub Pages / Caddy setup for `drops.hache.app` with CNAME.

Phase 5: Verification & End-to-End Testing
  └─ Smoke test sync script, test Telegram alert delivery, test web UI live timers.
```

---

## 11. Clarifications & Decision Forks for Mick

To tailor the setup exactly to your preference before execution:

1. **Hosting & Git Strategy**:
   - Do you prefer the web dashboard repository to be hosted on **GitHub Pages** (pushing `data/drops.json` from the Mac Mini to the repo), or would you prefer the Mac Mini's local **Caddy server** to serve `drops.hache.app` directly from `~/Documents/Workspaces/drops/web`?
2. **Twitch Account Linking (Optional)**:
   - For public drop campaigns, the system extracts the public directory. Do you also want an optional personal `TWITCH_OAUTH_TOKEN` in `.env` to show your personal drop claim progress (% watched) on the dashboard?
3. **Telegram Heartbeat Mode**:
   - If there are **no new campaigns** and **no campaigns ending within 24h**, should Heimdall remain completely silent (recommended), or send an optional 1-line daily summary status?
4. **Favorite Games Seeding**:
   - Are there specific game titles you already know you want on your priority watchlist (e.g. *Rust, Overwatch 2, World of Warcraft, Escape from Tarkov, Cyberpunk, GTA* etc.) so we can seed them from day one?

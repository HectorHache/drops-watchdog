# Phase 0 — Execution Log & Results (2026-09-01)

Status: **COMPLETE — all gates MET**. Next: Phase 1 (core engine).

## 1. What was validated (with evidence)

### 1.1 Device flow (app `drops-watchdog`, throwaway account `LaPurria` / id 908570355)
- Confidential client type: **accepted** for the device endpoint (code generated, tokens exchanged).
- `oauth2/validate` → OK (scope `user:read:email`); Helix `/users` → OK.
- **BUT:** gql.twitch.tv rejects device-flow app tokens (`401 "The Authorization token is invalid"`) —
  Twitch's gql only accepts tokens minted by the **web client** (`kimne78kx3ncx6brgo4mv6wki5h1ko`).
- Conclusion: the **web auth-token** (implicit token from the logged-in browser session) is the real
  gql credential. The device-flow app/token is kept as a Helix fallback only (no campaigns on Helix).

### 1.2 Auth stack that works (production shape)
| Credential | Source | Lifetime |
|---|---|---|
| `web_auth_token` | `auth-token` cookie from ego-browser session (throwaway logged in at twitch.tv) | weeks–months (browser session); re-capture ~monthly via login |
| `Client-Integrity` token (`v4.local.…`) | **must be minted in the browser** (Kasada proof via `/integrity`); bare-HTTP tokens are REJECTED for `dropCampaigns` | ≥20 min observed; mint per run (quick browser visit) |
| device headers (`X-Device-Id`, `Client-Session-Id`, `Client-Version`, `Device-ID`) | captured from the page's own requests; fixed per session | per browser session |

### 1.3 Inline GraphQL — THE WIN (B2 confirmed)
- `POST gql.twitch.tv/gql` with: `Client-ID: kimne78kx3ncx6brgo4mv6wki5h1ko`,
  `Authorization: OAuth <web_auth_token>`, `Client-Integrity: <browser token>`, device headers,
  and an **inline** query → **122 campaigns**.
- **No persisted-query hashes involved** → hash-rotation risk eliminated (D8).
- **Rewards come inline**: `timeBasedDrops[] { id, name, requiredMinutesWatched }` on every campaign →
  **no N+1 DropCampaignDetails calls** (B7 upgraded: zero extra calls).
- Rich field set (pinned): `id name status startAt endAt game{id name boxArtURL} imageURL self{isAccountConnected} timeBasedDrops{id name requiredMinutesWatched}`.

### 1.4 Data reality (live capture, 2026-09-01 ~17:25 CEST)
- 122 campaigns: **100 ACTIVE + 22 EXPIRED** (EXPIRED = the "Closed Drop Campaigns" section — detectable via status ✓).
- **19 ending <24h**, 8 ending 24–48h — real alert lists (Delta Force ends in 2.1h, Rise Online ×2, LoL Sub Drop, CoD MW4 Beta W2…).
- All 122 have rewards (names + watch minutes); all 122 have images (`imageURL` or `game.boxArtURL`).
- Locale: en-US confirmed (Accept-Language + content).

## 2. Fixtures saved (tests/fixtures/)
- `live_inline_gql_integrity.json` — basic inline gql (122 campaigns)
- `live_inline_gql_rewards_*.json` / `live_rich_20260901_172508.json` — rich inline gql with rewards + images
- Credentials persisted in `.logTw` (`web_auth_token`, `web_integrity_token`, `device_headers`)

## 3. Production fetch pattern (per run, ~30 min cadence)
1. Quick ego-browser visit (any twitch.tv page, logged-in throwaway) → capture fresh `Client-Integrity` token (~5 s; also keeps the session alive + DOM fallback available).
2. Server-side gql inline rich query (auth-token + integrity + device headers) → all campaigns + rewards, one call.
3. On gql failure → fall back to DOM extraction from the same browser visit.
4. If the browser session dies (auth-token invalid) → `auth_error` alert → Mick re-logs-in the throwaway (~2 min).

## 4. Notes / follow-ups for Phase 1
- Measure integrity-token TTL to potentially skip some browser visits (default: mint per run — safe).
- Confirm exact status semantics (EXPIRED vs a possible separate CLOSED flag) during history retention.
- DOM fallback extraction to be implemented + tested in Phase 1 hardening (not needed for the gate).

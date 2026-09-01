# Twitch Drops Watchdog

Hourly Twitch Drop Campaign watchdog + live dashboard.

- **Telegram:** digest → [t.me/dropwatchdog](https://t.me/dropwatchdog) (Heimdall bot) · personal alerts → DM
- **Site:** [drops.hache.app](https://drops.hache.app) (Cloudflare Pages, private repo)
- **Stack:** deterministic Python 3 (stdlib only) · SQLite source of truth · single-file SPA

## Layout
```
src/          pipeline (fetch → db → watchdog → notify → site)
docs/         published site (drops.json, index.html, feed.xml, assets/)
data/         drops.db (gitignored — stays local, rows kept forever)
tests/        unit tests + live fixtures
```

## Commands
```
python3 src/main.py seed        fetch live → upsert drops.db
python3 src/main.py send        full run → Telegram channel (delta-only, dedupe)
python3 src/main.py sync [--dry]  scheduled run (cron): fetch → seed → close ended
                       → digest → build → commit+push docs/ only when changed
python3 src/main.py build       regenerate docs/ from drops.db
python3 src/main.py dryrun [--fixture F]   preview digest (read-only)
python3 src/main.py status      credentials + DB status
python3 -m unittest discover -s tests
```

## Scheduling (Phase 4)
Hermes cron job `twitch-drops-sync` (`b9870369ed3a`), schedule `0,30 7-23,0 * * *`
(wake hours Europe/Madrid), script `~/.hermes/scripts/twitch_drops_sync.sh` →
`python3 src/main.py sync`. Deterministic `docs/` = byte-identical when no campaign
data changed → commit-on-change push keeps CF Pages under the 500 builds/month cap.
Error alerts (fetch/push failure) → DM, throttled 6h via `meta` watermark.

## Secrets (gitignored, chmod 600)
- `.logTw` — Twitch web auth-token + integrity token + device headers (throwaway account)
- `.steamMine` — Steam API key (Phase 5)

## Notes
- Twitch gql is login-gated: auth = browser-minted `Client-Integrity` token (Kasada) + web `auth-token`, refreshed via a quick ego-browser visit per run.
- 2FA session ≈ 30 days → `status` warns; re-login on expiry (~2 min).
- Plan: `260901-drops-plan.md` (canonical) · recon: `docs_research/`

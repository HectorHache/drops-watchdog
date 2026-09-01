// functions/api/interact.js — Phase 6 (improvement #1)
// POST /api/interact  {kind:"favorite"|"like"|"remind", campaignId, value}
//   -> writes an append-only interaction record to Cloudflare KV (binding: DROPS_KV).
// The local watchdog pulls these via GET /api/interactions each sync
// (zero always-on server — KV is the free-tier round-trip bus).
//
// KV layout (sorted by ts so the watchdog can page with `since`):
//   key: interaction:<epochMs>:<kind>:<campaignId>
//   val: {"ts": "<epochMs>", "kind": ..., "campaignId": ..., "value": ...}
export async function onRequestPost(context) {
  const { request, env } = context;
  try {
    const body = await request.json();
    const kind = String(body.kind || "").trim();
    const campaignId = String(body.campaignId || "").trim().slice(0, 64);
    const value = String(body.value ?? "").slice(0, 200);
    if (!["favorite", "like", "remind"].includes(kind)) {
      return json({ ok: false, error: "invalid kind" }, 400);
    }
    if (!campaignId) {
      return json({ ok: false, error: "campaignId required" }, 400);
    }
    const ts = Date.now();
    const key = `interaction:${ts}:${kind}:${campaignId}`;
    await env.DROPS_KV.put(key, JSON.stringify({ ts, kind, campaignId, value }));
    return json({ ok: true, id: key });
  } catch (e) {
    return json({ ok: false, error: String(e && e.message || e) }, 500);
  }
}

// GET /api/interactions?since=<epochMs> — all interactions newer than `since`
// (used by src/kv.py on the Mac; "since" default 0 = everything).
export async function onRequestGet(context) {
  const { request, env } = context;
  try {
    const url = new URL(request.url);
    const since = parseInt(url.searchParams.get("since") || "0", 10) || 0;
    const out = [];
    const prefix = "interaction:";
    // list() pages through the prefix (max 1000 per call; we page until done)
    let cursor;
    do {
      const page = await env.DROPS_KV.list({ prefix, cursor });
      for (const item of page.keys) {
        const ts = parseInt((item.name.match(/^interaction:(\d+):/) || [])[1], 10) || 0;
        if (ts > since) {
          const raw = await env.DROPS_KV.get(item.name);
          if (raw) {
            try { out.push(JSON.parse(raw)); }
            catch { out.push({ ts, name: item.name }); }
          }
        }
      }
      cursor = page.cursor;
    } while (cursor);
    out.sort((a, b) => (a.ts || 0) - (b.ts || 0));
    return json({ ok: true, since, interactions: out });
  } catch (e) {
    return json({ ok: false, error: String(e && e.message || e) }, 500);
  }
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

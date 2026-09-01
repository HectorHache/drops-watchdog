// functions/api/interactions.js — GET /api/interactions?since=<epochMs>
// Returns all KV interactions newer than `since` (default 0 = everything).
// Used by the local watchdog (src/kv.py) to pull site button clicks back
// into drops.db each sync. Zero always-on server (KV round-trip bus).
export async function onRequestGet(context) {
  const { request, env } = context;
  try {
    const url = new URL(request.url);
    const since = parseInt(url.searchParams.get("since") || "0", 10) || 0;
    const out = [];
    const prefix = "interaction:";
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

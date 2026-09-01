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

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

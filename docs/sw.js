/* Twitch Drops Watchdog — PWA-lite service worker (offline-first, cache-first).
 * Caches the app shell + the latest drops.json so the dashboard works offline
 * and loads fast on repeat visits. drops.json is revalidated on every fetch
 * (network-first with cache fallback) — the app already polls it every 10 min.
 */
const CACHE = "drops-watchdog-v1";
const SHELL = [
  "/", "/index.html", "/manifest.json", "/site.webmanifest", "/drops.json",
  "/favicon.ico", "/favicon-16x16.png", "/favicon-32x32.png",
  "/apple-touch-icon.png", "/android-chrome-192x192.png", "/android-chrome-512x512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// drops.json: network-first (fresh data matters); shell: cache-first
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname === "/drops.json" || url.pathname.startsWith("/assets/")) {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then(
      (hit) => hit || fetch(e.request).then((res) => {
        const clone = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, clone));
        return res;
      })
    )
  );
});

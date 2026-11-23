// Caches the app shell so it opens instantly and survives weak signal.
// API calls (/ride, /weather, /demo ...) always go to the network.
const CACHE = "moto-ai-v3";
const SHELL = ["./", "index.html", "css/app.css", "js/app.js", "js/alert.js", "js/beeper.js", "js/garage.js", "js/roads.js", "privacy.html", "terms.html", "css/legal.css", "manifest.webmanifest", "icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  const isShell = e.request.method === "GET" && url.origin === location.origin && url.pathname.startsWith(new URL(self.registration.scope).pathname);
  if (!isShell) return; // network as usual
  // network first (picks up new versions), cache fallback when offline
  e.respondWith(
    fetch(e.request)
      .then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; })
      .catch(() => caches.match(e.request))
  );
});

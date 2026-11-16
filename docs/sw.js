// Uygulamanın internetsizken de açılması için basit önbellek.
// Fiyat verisi (veri.json) her zaman önce internetten alınır, olmazsa son kayıtlı veri gösterilir.
const CACHE = "bilet-takip-v2";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  const veri = url.pathname.endsWith("veri.json");
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        const kopya = r.clone();
        const anahtar = veri ? new Request(url.origin + url.pathname) : e.request;
        caches.open(CACHE).then((c) => c.put(anahtar, kopya));
        return r;
      })
      .catch(() => caches.match(veri ? new Request(url.origin + url.pathname) : e.request))
  );
});

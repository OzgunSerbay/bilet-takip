// Uygulamanın internetsizken de açılması için basit önbellek.
// Fiyat verisi (veri.json) her zaman önce internetten alınır, olmazsa son kayıtlı veri gösterilir.
const CACHE = "bilet-takip-v4";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png", "apple-touch-icon.png"];

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

// ---- Bildirimler (GitHub'daki saatlik kontrolden web push ile gelir) ----
self.addEventListener("push", (e) => {
  let v = {};
  try { v = e.data ? e.data.json() : {}; } catch { v = { metin: e.data ? e.data.text() : "" }; }
  e.waitUntil(
    self.registration.showNotification(v.baslik || "✈️ Bilet Takip", {
      body: v.metin || "",
      icon: "icon-192.png",
      badge: "icon-192.png",
      data: { link: v.link || "./" },
      tag: "bilet-takip",
      renotify: true,
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const link = (e.notification.data && e.notification.data.link) || "./";
  e.waitUntil(self.clients.openWindow(link));
});

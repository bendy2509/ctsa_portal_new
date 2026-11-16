{% load static %}
const CACHE_NAME = 'ctsa-rampe-v1';
const STATIC_ASSETS = [
  "{% static 'css/output.css' %}",
  "{% static 'css/ctsa.css' %}",
  "{% static 'js/ctsa.js' %}",
  "{% static 'img/cap-terminal-logo.png' %}",
  "{% static 'img/icon-192.png' %}",
  "{% static 'img/icon-512.png' %}",
];

const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hors ligne — Portail RAMPE</title>
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, sans-serif; background: #f8fafc; color: #2c3741; display: grid; place-items: center; min-height: 100vh; padding: 1.5rem; }
    .card { max-width: 26rem; background: #fff; border: 1px solid #e2e8f0; border-top: 4px solid #3498db; border-radius: 1rem; padding: 1.75rem; text-align: center; box-shadow: 0 4px 16px rgba(15, 23, 42, 0.06); }
    h1 { font-size: 1.25rem; margin: 0 0 0.5rem; }
    p { font-size: 0.925rem; color: #475569; line-height: 1.5; margin: 0 0 1.25rem; }
    button { background: #217dbb; color: #fff; border: 0; border-radius: 0.5rem; padding: 0.7rem 1.25rem; font-weight: 700; font-size: 0.9rem; cursor: pointer; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Connexion indisponible</h1>
    <p>Vous êtes actuellement hors ligne. Rétablissez votre connexion Internet pour consulter les statuts RAMPE en temps réel.</p>
    <button type="button" onclick="window.location.reload()">Réessayer</button>
  </div>
</body>
</html>`;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Ressources statiques : cache + mise à jour en arrière-plan
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) =>
        cache.match(req).then((cached) => {
          const fetchPromise = fetch(req)
            .then((res) => {
              if (res && res.ok) cache.put(req, res.clone());
              return res;
            })
            .catch(() => cached);
          return cached || fetchPromise;
        })
      )
    );
    return;
  }

  // Pages HTML : réseau prioritaire (statuts et formulaires toujours frais), sinon message hors ligne
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() =>
        new Response(OFFLINE_HTML, {
          status: 503,
          headers: { 'Content-Type': 'text/html; charset=utf-8' },
        })
      )
    );
  }
});

/* PictureHouse service worker (served at /sw.js, scope /).
   Network-first for the app shell so updates show up straight away, with a cache fallback so the console
   still opens offline (it then shows "Server unreachable"). API calls, DLNA and media are never touched. */
'use strict';
const VERSION = 'picturehouse-shell-v1';
const SHELL = [
  '/',
  '/static/app.js',
  '/static/app.css',
  '/manifest.webmanifest',
  '/icons/icon-48.png',
  '/icons/icon-120.png',
  '/icons/icon-152.png',
  '/icons/icon-167.png',
  '/icons/icon-180.png',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/static/vendor/hls-1.7.3.min.js'
];
const SHELL_SET = new Set(SHELL);

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    // A missing file or unusable cache storage must not stop the worker installing; cache what we can.
    try {
      const cache = await caches.open(VERSION);
      await Promise.all(SHELL.map(url => cache.add(new Request(url, { cache: 'no-cache' })).catch(() => {})));
    } catch (err) { /* the shell is cached on the next successful fetch instead */ }
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.filter(k => k.startsWith('picturehouse-') && k !== VERSION).map(k => caches.delete(k)));
    } catch (err) { /* nothing to clean */ }
    await self.clients.claim();
  })());
});

function shellKey(request, url) {
  if (request.mode === 'navigate') return url.pathname === '/' || url.pathname === '/index.html' ? '/' : null;
  return SHELL_SET.has(url.pathname) && !url.search ? url.pathname : null;
}

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Never cache the API, DLNA, the CA certificate or media: let them go to the network untouched.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/dlna/') || url.pathname === '/ca.crt') return;
  if (req.headers.has('range')) return;
  const key = shellKey(req, url);
  if (!key) return;
  event.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (res.ok && res.type === 'basic') {
        const copy = res.clone();
        caches.open(VERSION).then(c => c.put(key, copy)).catch(() => {});
      }
      return res;
    } catch (err) {
      const hit = await caches.match(key, { cacheName: VERSION }).catch(() => null);
      if (hit) return hit;
      throw err;
    }
  })());
});

// Pour Choices service worker.
//
// Split out to its own real file (2026-09-18) -- this used to be registered
// from a blob: URL built inline in index.html (new Blob([...]) +
// URL.createObjectURL()), which silently fails in every real Chromium-based
// browser: Chrome/Samsung Internet on Android refuse to register a service
// worker whose script isn't served over http/https, so
// navigator.serviceWorker.register(blobUrl) always rejected there (caught
// and swallowed by the surrounding .catch(() => {})), meaning the app never
// actually had an active service worker on a real phone. A registered,
// active service worker is one of Chrome/Samsung Internet's own
// requirements for offering "Install app"/"Add to Home Screen" at all, so
// without one the browser just treats the page as an ordinary website --
// this was the real cause behind Samsung Internet showing no install option
// and treating the site like a desktop page.
//
// Deliberately does the bare minimum needed to satisfy that installability
// requirement: takes control of the page as soon as possible and otherwise
// stays out of the way (no caching/offline logic here).
self.addEventListener('install', (e) => self.skipWaiting());
self.addEventListener('activate', (e) => self.clients.claim());

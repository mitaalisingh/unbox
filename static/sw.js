// Lets the phone install Unbox like an app. It stores nothing, so private pages and photos are never kept on the device by us.
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function () { /* straight to the network */ });

import React, { Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

// The signed-in app and the public share page are separate downloads.
const App = lazy(() => import("./App"));
const ShareApp = lazy(() => import("./components/ShareApp").then((m) => ({ default: m.ShareApp })));

// Public links live at /s/<token>; everything else is the signed-in app.
const share = location.pathname.match(/^\/s\/([^/]+)/);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={null}>
      {share ? <ShareApp token={decodeURIComponent(share[1])} /> : <App />}
    </Suspense>
  </React.StrictMode>,
);

// Installable app (PWA): a small service worker caches the app shell for fast start-up.
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
}

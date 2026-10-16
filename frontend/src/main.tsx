import React, { Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { ErrorBoundary } from "./components/ErrorBoundary";

// The signed-in app and the public share page are separate downloads.
const App = lazy(() => import("./App"));
const ShareApp = lazy(() => import("./components/ShareApp").then((m) => ({ default: m.ShareApp })));

// Public links live at /s/<token>; everything else is the signed-in app.
const share = location.pathname.match(/^\/s\/([^/]+)/);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary label="Probyte">
      <Suspense fallback={null}>
        {share ? <ShareApp token={decodeURIComponent(share[1])} /> : <App />}
      </Suspense>
    </ErrorBoundary>
  </React.StrictMode>,
);

// The view filtering, for tests that check it agrees with the server's (tests/ui/test_filter_parity.py).
import("./util").then((u) => { (window as unknown as { __probyteViews: unknown }).__probyteViews = { applyView: u.applyView }; });

// Installable app (PWA): a small service worker caches the app shell for fast start-up.
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
}

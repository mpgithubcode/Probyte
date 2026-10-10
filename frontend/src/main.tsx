import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ShareApp } from "./components/ShareApp";
import "./styles.css";

// Public links live at /s/<token>; everything else is the signed-in app.
const share = location.pathname.match(/^\/s\/([^/]+)/);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {share ? <ShareApp token={decodeURIComponent(share[1])} /> : <App />}
  </React.StrictMode>,
);

// Installable app (PWA): a small service worker caches the app shell for fast start-up.
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
}

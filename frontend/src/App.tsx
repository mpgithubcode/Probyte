import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { DatasetPage } from "./components/DatasetPage";
import { Icon } from "./components/Icon";
import { LoginPage } from "./components/LoginPage";
import type { Tab as SettingsTab } from "./components/SettingsModal";
import { Sidebar } from "./components/Sidebar";
import type { User, Workspace } from "./types";
import { setWeekStart } from "./util";

const ComboModal = lazy(() => import("./components/ComboModal").then((m) => ({ default: m.ComboModal })));
const NewTableModal = lazy(() => import("./components/NewTableModal").then((m) => ({ default: m.NewTableModal })));
const SettingsModal = lazy(() => import("./components/SettingsModal").then((m) => ({ default: m.SettingsModal })));

interface Route { kind: "table" | "combo"; id: string; viewId?: string }

function parseHash(): Route | null {
  const m = location.hash.match(/^#\/(table|combo)\/([^/]+)(?:\/([^/]+))?/);
  return m ? { kind: m[1] as Route["kind"], id: m[2], viewId: m[3] } : null;
}

function go(r: Route | null) {
  location.hash = r ? `/${r.kind}/${r.id}${r.viewId ? `/${r.viewId}` : ""}` : "";
}

export default function App() {
  // undefined = still checking the session, null = logged out
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    api.get<User>("/auth/me").then(setUser).catch(() => setUser(null));
    const onUnauthorized = () => setUser(null);
    window.addEventListener("probyte:unauthorized", onUnauthorized);
    return () => window.removeEventListener("probyte:unauthorized", onUnauthorized);
  }, []);

  if (user === undefined) return <div className="boot">Loading Probyte…</div>;
  setWeekStart(user?.prefs?.week_start);
  // after signing in, load the full profile (with settings such as the first day of the week)
  if (user === null) return <LoginPage onLogin={(u) => { setUser(u); api.get<User>("/auth/me").then(setUser).catch(() => undefined); }} />;
  return <WorkspaceShell key={user.id} user={user} onLogout={() => setUser(null)} onUserChanged={setUser} />;
}

function WorkspaceShell({ user, onLogout, onUserChanged }: { user: User; onLogout: () => void; onUserChanged: (u: User) => void }) {
  const [ws, setWs] = useState<Workspace | null>(null);
  const [route, setRoute] = useState<Route | null>(parseHash);
  const [modal, setModal] = useState<"table" | "combo" | "settings" | null>(null);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("account");
  const openSettings = (tab: SettingsTab = "account") => { setSettingsTab(tab); setModal("settings"); setDrawer(false); };
  const [modalBase, setModalBase] = useState<string | undefined>(undefined);
  const [drawer, setDrawer] = useState(false);
  const openModal = (m: "table" | "combo", baseId?: string) => { setModalBase(baseId ?? ws?.bases[0]?.id); setModal(m); setDrawer(false); };
  const [toast, setToast] = useState<string | null>(null);

  const loadWs = useCallback(() => api.get<Workspace>("/workspace").then(setWs).catch((e) => setToast(e.message)), []);

  useEffect(() => { loadWs(); }, [loadWs]);
  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  // Keep sync timestamps / error dots in the sidebar fresh.
  useEffect(() => { const t = setInterval(loadWs, 30000); return () => clearInterval(t); }, [loadWs]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const logout = async () => {
    await api.post("/auth/logout").catch(() => undefined);
    location.hash = "";
    onLogout();
  };

  // Open the first database when nothing is selected.
  useEffect(() => {
    if (ws && !route && ws.tables[0]) go({ kind: "table", id: ws.tables[0].id });
  }, [ws, route]);

  const onError = useCallback((m: string) => setToast(m), []);

  if (!ws) return <div className="boot">Loading Probyte…</div>;

  return (
    <div className="app">
      <Sidebar ws={ws} user={user} active={route ?? undefined} open={drawer} onClose={() => setDrawer(false)}
        onOpen={(kind, id) => { go({ kind, id }); setDrawer(false); }}
        onNewTable={(b) => openModal("table", b)} onNewCombo={(b) => openModal("combo", b)}
        onSettings={() => openSettings()}
        onLogout={logout} onChanged={loadWs} onError={onError} />

      {route ? (
        <DatasetPage key={`${route.kind}:${route.id}`} kind={route.kind} id={route.id} viewId={route.viewId} tables={ws.tables} me={user}
          hiddenInSidebar={!!(route.kind === "table" ? ws.tables : ws.combos).find((x) => x.id === route.id)?.hidden}
          bases={ws.bases} onMenu={() => setDrawer(true)}
          onNavigate={(viewId) => go({ ...route, viewId })}
          onWorkspaceChanged={(goHome) => { loadWs(); if (goHome) go(null); }}
          onError={onError} />
      ) : (
        <div className="welcome">
          <button className="icon-btn menu-btn welcome-menu" onClick={() => setDrawer(true)} aria-label="Menu"><Icon name="menu" size={20} /></button>
          <h1>Welcome to Probyte</h1>
          <p>Spreadsheet-style databases with CalDAV task &amp; event sync, and combined views across databases.</p>
          <div className="welcome-actions">
            <button className="btn primary" onClick={() => openModal("table")}><Icon name="plus" size={14} /> New table</button>
            <button className="btn" onClick={() => openSettings("accounts")}><Icon name="settings" size={14} /> Connect a CalDAV server</button>
          </div>
        </div>
      )}

      <Suspense fallback={null}>
      {modal === "table" && (
        <NewTableModal accounts={ws.accounts} bases={ws.bases} baseId={modalBase} onClose={() => setModal(null)} onManageAccounts={() => openSettings("accounts")}
          onCreated={(t) => { setModal(null); loadWs(); go({ kind: "table", id: t.id }); }} />
      )}
      {modal === "combo" && (
        <ComboModal tables={ws.tables.filter((t) => t.base_id === modalBase)} baseId={modalBase} onClose={() => setModal(null)}
          onSaved={(id) => { loadWs(); go({ kind: "combo", id }); }} />
      )}
      {modal === "settings" && <SettingsModal user={user} ws={ws} initialTab={settingsTab} onClose={() => setModal(null)} onAccountsChanged={loadWs} onUserChanged={onUserChanged} />}
      </Suspense>

      {toast && <div className="toast" onClick={() => setToast(null)}>{toast}</div>}
    </div>
  );
}

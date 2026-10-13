import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { DatasetPage } from "./components/DatasetPage";
import { Icon } from "./components/Icon";
import { Modal } from "./components/Overlay";
import { LoginPage } from "./components/LoginPage";
import type { Tab as SettingsTab } from "./components/SettingsModal";
import { Sidebar } from "./components/Sidebar";
import type { User, Workspace } from "./types";
import { onToast, type Toast } from "./toast";
import { SCREENS, useExtensions, type ExtensionInfo } from "./extensions";
import { setWeekStart } from "./util";

const ComboModal = lazy(() => import("./components/ComboModal").then((m) => ({ default: m.ComboModal })));
const NewTableModal = lazy(() => import("./components/NewTableModal").then((m) => ({ default: m.NewTableModal })));
const SearchModal = lazy(() => import("./components/SearchModal").then((m) => ({ default: m.SearchModal })));
const TrashModal = lazy(() => import("./components/TrashModal").then((m) => ({ default: m.TrashModal })));
const SettingsModal = lazy(() => import("./components/SettingsModal").then((m) => ({ default: m.SettingsModal })));

interface Route { kind: "table" | "combo"; id: string; viewId?: string; record?: string }

/** An extension's screen: #/x/<id>/<its own path> */
interface ExtRoute { ext: string; path: string }
function parseExt(): ExtRoute | null {
  const m = location.hash.match(/^#\/x\/([^/?]+)\/?(.*)$/);
  return m ? { ext: m[1], path: m[2] } : null;
}

/** #/join/<token>: an invite link to a shared base. */
const parseJoin = () => location.hash.match(/^#\/join\/([A-Za-z0-9_-]+)/)?.[1] ?? null;

function parseHash(): Route | null {
  const m = location.hash.match(/^#\/(table|combo)\/([^/?]+)(?:\/([^/?]+))?(?:\?record=([^&]+))?/);
  return m ? { kind: m[1] as Route["kind"], id: m[2], viewId: m[3], record: m[4] } : null;
}

function go(r: Route | null) {
  location.hash = r ? `/${r.kind}/${r.id}${r.viewId ? `/${r.viewId}` : ""}${r.record ? `?record=${r.record}` : ""}` : "";
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
  const [extRoute, setExtRoute] = useState<ExtRoute | null>(parseExt);
  const [joinToken, setJoinToken] = useState<string | null>(parseJoin);
  const extensions = useExtensions();
  const [modal, setModal] = useState<"table" | "combo" | "settings" | "trash" | "search" | null>(null);
  // Ctrl/⌘ K: search everything
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setModal("search"); setDrawer(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("account");
  const openSettings = (tab: SettingsTab = "account") => { setSettingsTab(tab); setModal("settings"); setDrawer(false); };
  const [modalBase, setModalBase] = useState<string | undefined>(undefined);
  const [drawer, setDrawer] = useState(false);
  const openModal = (m: "table" | "combo", baseId?: string) => { setModalBase(baseId ?? ws?.bases[0]?.id); setModal(m); setDrawer(false); };
  const [toast, setToastState] = useState<Toast | null>(null);
  const setToast = useCallback((m: string | null) => setToastState(m ? { id: Date.now(), message: m } : null), []);
  useEffect(() => onToast(setToastState), []);

  const loadWs = useCallback(() => api.get<Workspace>("/workspace").then(setWs).catch((e) => setToast(e.message)), []);

  useEffect(() => { loadWs(); }, [loadWs]);
  useEffect(() => {
    const onHash = () => { setRoute(parseHash()); setExtRoute(parseExt()); setJoinToken(parseJoin()); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  // Keep sync timestamps / error dots in the sidebar fresh.
  useEffect(() => { const t = setInterval(loadWs, 30000); return () => clearInterval(t); }, [loadWs]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToastState(null), toast.action ? 8000 : 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const logout = async () => {
    await api.post("/auth/logout").catch(() => undefined);
    location.hash = "";
    onLogout();
  };

  // Open the first database when nothing is selected.
  useEffect(() => {
    if (ws && !route && !extRoute && !joinToken && ws.tables[0]) go({ kind: "table", id: ws.tables[0].id });
  }, [ws, route, extRoute, joinToken]);

  const onError = useCallback((m: string) => setToast(m), []);

  if (!ws) return <div className="boot">Loading Probyte…</div>;

  return (
    <div className="app">
      <Sidebar ws={ws} user={user} active={route ?? undefined} open={drawer} onClose={() => setDrawer(false)}
        onOpen={(kind, id) => { go({ kind, id }); setDrawer(false); }}
        onNewTable={(b) => openModal("table", b)} onNewCombo={(b) => openModal("combo", b)}
        onSettings={() => openSettings()}
        onLogout={logout} onChanged={loadWs} onError={onError} onTrash={() => { setModal("trash"); setDrawer(false); }} onSearch={() => { setModal("search"); setDrawer(false); }}
        extensions={extensions} activeExtension={extRoute?.ext} onOpenExtension={(id) => { location.hash = `/x/${id}`; setDrawer(false); }} />

      {extRoute ? (
        <ExtensionPage key={extRoute.ext} route={extRoute} extensions={extensions} onMenu={() => setDrawer(true)} onError={onError} onWorkspaceChanged={loadWs} />
      ) : route ? (
        <DatasetPage key={`${route.kind}:${route.id}`} kind={route.kind} id={route.id} viewId={route.viewId} tables={ws.tables} me={user} openRecord={route.record}
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
        {joinToken && <JoinBase token={joinToken} onClose={() => { location.hash = ""; }}
          onJoined={async (baseId) => {
            const w = await api.get<Workspace>("/workspace");
            setWs(w);
            const first = w.tables.find((t) => t.base_id === baseId);
            location.hash = first ? `/table/${first.id}` : "";
          }} />}
        {modal === "trash" && <TrashModal onClose={() => setModal(null)} onChanged={loadWs} />}
        {modal === "search" && <SearchModal onClose={() => setModal(null)} onOpen={(tableId, recordId) => go({ kind: "table", id: tableId, record: recordId })} />}
      </Suspense>

      {toast && (
        <div className="toast" role="status" onClick={() => setToastState(null)}>
          <span>{toast.message}</span>
          {toast.action && (
            <button className="toast-action" onClick={(e) => { e.stopPropagation(); setToastState(null); Promise.resolve(toast.action!.run()).catch((err) => setToast(err.message)); }}>
              {toast.action.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** An invite link to a base: who shares it and with what role, then join (or open it, if already in it). */
function JoinBase({ token, onClose, onJoined }: { token: string; onClose: () => void; onJoined: (baseId: string) => void }) {
  const [info, setInfo] = useState<{ base_id: string; base: string; owner: string; role: string; follow_views: boolean; already: string | null } | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<typeof info>(`/join/${token}`).then(setInfo).catch((e) => setErr(e.message)); }, [token]);
  const join = async () => {
    setBusy(true);
    try { const r = await api.post<{ base_id: string }>(`/join/${token}`); onJoined(r.base_id); }
    catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  return (
    <Modal title="Join a shared base" width={440} onClose={onClose}
      footer={<><span className="spacer" /><button className="btn" onClick={onClose}>Cancel</button>
        {info && (info.already ? <button className="btn primary" onClick={() => onJoined(info.base_id)}>Open it</button>
          : <button className="btn primary" disabled={busy} onClick={join}>{busy ? "Joining…" : "Join"}</button>)}</>}>
      {err && <div className="error">{err}</div>}
      {!info && !err && <p className="muted">Checking the link…</p>}
      {info && (
        <div className="form">
          <p><b>{info.owner}</b> shares the base <b>“{info.base}”</b> with you as {info.role === "admin" ? "an" : "a"} <b>{info.role}</b>.</p>
          {info.already ? <p className="muted">You're already in it{info.already === "owner" ? " (it's yours)" : ` as ${info.already === "admin" ? "an" : "a"} ${info.already}`}.</p>
            : <p className="muted small">{info.follow_views ? "Its views stay as the base's are, as they change." : "You get your own copies of its views."} You can change that, or leave the base, in its sharing settings.</p>}
        </div>
      )}
    </Modal>
  );
}

function ExtensionPage({ route, extensions, onMenu, onError, onWorkspaceChanged }: {
  route: ExtRoute; extensions: ExtensionInfo[]; onMenu: () => void; onError: (m: string) => void; onWorkspaceChanged: () => void;
}) {
  const Screen = route.ext in SCREENS ? SCREENS[route.ext] : undefined;
  const info = extensions.find((e) => e.id === route.ext);
  if (!Screen || (extensions.length && !info?.enabled)) {
    return (
      <div className="welcome">
        <button className="icon-btn menu-btn welcome-menu" onClick={onMenu} aria-label="Menu"><Icon name="menu" size={20} /></button>
        <h1>{info?.name ?? "Extension"}</h1>
        <p>{Screen ? "This extension is switched off. The server's admin can switch it on in Settings → Extensions." : "There's no such extension."}</p>
      </div>
    );
  }
  return (
    <Suspense fallback={<div className="boot">Loading {info?.name ?? ""}…</div>}>
      <Screen path={route.path} go={(p) => { location.hash = `/x/${route.ext}${p ? `/${p}` : ""}`; }} onMenu={onMenu} onError={onError}
        onWorkspaceChanged={onWorkspaceChanged} />
    </Suspense>
  );
}

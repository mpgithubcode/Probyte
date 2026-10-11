import { useEffect, useState } from "react";
import { api } from "../api";
import type { DavInfo, Prefs, User, Workspace } from "../types";
import { ApiPanel } from "./ApiPanel";
import { AccountsPanel } from "./AccountsModal";
import { SharesPanel } from "./SharesPanel";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

function CopyField({ label, value, secret }: { label: string; value: string; secret?: boolean }) {
  const [shown, setShown] = useState(!secret);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch { /* clipboard needs https; the value is still selectable */ }
  };
  return (
    <div className="copy-field">
      <span className="copy-label">{label}</span>
      <code onClick={() => setShown(true)}>{shown ? value : "••••-••••-••••-••••"}</code>
      {secret && <button className="icon-btn" title={shown ? "Hide" : "Show"} onClick={() => setShown(!shown)}><Icon name="eye" size={14} /></button>}
      <button className="icon-btn" title="Copy" onClick={copy}>{copied ? "✓" : <Icon name="copy" size={14} />}</button>
    </div>
  );
}

export type Tab = "account" | "accounts" | "phone" | "links" | "keys";
const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "account", label: "Account", icon: "user" }, { id: "accounts", label: "Sync accounts", icon: "calendar" },
  { id: "phone", label: "Phone sync", icon: "phone" },
  { id: "links", label: "Public links", icon: "share" }, { id: "keys", label: "API & MCP", icon: "key" },
];

export function SettingsModal({ user, ws, onClose, onAccountsChanged, onUserChanged, initialTab = "account" }: {
  user: User; ws: Workspace; onClose: () => void; onAccountsChanged: () => void; onUserChanged?: (u: User) => void; initialTab?: Tab;
}) {
  const setPref = async (patch: Prefs) => {
    const prefs = await api.patch<Prefs>("/auth/prefs", patch);
    onUserChanged?.({ ...user, prefs });
  };
  const [tab, setTab] = useState<Tab>(initialTab);
  const [dav, setDav] = useState<DavInfo | null>(null);
  const [check, setCheck] = useState<{ ok: boolean; detail: string } | "running" | null>(null);

  const runCheck = async () => {
    setCheck("running");
    try { setCheck(await api.post("/dav/check")); } catch (e) { setCheck({ ok: false, detail: (e as Error).message }); }
  };
  const [confirmReset, setConfirmReset] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [pwStatus, setPwStatus] = useState<{ ok: boolean; msg: string } | null>(null);

  useEffect(() => { api.get<DavInfo>("/dav").then(setDav).catch(() => setDav(null)); }, []);

  const resetDav = async () => {
    setDav(await api.post<DavInfo>("/dav/reset"));
    setConfirmReset(false);
    onAccountsChanged();
  };

  const changePassword = async () => {
    if (pw.next !== pw.confirm) return setPwStatus({ ok: false, msg: "New passwords don't match" });
    try {
      await api.post("/auth/password", { current_password: pw.current, new_password: pw.next });
      setPw({ current: "", next: "", confirm: "" });
      setPwStatus({ ok: true, msg: "Password changed. Other devices were signed out." });
    } catch (e) {
      setPwStatus({ ok: false, msg: (e as Error).message });
    }
  };

  // Probyte passes calendar traffic through to its CalDAV server, so this page's own address works too.
  const serverUrl = dav?.public_url ?? `${location.origin}/`;

  return (
    <Modal title="Settings" width={760} onClose={onClose}>
      <div className="settings">
        <nav className="settings-tabs">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
              <Icon name={t.icon} size={15} /> <span>{t.label}</span>
            </button>
          ))}
        </nav>
        <div className="form settings-body">
        {tab === "links" && <SharesPanel ws={ws} />}
        {tab === "keys" && <ApiPanel ws={ws} />}
        {tab === "accounts" && (
          <section className="settings-section">
            <p className="muted">Calendar and contacts servers your tables sync with. Each synced table uses one calendar or address book of one account.</p>
            <AccountsPanel accounts={ws.accounts} onChanged={onAccountsChanged} />
          </section>
        )}
        {tab === "phone" && !dav?.enabled && <p className="muted">The built-in CalDAV server isn't configured on this Probyte instance.</p>}
        {tab === "phone" && dav?.enabled && (
          <section className="settings-section">
            <h3><Icon name="phone" size={16} /> Sync with your phone</h3>
            <p className="muted">
              Probyte includes a CalDAV and CardDAV server. Add this account on your iPhone, Mac or Android (DAVx⁵) and your
              Probyte calendars show up in Calendar and Reminders, and your contact tables in Contacts.
            </p>
            {dav.provisioned ? (
              <>
                <CopyField label="Server" value={serverUrl} />
                <CopyField label="Username" value={dav.username!} />
                <CopyField label="Password" value={dav.password!} secret />
                <p className="hint">Use exactly this username and device password on your phone. Your Probyte password won't work there.</p>
                <div className="row">
                  <button className="btn sm" disabled={check === "running"} onClick={runCheck}>
                    <Icon name="sync" size={14} className={check === "running" ? "spin" : ""} /> Test connection
                  </button>
                  <span className="muted small">Checks the server address the same way a phone does.</span>
                </div>
                {check && check !== "running" && <div className={check.ok ? "success" : "error"}>{check.detail}</div>}
                
                <details className="howto">
                  <summary>iPhone / iPad setup</summary>
                  <ol>
                    <li>Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → <b>Add CalDAV Account</b>.</li>
                    <li>Enter the server, username and password above, then tap Next.</li>
                    <li>Turn on Calendars and Reminders. Events appear in Calendar and tasks appear in Reminders.</li>
                  </ol>
                  <p className="hint">In Probyte, create databases on the “Probyte CalDAV” account so they sync to your phone.</p>
                </details>
                <details className="howto">
                  <summary>iPhone / iPad contacts</summary>
                  <ol>
                    <li>Settings → Apps → Contacts → Contacts Accounts → Add Account → Other → <b>Add CardDAV Account</b>.</li>
                    <li>Enter the same server, username and password, then tap Next.</li>
                    <li>Contacts from your Probyte <b>Contacts</b> tables appear in the Contacts app, and changes on the phone sync back.</li>
                  </ol>
                  <p className="hint">Create the table with New table → Contacts on the “Probyte CalDAV” account. Android: DAVx⁵ syncs contacts with the same login.</p>
                </details>
                {confirmReset ? (
                  <div className="row">
                    <span className="muted small">Devices using the old password will stop syncing.</span>
                    <span className="spacer" />
                    <button className="btn sm danger" onClick={resetDav}>Generate new password</button>
                    <button className="btn sm" onClick={() => setConfirmReset(false)}>Cancel</button>
                  </div>
                ) : (
                  <button className="btn sm" onClick={() => setConfirmReset(true)}><Icon name="sync" size={14} /> New device password</button>
                )}
              </>
            ) : (
              <button className="btn" onClick={resetDav}>Enable phone sync login</button>
            )}
          </section>
        )}

        {tab === "account" && (
        <section className="settings-section">
          <div className="row"><span className="avatar">{user.username[0].toUpperCase()}</span> <b>{user.username}</b></div>
          <h3>Calendar</h3>
          <label className="stack">First day of the week
            <select className="input" value={user.prefs?.week_start ?? 1} onChange={(e) => setPref({ week_start: Number(e.target.value) })}>
              {["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].map((d, i) => <option key={d} value={i}>{d}</option>)}
            </select>
          </label>
          <h3>Change password</h3>
          <input className="input" type="password" placeholder="Current password" autoComplete="current-password"
            value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
          <input className="input" type="password" placeholder="New password (8+ characters)" autoComplete="new-password"
            value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
          <input className="input" type="password" placeholder="Confirm new password" autoComplete="new-password"
            value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} />
          {pwStatus && <div className={pwStatus.ok ? "success" : "error"}>{pwStatus.msg}</div>}
          <div className="row"><span className="spacer" /><button className="btn primary" disabled={!pw.current || !pw.next} onClick={changePassword}>Change password</button></div>
        </section>
        )}
        </div>
      </div>
    </Modal>
  );
}

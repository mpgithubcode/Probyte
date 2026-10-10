import { useState } from "react";
import { api } from "../api";
import type { Account } from "../types";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

const EMPTY = { name: "", url: "", carddav_url: "", username: "", password: "" };

export function AccountsModal({ accounts, onClose, onChanged }: { accounts: Account[]; onClose: () => void; onChanged: () => void }) {
  return (
    <Modal title="CalDAV & CardDAV accounts" onClose={onClose}>
      <AccountsPanel accounts={accounts} onChanged={onChanged} />
    </Modal>
  );
}

/** The sync accounts list and editor (Settings → Sync accounts). */
export function AccountsPanel({ accounts, onChanged }: { accounts: Account[]; onChanged: () => void }) {
  const [editing, setEditing] = useState<Account | "new" | null>(accounts.length ? null : "new");
  const [form, setForm] = useState(EMPTY);
  const [status, setStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const startEdit = (a: Account | "new") => {
    setEditing(a);
    setStatus(null);
    setForm(a === "new" ? EMPTY : { name: a.name, url: a.url, carddav_url: a.carddav_url ?? "", username: a.username ?? "", password: "" });
  };

  const test = async () => {
    setBusy(true);
    setStatus(null);
    try {
      const r = await api.post<{ calendars: unknown[]; addressbooks: unknown[] | null; calendar_error: string | null }>(
        "/accounts/test", { ...form, id: editing !== "new" ? editing?.id : undefined });
      const parts = [r.calendar_error ? "no calendars" : `${r.calendars.length} calendar(s)`,
        r.addressbooks ? `${r.addressbooks.length} address book(s)` : "no contacts (CardDAV) found"];
      setStatus({ ok: true, msg: `Connected — ${parts.join(", ")}` });
    } catch (e) {
      setStatus({ ok: false, msg: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!form.name.trim() || !form.url.trim()) return setStatus({ ok: false, msg: "Name and URL are required" });
    try {
      if (editing === "new") await api.post("/accounts", form);
      else if (editing) await api.patch(`/accounts/${editing.id}`, form);
      onChanged();
      setEditing(null);
    } catch (e) {
      setStatus({ ok: false, msg: (e as Error).message });
    }
  };

  const remove = async (a: Account) => {
    await api.del(`/accounts/${a.id}`);
    onChanged();
  };

  const set = (k: keyof typeof EMPTY) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="accounts-panel">
      {!editing && (
        <div className="form">
          {accounts.map((a) => (
            <div key={a.id} className="account-row">
              <div>
                <b>{a.name}</b> {a.is_builtin && <span className="badge">built-in · syncs to your phone</span>}
                <div className="muted small">{a.is_builtin ? `${a.username} — see Settings for phone setup` : `${a.username ? `${a.username} @ ` : ""}${a.url}`}</div>
              </div>
              <span className="spacer" />
              {!a.is_builtin && <>
                <button className="icon-btn" title="Edit" onClick={() => startEdit(a)}><Icon name="edit" size={14} /></button>
                <button className="icon-btn" title="Remove" onClick={() => remove(a)}><Icon name="trash" size={14} /></button>
              </>}
            </div>
          ))}
          {!accounts.length && <p className="muted">No accounts yet.</p>}
          <button className="btn" onClick={() => startEdit("new")}><Icon name="plus" size={14} /> Add account</button>
          <p className="hint">Connect as many accounts as you like (iCloud, Nextcloud, Fastmail…). Each table syncs with one calendar or address book of one account.</p>
        </div>
      )}
      {editing && (
        <div className="form">
          <label className="stack">Name<input className="input" autoFocus value={form.name} placeholder="Nextcloud" onChange={set("name")} /></label>
          <label className="stack">Server URL
            <input className="input" value={form.url} placeholder="https://cloud.example.com/remote.php/dav/" onChange={set("url")} />
            <span className="hint">Nextcloud: …/remote.php/dav/ · Radicale: http://host:5232/ · iCloud: https://caldav.icloud.com/ (app password)</span>
          </label>
          <label className="stack">Contacts (CardDAV) URL (optional)
            <input className="input" value={form.carddav_url} placeholder="Same as the server URL" onChange={set("carddav_url")} />
            <span className="hint">Only if contacts live at a different address. iCloud: https://contacts.icloud.com/</span>
          </label>
          <label className="stack">Username<input className="input" value={form.username} autoComplete="off" onChange={set("username")} /></label>
          <label className="stack">Password
            <input className="input" type="password" value={form.password} autoComplete="new-password"
              placeholder={editing !== "new" && editing.has_password ? "(unchanged)" : ""} onChange={set("password")} />
          </label>
          {status && <div className={status.ok ? "success" : "error"}>{status.msg}</div>}
          <div className="row">
            <button className="btn" disabled={busy} onClick={test}>{busy ? "Testing…" : "Test connection"}</button>
            <span className="spacer" />
            <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn primary" onClick={save}>Save</button>
          </div>
        </div>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { api } from "../api";
import type { ApiKey, Workspace } from "../types";
import { timeAgo } from "../util";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

export function ApiKeysPanel({ ws }: { ws: Workspace }) {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [editing, setEditing] = useState<ApiKey | "new" | null>(null);
  const [created, setCreated] = useState<ApiKey | null>(null);
  const load = () => api.get<ApiKey[]>("/keys").then(setKeys);
  useEffect(() => { load(); }, []);

  const baseNames = (k: ApiKey) => k.base_ids === null ? "All bases" : k.base_ids.map((id) => ws.bases.find((b) => b.id === id)?.name ?? "?").join(", ");

  return (
    <div className="settings-section">
      <div className="row">
        <p className="muted" style={{ flex: 1 }}>Keys for scripts, automation tools and AI assistants. Apps you connect with
          sign-in (OAuth) show up here too; revoke one to disconnect it.</p>
        <button className="btn primary sm" onClick={() => setEditing("new")}><Icon name="plus" size={14} /> New API key</button>
      </div>
      {keys?.length === 0 && <div className="empty-card">No API keys yet.</div>}
      {keys?.map((k) => {
        const expired = k.expires_at && new Date(k.expires_at) < new Date();
        return (
          <div key={k.id} className={`list-card ${expired ? "off" : ""}`}>
            <div className="list-card-head">
              <Icon name={k.source === "oauth" ? "link" : "key"} size={18} />
              <div className="list-card-title">
                <b>{k.name}</b>
                <span className="muted small"><code>{k.prefix}…</code> · {baseNames(k)} · last used {timeAgo(k.last_used_at)}</span>
              </div>
              <span className="spacer" />
              {k.source === "oauth" && <span className="badge">connected app</span>}
              <span className={`badge ${k.scope === "write" ? "warn" : ""}`}>{k.scope === "write" ? "read & write" : "read only"}</span>
              {expired ? <span className="badge danger">expired</span> : k.expires_at && <span className="badge">until {new Date(k.expires_at).toLocaleDateString()}</span>}
              <button className="icon-btn" title="Edit" onClick={() => setEditing(k)}><Icon name="edit" size={14} /></button>
            </div>
          </div>
        );
      })}
      {editing && (
        <KeyEditor ws={ws} apiKey={editing === "new" ? null : editing} onClose={() => setEditing(null)}
          onSaved={(k) => { load(); setEditing(null); if (k.key) setCreated(k); }} onDeleted={() => { load(); setEditing(null); }} />
      )}
      {created && <NewKeyModal apiKey={created} onClose={() => setCreated(null)} />}
    </div>
  );
}

function KeyEditor({ ws, apiKey, onClose, onSaved, onDeleted }: {
  ws: Workspace; apiKey: ApiKey | null; onClose: () => void; onSaved: (k: ApiKey) => void; onDeleted: () => void;
}) {
  const [name, setName] = useState(apiKey?.name ?? "");
  const [scope, setScope] = useState<"read" | "write">(apiKey?.scope ?? "read");
  const [allBases, setAllBases] = useState(apiKey ? apiKey.base_ids === null : true);
  const [bases, setBases] = useState<string[]>(apiKey?.base_ids ?? []);
  const [expires, setExpires] = useState(apiKey?.expires_at ? apiKey.expires_at.slice(0, 10) : "");
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);

  const save = async () => {
    if (!allBases && !bases.length) return setError("Choose at least one base, or allow all bases");
    const body = { name: name.trim() || "API key", scope, base_ids: allBases ? null : bases,
                   expires_at: expires ? new Date(`${expires}T23:59:59`).toISOString() : null };
    try { onSaved(apiKey ? await api.patch<ApiKey>(`/keys/${apiKey.id}`, body) : await api.post<ApiKey>("/keys", body)); }
    catch (e) { setError((e as Error).message); }
  };

  return (
    <Modal title={apiKey ? "Edit API key" : "New API key"} width={480} onClose={onClose}
      footer={<>
        {apiKey && (confirm
          ? <><button className="btn danger" onClick={async () => { await api.del(`/keys/${apiKey.id}`); onDeleted(); }}>Revoke key</button>
              <button className="btn" onClick={() => setConfirm(false)}>Cancel</button></>
          : <button className="btn danger-ghost" onClick={() => setConfirm(true)}><Icon name="trash" size={14} /> Revoke</button>)}
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={save}>{apiKey ? "Save" : "Create key"}</button>
      </>}>
      <div className="form">
        <label className="stack">Name<input className="input" autoFocus value={name} placeholder="Home Assistant" onChange={(e) => setName(e.target.value)} /></label>
        <div className="stack">Access
          <div className="segmented">
            <button className={scope === "read" ? "active" : ""} onClick={() => setScope("read")}>Read only</button>
            <button className={scope === "write" ? "active" : ""} onClick={() => setScope("write")}>Read &amp; write</button>
          </div>
        </div>
        <div className="stack">Bases
          <label className="inline-check"><input type="checkbox" checked={allBases} onChange={(e) => setAllBases(e.target.checked)} /> All bases (including ones created later)</label>
          {!allBases && ws.bases.map((b) => (
            <label key={b.id} className="inline-check">
              <input type="checkbox" checked={bases.includes(b.id)} onChange={(e) => setBases(e.target.checked ? [...bases, b.id] : bases.filter((x) => x !== b.id))} />
              <span className="swatch" style={{ background: b.color }} /> {b.name}
            </label>
          ))}
        </div>
        <label className="stack">Expires<input className="input" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
          <span className="hint">{expires ? "The key stops working after this day." : "Never"}</span></label>
        <p className="hint">Keys can read and change data only. They can't change your password, CalDAV accounts, public links or other keys.</p>
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}

function NewKeyModal({ apiKey, onClose }: { apiKey: ApiKey; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Modal title="Your new API key" width={520} onClose={onClose}
      footer={<><span className="spacer" /><button className="btn primary" onClick={onClose}>Done</button></>}>
      <div className="form">
        <div className="notice">Copy this key now. For your security it won't be shown again.</div>
        <div className="copy-field">
          <code className="big-key">{apiKey.key}</code>
          <button className="btn sm" onClick={async () => { try { await navigator.clipboard.writeText(apiKey.key!); setCopied(true); } catch { /* select manually */ } }}>
            {copied ? "Copied ✓" : <><Icon name="copy" size={14} /> Copy</>}
          </button>
        </div>
      </div>
    </Modal>
  );
}

import { useEffect, useState } from "react";
import { api } from "../api";
import type { Share, ShareItem, SharePerms, Workspace } from "../types";
import { PALETTE } from "../util";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";
import { VIEW_TYPES } from "./Toolbar";

interface BaseItem { kind: "table" | "combo"; id: string; name: string; views: { id: string; name: string; type: string }[] }

export const shareUrl = (s: Share) => `${location.origin}/s/${s.token}`;
export const embedCode = (s: Share) =>
  `<iframe src="${shareUrl(s)}?embed=1" style="width:100%;height:640px;border:0;border-radius:8px" loading="lazy"></iframe>`;

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

function CopyRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  const [done, setDone] = useState(false);
  return (
    <div className="copy-field">
      <span className="copy-label">{label}</span>
      <code className={mono ? "" : "wrap"}>{value}</code>
      <button className="icon-btn" title="Copy" onClick={async () => { if (await copy(value)) { setDone(true); setTimeout(() => setDone(false), 1200); } }}>
        {done ? "✓" : <Icon name="copy" size={14} />}
      </button>
    </div>
  );
}

export function SharesPanel({ ws }: { ws: Workspace }) {
  const [shares, setShares] = useState<Share[] | null>(null);
  const [editing, setEditing] = useState<Share | "new" | null>(null);
  const [error, setError] = useState("");
  const load = () => api.get<Share[]>("/shares").then(setShares).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const toggle = async (s: Share) => {
    await api.patch(`/shares/${s.id}`, { ...s, enabled: !s.enabled, password: null });
    load();
  };

  return (
    <div className="settings-section">
      <div className="row">
        <p className="muted" style={{ flex: 1 }}>Share chosen tables and views as a web page, an embed, or an app people can install on their phone.</p>
        <button className="btn primary sm" onClick={() => setEditing("new")}><Icon name="plus" size={14} /> New public link</button>
      </div>
      {error && <div className="error">{error}</div>}
      {shares === null && <p className="muted">Loading…</p>}
      {shares?.length === 0 && <div className="empty-card">No public links yet.</div>}
      {shares?.map((s) => {
        const base = ws.bases.find((b) => b.id === s.base_id);
        const expired = s.expires_at && new Date(s.expires_at) < new Date();
        return (
          <div key={s.id} className={`list-card ${s.enabled && !expired ? "" : "off"}`}>
            <div className="list-card-head">
              <span className="base-dot" style={{ background: s.color }}>{s.name[0]?.toUpperCase()}</span>
              <div className="list-card-title">
                <b>{s.name}</b>
                <span className="muted small">
                  {base?.name ?? "?"} · {s.items.length} table{s.items.length === 1 ? "" : "s"}
                  {s.permissions.add || s.permissions.edit || s.permissions.delete
                    ? ` · can ${[s.permissions.add && "add", s.permissions.edit && "edit", s.permissions.delete && "delete"].filter(Boolean).join(", ")}`
                    : " · view only"}
                </span>
              </div>
              <span className="spacer" />
              {s.has_password && <span className="badge" title="Password protected"><Icon name="lock" size={11} /> password</span>}
              {expired ? <span className="badge danger">expired</span> : s.expires_at && <span className="badge">until {new Date(s.expires_at).toLocaleDateString()}</span>}
              <label className="switch" title={s.enabled ? "Link is on" : "Link is off"}>
                <input type="checkbox" checked={s.enabled} onChange={() => toggle(s)} /><span />
              </label>
            </div>
            <CopyRow label="Link" value={shareUrl(s)} />
            <div className="row wrap">
              <a className="btn sm" href={shareUrl(s)} target="_blank" rel="noreferrer"><Icon name="globe" size={14} /> Open</a>
              <button className="btn sm" onClick={() => setEditing(s)}><Icon name="edit" size={14} /> Edit</button>
            </div>
          </div>
        );
      })}
      {editing && (
        <ShareEditor ws={ws} share={editing === "new" ? null : editing} onClose={() => setEditing(null)}
          onSaved={(s) => { load(); setEditing(s); }} onDeleted={() => { load(); setEditing(null); }} />
      )}
    </div>
  );
}

function ShareEditor({ ws, share, onClose, onSaved, onDeleted }: {
  ws: Workspace; share: Share | null; onClose: () => void; onSaved: (s: Share) => void; onDeleted: () => void;
}) {
  const [name, setName] = useState(share?.name ?? "");
  const [baseId, setBaseId] = useState(share?.base_id ?? ws.bases[0]?.id ?? "");
  const [items, setItems] = useState<ShareItem[]>(share?.items ?? []);
  const [perms, setPerms] = useState<SharePerms>(share?.permissions ?? { add: false, edit: false, delete: false });
  const [password, setPassword] = useState("");
  const [removePassword, setRemovePassword] = useState(false);
  const [expires, setExpires] = useState(share?.expires_at ? share.expires_at.slice(0, 10) : "");
  const [enabled, setEnabled] = useState(share?.enabled ?? true);
  const [color, setColor] = useState(share?.color ?? PALETTE[0]);
  const [available, setAvailable] = useState<BaseItem[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"delete" | "regen" | null>(null);

  useEffect(() => {
    setAvailable(null);
    if (baseId) api.get<BaseItem[]>(`/bases/${baseId}/views`).then(setAvailable).catch((e) => setError(e.message));
  }, [baseId]);

  const itemOf = (it: BaseItem) => items.find((x) => x.kind === it.kind && x.id === it.id);
  const toggleItem = (it: BaseItem) => setItems((cur) => itemOf(it) ? cur.filter((x) => !(x.kind === it.kind && x.id === it.id)) : [...cur, { kind: it.kind, id: it.id, views: null }]);
  const toggleView = (it: BaseItem, viewId: string) => setItems((cur) => cur.map((x) => {
    if (x.kind !== it.kind || x.id !== it.id) return x;
    const current = x.views ?? it.views.map((v) => v.id);
    const next = current.includes(viewId) ? current.filter((v) => v !== viewId) : [...current, viewId];
    return { ...x, views: next.length === it.views.length ? null : next };
  }));

  const save = async () => {
    if (!name.trim()) return setError("Give the link a name");
    if (!items.length) return setError("Choose at least one table to share");
    setBusy(true);
    setError("");
    const body = {
      name: name.trim(), base_id: baseId, items, permissions: perms, enabled, color,
      expires_at: expires ? new Date(`${expires}T23:59:59`).toISOString() : null,
      password: removePassword ? "" : password || null,  // null keeps the current password
    };
    try {
      const s = share ? await api.patch<Share>(`/shares/${share.id}`, body) : await api.post<Share>("/shares", body);
      setPassword("");
      setRemovePassword(false);
      onSaved(s);
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  const regenerate = async () => { if (share) onSaved(await api.post<Share>(`/shares/${share.id}/regenerate`)); setConfirm(null); };
  const remove = async () => { if (share) { await api.del(`/shares/${share.id}`); onDeleted(); } };

  return (
    <Modal title={share ? "Edit public link" : "New public link"} width={640} onClose={onClose}
      footer={<>
        {share && (confirm === "delete"
          ? <><button className="btn danger" onClick={remove}>Delete link</button><button className="btn" onClick={() => setConfirm(null)}>Cancel</button></>
          : <button className="btn danger-ghost" onClick={() => setConfirm("delete")}><Icon name="trash" size={14} /> Delete</button>)}
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Close</button>
        <button className="btn primary" disabled={busy} onClick={save}>{busy ? "Saving…" : share ? "Save" : "Create link"}</button>
      </>}>
      <div className="form">
        {share && (
          <div className="share-out">
            <CopyRow label="Link" value={shareUrl(share)} />
            <CopyRow label="Embed" value={embedCode(share)} mono />
            <details className="howto">
              <summary>Install it as an app on a phone</summary>
              <ol>
                <li><b>iPhone / iPad:</b> open the link in Safari → Share → <b>Add to Home Screen</b>.</li>
                <li><b>Android:</b> open the link in Chrome → ⋮ menu → <b>Install app</b> (or Add to Home screen).</li>
              </ol>
              <p className="hint">It installs with this link's name and color, and opens full-screen like an app.</p>
            </details>
            <div className="row">
              {confirm === "regen" ? (
                <>
                  <span className="muted small">The current URL will stop working.</span>
                  <button className="btn sm danger" onClick={regenerate}>Generate new URL</button>
                  <button className="btn sm" onClick={() => setConfirm(null)}>Cancel</button>
                </>
              ) : <button className="btn sm" onClick={() => setConfirm("regen")}><Icon name="sync" size={14} /> New URL</button>}
            </div>
          </div>
        )}

        <div className="row-2">
          <label className="stack">Name<input className="input" autoFocus={!share} value={name} placeholder="Team calendar" onChange={(e) => setName(e.target.value)} /></label>
          <label className="stack">Base
            <select className="input" value={baseId} onChange={(e) => { setBaseId(e.target.value); setItems([]); }}>
              {ws.bases.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
        </div>

        <div className="stack">What visitors can see
          {available === null && <span className="muted">Loading…</span>}
          {available?.length === 0 && <span className="muted">This base has no tables yet.</span>}
          <div className="share-items">
            {available?.map((it) => {
              const sel = itemOf(it);
              const chosen = sel ? (sel.views ?? it.views.map((v) => v.id)) : [];
              return (
                <div key={it.kind + it.id} className={`share-item ${sel ? "on" : ""}`}>
                  <label className="inline-check">
                    <input type="checkbox" checked={!!sel} onChange={() => toggleItem(it)} />
                    <Icon name={it.kind === "combo" ? "layers" : "table"} size={14} /> <b>{it.name}</b>
                  </label>
                  {sel && (
                    <div className="share-views">
                      {it.views.map((v) => (
                        <label key={v.id} className="chip-check">
                          <input type="checkbox" checked={chosen.includes(v.id)} onChange={() => toggleView(it, v.id)} />
                          <Icon name={VIEW_TYPES.find((t) => t.type === v.type)?.icon ?? "grid"} size={12} /> {v.name}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <span className="hint">Visitors see only the fields shown in the chosen views, and only records that match each view's filters.</span>
        </div>

        <div className="stack">Visitors can
          <div className="perm-row">
            <label className="inline-check"><input type="checkbox" checked disabled /> View</label>
            <label className="inline-check"><input type="checkbox" checked={perms.add} onChange={(e) => setPerms({ ...perms, add: e.target.checked })} /> Add records</label>
            <label className="inline-check"><input type="checkbox" checked={perms.edit} onChange={(e) => setPerms({ ...perms, edit: e.target.checked })} /> Edit records</label>
            <label className="inline-check"><input type="checkbox" checked={perms.delete} onChange={(e) => setPerms({ ...perms, delete: e.target.checked })} /> Delete records</label>
          </div>
        </div>

        <div className="row-2">
          <label className="stack">Password
            <input className="input" type="password" autoComplete="new-password" value={password} disabled={removePassword}
              placeholder={share?.has_password ? "(unchanged)" : "No password"} onChange={(e) => setPassword(e.target.value)} />
            {share?.has_password && (
              <label className="inline-check small"><input type="checkbox" checked={removePassword} onChange={(e) => setRemovePassword(e.target.checked)} /> Remove password</label>
            )}
          </label>
          <label className="stack">Expires
            <input className="input" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
            <span className="hint">{expires ? "The link stops working after this day." : "Never"}</span>
          </label>
        </div>
        <div className="row-2">
          <label className="stack">App color<input type="color" className="color-input" value={color} onChange={(e) => setColor(e.target.value)} /></label>
          <label className="inline-check" style={{ alignSelf: "end", paddingBottom: 8 }}>
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Link is on
          </label>
        </div>
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}

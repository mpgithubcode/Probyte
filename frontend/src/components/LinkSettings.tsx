import { useState } from "react";
import { api } from "../api";
import type { RemoteInfo } from "../types";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

/** Where a linked table comes from: re-enter its password, or point it at a different link. */
export function LinkSettings({ tableId, link, onClose, onSaved }: { tableId: string; link: RemoteInfo; onClose: () => void; onSaved: () => void }) {
  const [url, setUrl] = useState(link.url);
  const [password, setPassword] = useState("");
  const [needsPw, setNeedsPw] = useState(!!link.error?.includes("password"));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const p = link.permissions;

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/tables/${tableId}/relink`, { url: url.trim() !== link.url ? url.trim() : undefined, password: password || undefined });
      onSaved();
    } catch (e) {
      const msg = (e as Error).message;
      if (msg === "password_required") { setNeedsPw(true); setError("This link needs its password"); } else setError(msg);
    }
    setBusy(false);
  };

  return (
    <Modal title="Link settings" width={520} onClose={onClose}
      footer={<><span className="spacer" /><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy} onClick={save}>{busy ? "Checking…" : "Save"}</button></>}>
      <div className="form">
        <div className="link-preview">
          <div className="row"><Icon name="link" size={16} /> <b>{link.name ?? "Linked table"}</b>
            <span className="muted small">{link.local ? "on this server" : new URL(link.origin).host}</span></div>
          <span className="muted small">
            The link's owner allows: view{p.add ? ", add" : ""}{p.edit ? ", edit" : ""}{p.delete ? ", delete" : ""}.
            {" "}Only the fields and records shared by the link are shown, and they update live.
          </span>
          {link.error && <div className="error small">{link.error}</div>}
        </div>
        <label className="stack">Public link
          <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} />
          <span className="hint">Paste a new link here if the owner gave you a different one.</span>
        </label>
        {(needsPw || link.has_password) && (
          <label className="stack">Password
            <input className="input" type="password" value={password} placeholder={link.has_password ? "(unchanged)" : ""}
              onChange={(e) => setPassword(e.target.value)} />
          </label>
        )}
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}

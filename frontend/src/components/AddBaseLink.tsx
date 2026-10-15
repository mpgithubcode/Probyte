import { useState } from "react";
import { api } from "../api";
import { Modal } from "./Overlay";

/** What a pasted base link is: an invite to join a base on this server (#/join/<token>), or a public link to a whole
 * base (…/s/<token>, on this server or another), added as a base of linked tables. */
export function readBaseLink(raw: string): { kind: "join"; token: string; here: boolean } | { kind: "public" } | null {
  const url = raw.trim();
  if (!url) return null;
  const join = url.match(/#\/join\/([A-Za-z0-9_-]+)/);
  if (join) {
    let here = true;
    try { here = new URL(url, location.href).origin === location.origin; } catch { /* a bare #/join/… : this server */ }
    return { kind: "join", token: join[1], here };
  }
  return /^https?:\/\//i.test(url) ? { kind: "public" } : null;
}

/** "Add a base from a link": paste a link someone shared to a base. An invite joins it (as the role the link gives);
 * a public link to a whole base makes a base of linked tables that stays in step with it. */
export function AddBaseLink({ onClose, onAdded }: { onClose: () => void; onAdded: (firstTable?: string) => void }) {
  const [url, setUrl] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [followViews, setFollowViews] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const link = readBaseLink(url);
  const add = async () => {
    setErr("");
    if (!link) return setErr("Paste the link to the base (it starts with https://)");
    if (link.kind === "join") {
      if (!link.here) return setErr("This invite is for a base on another Probyte server: open it there (with your account on that server). To have the base here, ask for a public link to the whole base.");
      onClose();
      location.hash = `/join/${link.token}`;   // who shares it and with what role, then Join
      return;
    }
    setBusy(true);
    try {
      const r = await api.post<{ id: string; tables: { id: string }[] }>("/bases/from-link",
        { url: url.trim(), password: password || undefined, name: name.trim() || undefined, follow_views: followViews });
      onAdded(r.tables[0]?.id);
      onClose();
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  return (
    <Modal title="Add a base from a link" width={520} onClose={onClose}
      footer={<><span className="spacer" /><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !url.trim()} onClick={add}>{busy ? "Adding…" : link?.kind === "join" ? "Continue" : "Add base"}</button></>}>
      <div className="form">
        <label className="stack">Link to the base
          <input className="input" autoFocus placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()} />
          <span className="hint">
            {link?.kind === "join" ? "An invite to a shared base: you'll see who shares it and as what, then join it."
              : link?.kind === "public" ? "A public link to a whole base (on this server or another Probyte server): its tables are added as linked tables, kept in step with it, and tables shared later are added too."
              : "An invite to a shared base, or a public link to a whole base."}
          </span>
        </label>
        {link?.kind === "public" && (<>
          <label className="stack">Password <span className="muted small">(if the link has one)</span>
            <input className="input" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label className="stack">Name <span className="muted small">(optional: else the shared base's)</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="inline-check"><input type="checkbox" checked={followViews} onChange={(e) => setFollowViews(e.target.checked)} />
            Keep the views as the owner sets them (untick to make your own changes to them)</label>
        </>)}
        {err && <div className="error">{err}</div>}
      </div>
    </Modal>
  );
}

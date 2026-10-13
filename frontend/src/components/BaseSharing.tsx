import { useEffect, useState } from "react";
import { api } from "../api";
import type { Base, Role } from "../types";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

/** Sharing a base with other users of this server (backend/app/data/access.py). Its owner and admins add people and
 * set their roles; someone it's shared with chooses whether their views follow the base's, or leaves it. */

interface Member { id: string; username: string; role: Role; follow_views?: boolean }
interface Members { owner: Member; members: Member[]; you: Role }

const ROLE_HELP: Record<Exclude<Role, "owner">, string> = {
  viewer: "Sees everything in the base",
  editor: "Also adds, changes and deletes records, and changes views",
  admin: "Also changes fields and tables, and who the base is shared with",
};

export function BaseSharing({ base, me, onClose, onChanged }: { base: Base; me: string; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<Members | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState<Exclude<Role, "owner">>("editor");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.get<Members>(`/bases/${base.id}/members`).then(setData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [base.id]);
  const manage = data?.you === "owner" || data?.you === "admin";
  const run = async (p: Promise<unknown>) => {
    setBusy(true); setErr("");
    try { await p; await load(); onChanged(); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  const add = () => name.trim() && run(api.post(`/bases/${base.id}/members`, { username: name.trim(), role }).then(() => setName("")));
  const mine = data?.members.find((m) => m.username === me);

  return (
    <Modal title={`Share “${base.name}”`} width={560} onClose={onClose}>
      <div className="form base-sharing">
        <p className="muted small">People you share a base with see it in their sidebar as you do: its tables, views and combined
          databases, also on their phone (its calendars and contacts) and through the API.</p>
        {manage && (
          <div className="row share-add">
            <input className="input" placeholder="Username on this server" value={name} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") add(); }} />
            <select className="input" value={role} onChange={(e) => setRole(e.target.value as Exclude<Role, "owner">)}>
              <option value="viewer">Viewer</option><option value="editor">Editor</option><option value="admin">Admin</option>
            </select>
            <button className="btn primary" disabled={busy || !name.trim()} onClick={add}>Share</button>
          </div>
        )}
        {manage && <p className="hint">{ROLE_HELP[role]}. Only the owner can delete the base.</p>}
        {err && <div className="error">{err}</div>}
        {data && (
          <table className="members-table">
            <tbody>
              <tr><td><b>{data.owner.username}</b>{data.owner.username === me ? " (you)" : ""}</td><td className="muted">Owner</td><td /></tr>
              {data.members.map((m) => (
                <tr key={m.id}>
                  <td>{m.username}{m.username === me ? " (you)" : ""}</td>
                  <td>{manage ? (
                    <select className="input sm" value={m.role} disabled={busy}
                      onChange={(e) => run(api.patch(`/bases/${base.id}/members/${m.id}`, { role: e.target.value }))}>
                      <option value="viewer">Viewer</option><option value="editor">Editor</option><option value="admin">Admin</option>
                    </select>) : <span className="muted">{m.role[0].toUpperCase() + m.role.slice(1)}</span>}</td>
                  <td>{(manage || m.username === me) && (
                    <button className="icon-btn" title={m.username === me ? "Leave this base" : "Stop sharing with them"} disabled={busy}
                      onClick={() => run(api.del(`/bases/${base.id}/members/${m.id}`)).then(() => { if (m.username === me) onClose(); })}>
                      <Icon name="x" size={14} /></button>)}</td>
                </tr>
              ))}
              {!data.members.length && <tr><td colSpan={3} className="muted small">Not shared with anyone yet.</td></tr>}
            </tbody>
          </table>
        )}
        {mine && (
          <div className="share-mine">
            <h4>Your views of this base</h4>
            <label className="inline-check">
              <input type="radio" checked={mine.follow_views !== false} disabled={busy}
                onChange={() => run(api.patch(`/bases/${base.id}/me`, { follow_views: true }))} />
              <span><b>Stay updated</b>: the views are {data!.owner.username}'s, as they change them.
                {mine.role === "viewer" ? " Your own filters and sorts last until a view is changed for everyone." : " Your changes to a view are everyone's."}</span>
            </label>
            <label className="inline-check">
              <input type="radio" checked={mine.follow_views === false} disabled={busy}
                onChange={() => run(api.patch(`/bases/${base.id}/me`, { follow_views: false }))} />
              <span><b>Keep my own copies</b>: the views as they are now become yours to change; later changes to the base's views don't
                touch them.</span>
            </label>
            <p className="hint">Either way, views you add yourself are yours alone, and you can hide tables from your own sidebar.</p>
          </div>
        )}
      </div>
    </Modal>
  );
}

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { View } from "../types";
import { timeAgo } from "../util";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

interface Feed { id: string; name: string; view_id: string | null; url: string; webcal: string; last_fetched_at: string | null }

/** Calendar subscription links for a table: read-only calendars other apps subscribe to. */
export function FeedsModal({ kind, id, name, views, onClose }: { kind: "table" | "combo"; id: string; name: string; views: View[]; onClose: () => void }) {
  const [feeds, setFeeds] = useState<Feed[] | null>(null);
  const [viewId, setViewId] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const q = kind === "table" ? `table_id=${id}` : `combo_id=${id}`;
  const load = useCallback(() => api.get<{ feeds: Feed[] }>(`/feeds?${q}`).then((r) => setFeeds(r.feeds)).catch((e) => setError(e.message)), [q]);
  useEffect(() => { load(); }, [load]);
  const act = async (p: Promise<unknown>) => { setError(""); try { await p; } catch (e) { setError((e as Error).message); } load(); };
  const create = () => {
    const v = views.find((x) => x.id === viewId);
    act(api.post("/feeds", { [kind === "table" ? "table_id" : "combo_id"]: id, view_id: viewId || null, name: v ? `${name} – ${v.name}` : name }));
  };
  const copy = async (text: string, key: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(key); setTimeout(() => setCopied(null), 1500); } catch { /* select it by hand */ }
  };
  return (
    <Modal title="Subscribe in a calendar app" width={600} onClose={onClose}>
      <div className="form">
        <p className="muted small">
          A secret link that Apple Calendar, Google Calendar, Outlook and others can subscribe to: the records with a date,
          read-only, refreshed by the calendar app every hour or so. Anyone with the link can see them, so share it with care.
        </p>
        <div className="row wrap">
          <select className="input" value={viewId} onChange={(e) => setViewId(e.target.value)} style={{ flex: 1, minWidth: 200 }}>
            <option value="">All records</option>
            {views.map((v) => <option key={v.id} value={v.id}>Only the view “{v.name}” (its filters)</option>)}
          </select>
          <button className="btn primary" onClick={create}><Icon name="plus" size={14} /> Create link</button>
        </div>
        {feeds && !feeds.length && <p className="muted small">No links yet.</p>}
        {feeds?.map((f) => (
          <div key={f.id} className="feed-row">
            <div className="row">
              <b style={{ flex: 1 }}>{f.name}</b>
              <span className="muted small">{f.last_fetched_at ? `last fetched ${timeAgo(f.last_fetched_at)}` : "not used yet"}</span>
            </div>
            <div className="copy-field">
              <code className="feed-url">{f.url}</code>
              <button className="btn sm" onClick={() => copy(f.url, f.id)}>{copied === f.id ? "Copied ✓" : <><Icon name="copy" size={14} /> Copy</>}</button>
            </div>
            <div className="row wrap">
              <a className="btn sm" href={f.webcal}><Icon name="calendar" size={14} /> Open in calendar app</a>
              <a className="btn sm" target="_blank" rel="noreferrer"
                href={`https://calendar.google.com/calendar/r?cid=${encodeURIComponent(f.webcal)}`}>Add to Google Calendar</a>
              <span className="spacer" />
              <button className="btn sm" title="A new address: the old one stops working" onClick={() => act(api.post(`/feeds/${f.id}/regenerate`))}>New address</button>
              <button className="btn sm danger-ghost" onClick={() => act(api.del(`/feeds/${f.id}`))}><Icon name="trash" size={14} /> Delete</button>
            </div>
          </div>
        ))}
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}

import { useContext, useState } from "react";
import { api } from "../api";
import type { Field } from "../types";
import { displayText, timeAgo } from "../util";
import { RefsContext } from "./Cells";
import { Icon } from "./Icon";

interface Entry { at: string; actor: string; action: string; changes: { field_id: string; field: string; old: unknown; new: unknown }[] }

const VERB: Record<string, string> = { created: "created this", deleted: "deleted this", restored: "restored this from the trash" };

/** "Who changed what" for one record, at the bottom of the record panel (loaded when opened). */
export function RecordHistory({ recordId, fields }: { recordId: string; fields: Field[] }) {
  const { refs } = useContext(RefsContext);
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [error, setError] = useState("");
  const toggle = () => {
    setOpen((o) => !o);
    if (!open) api.get<{ entries: Entry[] }>(`/records/${recordId}/history`).then((r) => setEntries(r.entries)).catch((e) => setError(e.message));
  };
  const show = (fid: string, v: unknown) => {
    const f = fields.find((x) => x.id === fid);
    const text = f ? displayText(f, v, refs) : v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);
    return text ? <span className="hist-value">{text.length > 80 ? text.slice(0, 80) + "…" : text}</span> : <span className="muted">empty</span>;
  };
  return (
    <div className="record-history">
      <button className="ghost-btn" onClick={toggle} aria-expanded={open}>
        <span className={`chev ${open ? "open" : ""}`}><Icon name="right" size={12} /></span> History
      </button>
      {open && (
        <div className="hist-list">
          {error && <div className="error">{error}</div>}
          {!entries && !error && <p className="muted small">Loading…</p>}
          {entries && !entries.length && <p className="muted small">No changes recorded yet.</p>}
          {entries?.map((e, i) => (
            <div key={i} className="hist-entry">
              <div className="hist-head"><b>{e.actor}</b> <span className="muted">{VERB[e.action] ?? "changed"}</span>
                <span className="muted small" title={new Date(e.at).toLocaleString()}> · {timeAgo(e.at)}</span></div>
              {e.changes.map((c) => (
                <div key={c.field_id} className="hist-change small">
                  <span className="hist-field">{c.field}</span> {show(c.field_id, c.old)} <span className="muted">→</span> {show(c.field_id, c.new)}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

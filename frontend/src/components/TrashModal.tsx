import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { timeAgo } from "../util";
import { Icon, tableIcon } from "./Icon";
import { Modal } from "./Overlay";

interface TrashTable { id: string; name: string; kind: string; base_name: string; trashed_at: string; records: number; trash_delete_on_server: boolean }
interface TrashRecord { id: string; table_id: string; table_name: string; title: unknown; deleted_at: string }

/** Tables and records deleted in the last 30 days: put them back, or delete them for good. */
export function TrashModal({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<{ tables: TrashTable[]; records: TrashRecord[] } | null>(null);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);  // id of a table about to be deleted for good
  const load = useCallback(() => api.get("/trash").then(setData).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const run = async (p: Promise<unknown>) => {
    setError("");
    try { await p; onChanged(); } catch (e) { setError((e as Error).message); }
    setConfirm(null);
    load();
  };
  const title = (r: TrashRecord) => (typeof r.title === "string" && r.title) || "Untitled";

  return (
    <Modal title="Trash" width={620} onClose={onClose}>
      <div className="form">
        <p className="muted small">Deleted tables and records stay here for 30 days, then they're deleted for good.</p>
        {!data && !error && <p className="muted">Loading…</p>}
        {data && (
          <>
            <h3>Tables</h3>
            {!data.tables.length && <p className="muted small">No deleted tables.</p>}
            <div className="trash-list">
              {data.tables.map((t) => (
                <div key={t.id} className="trash-row">
                  <Icon name={tableIcon(t.kind)} size={15} />
                  <span className="trash-name"><b>{t.name}</b>
                    <span className="muted small"> · {t.base_name} · {t.records} record{t.records === 1 ? "" : "s"} · deleted {timeAgo(t.trashed_at)}
                      {t.trash_delete_on_server ? " · its calendar goes too" : ""}</span>
                  </span>
                  <button className="btn sm" onClick={() => run(api.post(`/trash/tables/${t.id}/restore`))}>Restore</button>
                  {confirm === t.id
                    ? <button className="btn sm danger" onClick={() => run(api.del(`/trash/tables/${t.id}`))}>Delete for good</button>
                    : <button className="icon-btn" title="Delete for good" onClick={() => setConfirm(t.id)}><Icon name="trash" size={14} /></button>}
                </div>
              ))}
            </div>
            <h3>Records</h3>
            {!data.records.length && <p className="muted small">No deleted records.</p>}
            {data.records.length > 0 && (
              <div className="row">
                <span className="muted small" style={{ flex: 1 }}>{data.records.length} record{data.records.length === 1 ? "" : "s"}</span>
                <button className="btn sm" onClick={() => run(api.post("/trash/records/restore", { ids: data.records.map((r) => r.id) }))}>Restore all</button>
                <button className="btn sm danger-ghost" onClick={() => run(api.post("/trash/records/delete", { ids: data.records.map((r) => r.id) }))}>Empty</button>
              </div>
            )}
            <div className="trash-list">
              {data.records.map((r) => (
                <div key={r.id} className="trash-row">
                  <span className="trash-name">{title(r)}<span className="muted small"> · {r.table_name} · {timeAgo(r.deleted_at)}</span></span>
                  <button className="btn sm" onClick={() => run(api.post("/trash/records/restore", { ids: [r.id] }))}>Restore</button>
                  <button className="icon-btn" title="Delete for good" onClick={() => run(api.post("/trash/records/delete", { ids: [r.id] }))}>
                    <Icon name="x" size={14} />
                  </button>
                </div>
              ))}
            </div>
          </>
        )}
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}

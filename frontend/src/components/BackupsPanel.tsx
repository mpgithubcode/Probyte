import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { timeAgo } from "../util";
import { Icon } from "./Icon";

interface Backup { name: string; created_at: string; total: number; seconds: number; counts: { users: number; tables: number; records: number } }
interface Info { enabled: boolean; interval_hours: number; keep: number; dir: string; backups: Backup[] }

const mb = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(1)} MB`);

/** Settings → Backups (admin only): when the last one ran, the list, "Back up now" and downloads. */
export function BackupsPanel() {
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState("");
  const [waiting, setWaiting] = useState<string | null>(null);  // newest backup when "Back up now" was pressed
  const load = useCallback(() => api.get<Info>("/admin/backups").then(setInfo).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {  // after "Back up now": look again until the new one shows up
    if (waiting === null) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [waiting, load]);
  useEffect(() => { if (waiting !== null && info?.backups[0] && info.backups[0].name !== waiting) setWaiting(null); }, [info, waiting]);

  const backUpNow = async () => {
    setError("");
    try {
      await api.post("/admin/backups");
      setWaiting(info?.backups[0]?.name ?? "");
    } catch (e) { setError((e as Error).message); }
  };

  if (!info) return <p className="muted">{error || "Loading…"}</p>;
  const last = info.backups[0];
  return (
    <section className="settings-section">
      {!info.enabled ? (
        <div className="notice">Backups are off: the worker has no backup folder ({info.dir}). The standard docker-compose.yml mounts one.</div>
      ) : (
        <>
          <div className="row wrap">
            <div style={{ flex: 1, minWidth: 220 }}>
              <b>{last ? `Last backup ${timeAgo(last.created_at)}` : "No backups yet"}</b>
              <div className="muted small">
                {info.interval_hours > 0 ? `Automatic every ${info.interval_hours} h` : "Automatic backups are off (BACKUP_INTERVAL=0)"}
                {" · "}keeps the newest {info.keep}
              </div>
            </div>
            <button className="btn primary sm" disabled={waiting !== null} onClick={backUpNow}>
              <Icon name="sync" size={14} className={waiting !== null ? "spin" : ""} /> {waiting !== null ? "Backing up…" : "Back up now"}
            </button>
          </div>
          {last && info.interval_hours > 0 && Date.now() - Date.parse(last.created_at) > info.interval_hours * 1.5 * 3600e3 && (
            <div className="notice warn">The last backup is older than it should be ({timeAgo(last.created_at)}). Check that the worker is
              running and has room on its disk, or press Back up now to see what happens.</div>
          )}
          <BackupCopy />
          <p className="muted small">
            Each backup holds the database, the calendars and contacts, and uploaded files. They're stored on this server
            ({info.dir}); download one now and then, or point the backup folder at another disk, to survive losing the server.
            The key that protects stored account passwords isn't in backups: keep it with your .env.
          </p>
          <div className="backup-list">
            {info.backups.map((b) => (
              <div key={b.name} className="backup-row">
                <span className="backup-name">{new Date(b.created_at).toLocaleString()}</span>
                <span className="muted small">{mb(b.total)} · {b.counts.records.toLocaleString()} records</span>
                <a className="btn sm" href={`/api/admin/backups/${encodeURIComponent(b.name)}/download`} download>
                  <Icon name="download" size={14} /> Download
                </a>
              </div>
            ))}
          </div>
          <p className="muted small">To restore one, see “Backups” in the README (the app is stopped while it's put back).</p>
        </>
      )}
      {error && <div className="error">{error}</div>}
    </section>
  );
}

interface CopyInfo {
  settings: { storage_id: string; prefix: string; keep: number } | null;
  status: { name: string; at: string; ok: boolean; error?: string; seconds?: number; copies?: number } | null;
  storages: { id: string; name: string; kind: string }[];
}

/** A copy of each backup off this server's disk: to one of your storages (an S3 bucket, a WebDAV folder). */
function BackupCopy() {
  const [c, setC] = useState<CopyInfo | null>(null);
  const [draft, setDraft] = useState<{ storage_id: string; prefix: string; keep: number }>({ storage_id: "", prefix: "probyte-backups", keep: 14 });
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => api.get<CopyInfo>("/admin/backups/copy").then((x) => {
    setC(x);
    if (x.settings) setDraft({ storage_id: x.settings.storage_id, prefix: x.settings.prefix.replace(/\/$/, ""), keep: x.settings.keep });
  }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (!c) return null;
  const save = async () => {
    setErr(""); setMsg("");
    try {
      await api.put("/admin/backups/copy", { ...draft, storage_id: draft.storage_id || null });
      setMsg(draft.storage_id ? "Saved: each new backup is copied there." : "Saved: backups stay on this server only.");
      load();
    } catch (e) { setErr((e as Error).message); }
  };
  const copyNow = async () => {
    setErr(""); setMsg("");
    try {
      const r = await api.post<{ name: string }>("/admin/backups/copy");
      setMsg(`Copying ${r.name}…`);
      setTimeout(load, 4000);
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="backup-copy">
      <b>Off this server</b>
      <p className="muted small">A copy of each backup on another disk survives losing this one. Choose one of your storages (the S3 buckets
        and WebDAV folders set up for files tables): each backup goes into its own folder there, and the newest are kept.</p>
      {!c.storages.length ? <p className="muted small">You have no storages yet: set one up with New table → Files.</p> : (
        <div className="backup-copy-form">
          <label className="stack">Copy to
            <select className="input" value={draft.storage_id} onChange={(e) => setDraft({ ...draft, storage_id: e.target.value })}>
              <option value="">Nowhere (only on this server)</option>
              {c.storages.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.kind === "s3" ? "S3" : "WebDAV"})</option>)}
            </select>
          </label>
          <label className="stack">In the folder
            <input className="input" value={draft.prefix} onChange={(e) => setDraft({ ...draft, prefix: e.target.value })} />
          </label>
          <label className="stack">Keep the newest
            <input className="input" type="number" min={1} max={365} value={draft.keep} onChange={(e) => setDraft({ ...draft, keep: Number(e.target.value) || 1 })} />
          </label>
          <div className="row">
            <button className="btn sm primary" onClick={save}>Save</button>
            {c.settings && <button className="btn sm" onClick={copyNow}>Copy the latest now</button>}
          </div>
        </div>
      )}
      {c.status && (
        <div className={`small ${c.status.ok ? "muted" : "error"}`}>
          {c.status.ok ? `Last copy: ${new Date(c.status.at).toLocaleString()} (${c.status.copies ?? 1} kept there)`
            : `The last copy failed (${new Date(c.status.at).toLocaleString()}): ${c.status.error}`}
        </div>
      )}
      {msg && <div className="success small">{msg}</div>}
      {err && <div className="error small">{err}</div>}
    </div>
  );
}

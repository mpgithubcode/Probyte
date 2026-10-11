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

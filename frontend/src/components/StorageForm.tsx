import { useEffect, useState } from "react";
import { api } from "../api";
import type { Storage } from "../types";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

/** What's typed into the connection form (the secret only when entered or changed). */
export interface StorageDraft {
  name: string; kind: "s3" | "webdav"; endpoint: string; region: string; bucket: string; username: string; secret: string; path_style: boolean;
}

export const emptyStorage = (): StorageDraft =>
  ({ name: "", kind: "s3", endpoint: "", region: "", bucket: "", username: "", secret: "", path_style: true });

export const draftOf = (s: Storage): StorageDraft => ({ ...s, secret: "" });

export const storageBody = (d: StorageDraft) => ({ ...d, secret: d.secret || undefined });

/** S3 (AWS, MinIO, Cloudflare R2, Backblaze B2, Wasabi…) or WebDAV (Nextcloud, ownCloud, a NAS…) connection details. */
export function StorageForm({ value, onChange, saved }: { value: StorageDraft; onChange: (d: StorageDraft) => void; saved?: boolean }) {
  const set = (p: Partial<StorageDraft>) => onChange({ ...value, ...p });
  const s3 = value.kind === "s3";
  return (
    <div className="form storage-form">
      <div className="segmented">
        <button type="button" className={s3 ? "active" : ""} onClick={() => set({ kind: "s3" })}>S3 bucket</button>
        <button type="button" className={!s3 ? "active" : ""} onClick={() => set({ kind: "webdav" })}>WebDAV folder</button>
      </div>
      {s3 ? (
        <>
          <label className="stack">Endpoint
            <input className="input" value={value.endpoint} placeholder="https://minio.example.com — leave empty for Amazon S3"
              onChange={(e) => set({ endpoint: e.target.value })} />
            <span className="hint">MinIO, Cloudflare R2 (https://&lt;account&gt;.r2.cloudflarestorage.com), Backblaze B2, Wasabi and other S3-compatible services.</span>
          </label>
          <div className="row-2">
            <label className="stack">Bucket<input className="input" value={value.bucket} onChange={(e) => set({ bucket: e.target.value })} /></label>
            <label className="stack">Region<input className="input" value={value.region} placeholder="us-east-1" onChange={(e) => set({ region: e.target.value })} /></label>
          </div>
          <div className="row-2">
            <label className="stack">Access key<input className="input" value={value.username} autoComplete="off" onChange={(e) => set({ username: e.target.value })} /></label>
            <label className="stack">Secret key<input className="input" type="password" value={value.secret} autoComplete="new-password"
              placeholder={saved ? "(unchanged)" : ""} onChange={(e) => set({ secret: e.target.value })} /></label>
          </div>
          <label className="inline-check"><input type="checkbox" checked={value.path_style} onChange={(e) => set({ path_style: e.target.checked })} />
            Path-style addresses (needed by MinIO and most self-hosted servers)</label>
        </>
      ) : (
        <>
          <label className="stack">Folder address
            <input className="input" value={value.endpoint} placeholder="https://cloud.example.com/remote.php/dav/files/me/"
              onChange={(e) => set({ endpoint: e.target.value })} />
            <span className="hint">Nextcloud / ownCloud: Files → Settings → WebDAV shows it. Use an app password.</span>
          </label>
          <div className="row-2">
            <label className="stack">Username<input className="input" value={value.username} autoComplete="off" onChange={(e) => set({ username: e.target.value })} /></label>
            <label className="stack">Password<input className="input" type="password" value={value.secret} autoComplete="new-password"
              placeholder={saved ? "(unchanged)" : ""} onChange={(e) => set({ secret: e.target.value })} /></label>
          </div>
        </>
      )}
      <label className="stack">Connection name<input className="input" value={value.name} placeholder={s3 ? value.bucket || "My bucket" : "My NAS"}
        onChange={(e) => set({ name: e.target.value })} /></label>
    </div>
  );
}

/** Settings: saved storage connections. */
export function StoragesPanel() {
  const [list, setList] = useState<Storage[] | null>(null);
  const [edit, setEdit] = useState<Storage | "new" | null>(null);
  const [error, setError] = useState("");
  const load = () => api.get<Storage[]>("/storages").then(setList).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const remove = async (s: Storage) => {
    try { await api.del(`/storages/${s.id}`); load(); } catch (e) { setError((e as Error).message); }
  };
  return (
    <div className="storages">
      <p className="muted small">S3 buckets and WebDAV folders used by files tables. Passwords and secret keys are stored encrypted.</p>
      {list?.map((s) => (
        <div key={s.id} className="account-row">
          <Icon name="folder" size={16} />
          <div className="stack-tight">
            <b>{s.name}</b>
            <span className="muted small">{s.kind === "s3" ? `S3 · ${s.bucket}${s.endpoint ? ` · ${s.endpoint}` : ""}` : `WebDAV · ${s.endpoint}`}
              {s.tables ? ` · ${s.tables} table${s.tables === 1 ? "" : "s"}` : ""}</span>
          </div>
          <span className="spacer" />
          <button className="btn sm" onClick={() => setEdit(s)}>Edit</button>
          <button className="icon-btn" title={s.tables ? "In use by a table" : "Delete"} disabled={!!s.tables} onClick={() => remove(s)}><Icon name="trash" size={14} /></button>
        </div>
      ))}
      {list && !list.length && <p className="muted small">None yet: they're added when you create a files table.</p>}
      <button className="btn sm" onClick={() => setEdit("new")}><Icon name="plus" size={14} /> Add connection</button>
      {error && <div className="error">{error}</div>}
      {edit && <StorageEditor storage={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}

function StorageEditor({ storage, onClose, onSaved }: { storage: Storage | null; onClose: () => void; onSaved: () => void }) {
  const [d, setD] = useState<StorageDraft>(storage ? draftOf(storage) : emptyStorage());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true); setError("");
    try {
      if (storage) await api.patch(`/storages/${storage.id}`, storageBody(d));
      else await api.post("/storages", storageBody(d));
      onSaved();
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };
  return (
    <Modal title={storage ? "Edit connection" : "New storage connection"} onClose={onClose}
      footer={<><span className="spacer" /><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy} onClick={save}>{busy ? "Checking…" : "Save"}</button></>}>
      <StorageForm value={d} onChange={setD} saved={!!storage?.has_secret} />
      {error && <div className="error">{error}</div>}
    </Modal>
  );
}

import { useEffect, useRef, useState } from "react";
import { formatBytes } from "../util";
import { Icon } from "./Icon";

/** Uploads to a files table: many files or whole folders (their subfolders kept), three at a time, with progress. */

export interface UploadItem { file: File; folder: string }
interface Job extends UploadItem { id: number; status: "queued" | "uploading" | "done" | "error" | "cancelled"; loaded: number; error?: string }

const PARALLEL = 3;
const JUNK = new Set([".DS_Store", "Thumbs.db", "desktop.ini"]);
let nextId = 1;

const joinPath = (...parts: string[]) => parts.map((p) => p.replace(/^\/+|\/+$/g, "")).filter(Boolean).join("/");

/** Files picked with a folder picker: each keeps the folder it was in (inside `into`). */
export function fromPicker(list: FileList | null, into = ""): UploadItem[] {
  return Array.from(list ?? []).filter((f) => !JUNK.has(f.name)).map((f) => {
    const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || "";
    return { file: f, folder: joinPath(into, rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "") };
  });
}

/** Files and folders dropped on the page. Call it right in the drop handler (the browser only lends the items then). */
export function fromDrop(dt: DataTransfer, into = ""): Promise<UploadItem[]> {
  type Entry = { isFile: boolean; isDirectory: boolean; name: string; file?: (ok: (f: File) => void, err: (e: unknown) => void) => void;
    createReader?: () => { readEntries: (ok: (e: Entry[]) => void, err: (e: unknown) => void) => void } };
  const entries = Array.from(dt.items).filter((i) => i.kind === "file")
    .map((i) => (i as DataTransferItem & { webkitGetAsEntry?: () => Entry | null }).webkitGetAsEntry?.() ?? null);
  if (!entries.length || entries.some((e) => !e)) return Promise.resolve(fromPicker(dt.files, into));
  const out: UploadItem[] = [];
  const walk = async (e: Entry, folder: string): Promise<void> => {
    if (e.isFile && e.file) {
      if (JUNK.has(e.name)) return;
      const f = await new Promise<File>((ok, err) => e.file!(ok, err));
      out.push({ file: f, folder });
    } else if (e.isDirectory && e.createReader) {
      const reader = e.createReader();
      for (;;) {  // a directory reader hands out entries in batches
        const batch = await new Promise<Entry[]>((ok, err) => reader.readEntries(ok, err));
        if (!batch.length) break;
        for (const child of batch) await walk(child, joinPath(folder, e.name));
      }
    }
  };
  return Promise.all(entries.map((e) => walk(e!, into))).then(() => out);
}

/** uploadUrl: where files go (a files table's, or a linked files table's through its link); undefined = off. */
export function useUploads(uploadUrl: string | undefined, onFinished: (done: number, failed: number) => void) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const xhrs = useRef(new Map<number, XMLHttpRequest>());
  const [open, setOpen] = useState(true);

  const enqueue = (items: UploadItem[]) => {
    if (!items.length) return;
    setOpen(true);
    setJobs((j) => [...j.filter((x) => x.status !== "done" && x.status !== "cancelled"),
      ...items.map((it) => ({ ...it, id: nextId++, status: "queued" as const, loaded: 0 }))]);
  };

  // start queued uploads while fewer than PARALLEL are running
  useEffect(() => {
    if (!uploadUrl) return;
    const running = jobs.filter((j) => j.status === "uploading").length;
    const next = jobs.filter((j) => j.status === "queued").slice(0, Math.max(0, PARALLEL - running));
    if (!next.length) return;
    const set = (id: number, p: Partial<Job>) => setJobs((js) => js.map((x) => (x.id === id ? { ...x, ...p } : x)));
    for (const job of next) {
      const xhr = new XMLHttpRequest();
      xhrs.current.set(job.id, xhr);
      xhr.open("POST", uploadUrl);
      xhr.upload.onprogress = (e) => set(job.id, { loaded: e.loaded });
      xhr.onload = () => {
        xhrs.current.delete(job.id);
        if (xhr.status >= 200 && xhr.status < 300) set(job.id, { status: "done", loaded: job.file.size });
        else {
          let msg = xhr.status === 413 ? "Too large for the server" : xhr.statusText || `HTTP ${xhr.status}`;
          try { const d = JSON.parse(xhr.responseText).detail; if (typeof d === "string") msg = d; } catch { /* not JSON */ }
          set(job.id, { status: "error", error: msg });
        }
      };
      xhr.onerror = () => { xhrs.current.delete(job.id); set(job.id, { status: "error", error: "Connection lost" }); };
      xhr.onabort = () => { xhrs.current.delete(job.id); set(job.id, { status: "cancelled" }); };
      const body = new FormData();
      body.append("files", job.file);
      body.append("folder", job.folder);
      xhr.send(body);
    }
    setJobs((js) => js.map((x) => (next.some((n) => n.id === x.id) ? { ...x, status: "uploading" } : x)));
  }, [jobs, uploadUrl]);

  // a batch finished: tell the page (reload) once
  const active = jobs.some((j) => j.status === "queued" || j.status === "uploading");
  const reported = useRef(true);
  useEffect(() => {
    if (active) { reported.current = false; return; }
    if (reported.current || !jobs.length) return;
    reported.current = true;
    onFinished(jobs.filter((j) => j.status === "done").length, jobs.filter((j) => j.status === "error").length);
  }, [active]);

  useEffect(() => () => { xhrs.current.forEach((x) => x.abort()); }, []);

  const cancel = (j: Job) => {
    const x = xhrs.current.get(j.id);
    if (x) x.abort();
    else setJobs((js) => js.map((y) => (y.id === j.id ? { ...y, status: "cancelled" } : y)));
  };
  const cancelAll = () => jobs.filter((j) => j.status === "queued" || j.status === "uploading").forEach(cancel);

  const done = jobs.filter((j) => j.status === "done").length;
  const failed = jobs.filter((j) => j.status === "error").length;
  const total = jobs.filter((j) => j.status !== "cancelled").length;
  const bytes = jobs.reduce((n, j) => n + (j.status === "cancelled" ? 0 : j.file.size), 0);
  const sent = jobs.reduce((n, j) => n + (j.status === "cancelled" ? 0 : Math.min(j.loaded, j.file.size)), 0);

  const panel = jobs.length ? (
    <div className="upload-panel" role="status">
      <div className="upload-head">
        <b>{active ? `Uploading ${done + 1 > total ? total : done + 1} of ${total}` : `${done} upload${done === 1 ? "" : "s"} complete`}
          {failed ? <span className="error-text"> · {failed} failed</span> : null}</b>
        <span className="spacer" />
        <button className="icon-btn" onClick={() => setOpen(!open)} aria-label={open ? "Collapse" : "Expand"}><Icon name={open ? "right" : "up"} size={14} /></button>
        {active
          ? <button className="icon-btn" onClick={cancelAll} title="Cancel all" aria-label="Cancel all uploads"><Icon name="x" size={14} /></button>
          : <button className="icon-btn" onClick={() => setJobs([])} aria-label="Close"><Icon name="x" size={14} /></button>}
      </div>
      {active && <div className="upload-total"><span style={{ width: `${bytes ? (sent / bytes) * 100 : 0}%` }} /></div>}
      {open && (
        <div className="upload-list">
          {jobs.filter((j) => j.status !== "cancelled").map((j) => (
            <div key={j.id} className={`upload-row ${j.status}`}>
              <div className="upload-name">
                <span className="ellipsis" title={joinPath(j.folder, j.file.name)}>{j.folder && <span className="muted">{j.folder}/</span>}{j.file.name}</span>
                <span className="muted small">{j.status === "error" ? <span className="error-text">{j.error}</span> : formatBytes(j.file.size)}</span>
              </div>
              {j.status === "uploading" && <div className="upload-bar"><span style={{ width: `${j.file.size ? (j.loaded / j.file.size) * 100 : 100}%` }} /></div>}
              <span className="upload-state">
                {j.status === "done" ? <span className="ok">✓</span>
                  : j.status === "error" ? <span className="error-text">!</span>
                    : <button className="icon-btn" onClick={() => cancel(j)} aria-label={`Cancel ${j.file.name}`}><Icon name="x" size={12} /></button>}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  ) : null;

  return { enqueue, panel, active };
}

import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { Icon } from "../../components/Icon";
import { Modal } from "../../components/Overlay";
import type { ExtensionProps } from "..";
import type { Rec, Workspace } from "../../types";
import { tk, type ProjectSummary } from "./tk";
import "./takeoff.css";

const ProjectPage = lazy(() => import("./Project"));

/** Blueprint takeoff: #/x/takeoff (the projects) and #/x/takeoff/<project id> (one drawing set). */
export default function Takeoff(props: ExtensionProps) {
  const id = props.path.split("/")[0];
  return id
    ? <Suspense fallback={<div className="boot">Opening the drawings…</div>}><ProjectPage key={id} id={id} {...props} /></Suspense>
    : <Projects {...props} />;
}

function Projects({ go, onMenu, onError, onWorkspaceChanged }: ExtensionProps) {
  const [list, setList] = useState<ProjectSummary[] | null>(null);
  const [busy, setBusy] = useState("");
  const [pick, setPick] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { tk.projects().then(setList).catch((e) => onError(e.message)); }, []);

  const start = async (f: File, name: string, table: string, folder: string) => {
    setFile(null);
    setBusy(`Reading ${f.name}…`);
    try {
      if (table) {  // kept in a files table (its S3 / WebDAV storage), and the takeoff made from that file
        const body = new FormData();
        body.append("files", f);
        body.append("folder", folder);
        const r = await api.upload<{ records: Rec[] }>(`/tables/${table}/files`, body);
        onWorkspaceChanged();
        go((await tk.fromFile(r.records[0].id, name)).id);
      } else go((await tk.upload(f, name)).id);
    } catch (e) { onError((e as Error).message); }
    setBusy("");
  };

  return (
    <div className="page tk-projects">
      <header className="page-head">
        <div className="title-row">
          <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Menu"><Icon name="menu" size={20} /></button>
          <Icon name="ruler" size={20} />
          <h1>Blueprint takeoff</h1>
          <span className="spacer" />
          <input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) setFile(f); e.target.value = ""; }} />
          <button className="btn" disabled={!!busy} onClick={() => setPick(true)}><Icon name="folder" size={14} /> From a files table</button>
          <button className="btn primary" disabled={!!busy} onClick={() => input.current?.click()}><Icon name="upload" size={14} /> Upload drawings (PDF)</button>
        </div>
      </header>
      <main className="page-body tk-projects-body"
        onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = [...e.dataTransfer.files].find((x) => /\.pdf$/i.test(x.name)); if (f) setFile(f); }}>
        {busy && <div className="tk-busy"><Icon name="sync" size={16} className="spin" /> {busy} (sheet numbers, titles and scales are read from every page)</div>}
        {list === null ? <p className="muted">Loading…</p> : !list.length ? (
          <div className="tk-empty">
            <Icon name="ruler" size={32} />
            <h2>Start a takeoff</h2>
            <p>Upload a drawing set (PDF), or open one from a files table. Probyte reads each sheet's number, title and scale; you choose
              the sheets to work on, add items from their legends, find, count and measure them, and get a takeoff list to export.</p>
            <p className="muted small">Or drop a PDF here. PDFs in files tables also open here from the file viewer.</p>
          </div>
        ) : (
          <div className="tk-project-grid">
            {list.map((p) => (
              <button key={p.id} className="tk-project-card" onClick={() => go(p.id)}>
                <span className="tk-thumb"><img src={tk.image(p.id, 0, 50)} alt="" loading="lazy" /></span>
                <b>{p.name}</b>
                <span className="muted small">{p.page_count} sheet{p.page_count === 1 ? "" : "s"} · {p.included} in the takeoff · {p.items} item{p.items === 1 ? "" : "s"}
                  {p.source?.kind === "files" ? " · from a files table" : ""}</span>
              </button>
            ))}
          </div>
        )}
      </main>
      {file && <UploadDialog file={file} onClose={() => setFile(null)} onStart={start} />}
      {pick && <PickPdf onClose={() => setPick(false)} onPick={async (rec, name) => {
        setPick(false); setBusy(`Reading ${name}…`);
        try {
          const existing = await tk.projects(rec);  // a takeoff of that file already: open it
          go(existing[0]?.id ?? (await tk.fromFile(rec, name.replace(/\.pdf$/i, ""))).id);
        } catch (e) { onError((e as Error).message); }
        setBusy("");
      }} />}
    </div>
  );
}

const DEST_KEY = "probyte.takeoff.destination";

/** Name the takeoff, and choose where the PDF is kept: a files table (and folder), or only in the takeoff. */
function UploadDialog({ file, onClose, onStart }: { file: File; onClose: () => void; onStart: (f: File, name: string, table: string, folder: string) => void }) {
  const [ws, setWs] = useState<Workspace | null>(null);
  const saved = (() => { try { return JSON.parse(localStorage.getItem(DEST_KEY) || "{}"); } catch { return {}; } })();
  const [name, setName] = useState(file.name.replace(/\.pdf$/i, ""));
  const [table, setTable] = useState<string>(saved.table ?? "");
  const [folder, setFolder] = useState<string>(saved.folder ?? "Drawings");
  useEffect(() => { api.get<Workspace>("/workspace").then((w) => {
    setWs(w);
    if (saved.table && !w.tables.some((t) => t.id === saved.table && t.kind === "files")) setTable("");
  }).catch(() => setWs(null)); }, []);
  const tables = ws?.tables.filter((t) => t.kind === "files") ?? [];
  const go = () => {
    try { localStorage.setItem(DEST_KEY, JSON.stringify({ table, folder })); } catch { /* private window */ }
    onStart(file, name.trim() || file.name, table, folder.trim());
  };
  return (
    <Modal title="New takeoff" onClose={onClose}
      footer={<><span className="spacer" /><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={go}>Start</button></>}>
      <div className="form">
        <label className="stack">Name<input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></label>
        <label className="stack">Keep the PDF in
          <select className="input" value={table} onChange={(e) => setTable(e.target.value)} aria-label="Keep the PDF in">
            <option value="">Only this takeoff</option>
            {tables.map((t) => <option key={t.id} value={t.id}>{t.name} (files table)</option>)}
          </select>
          <span className="hint">{table ? "The drawings are uploaded to that table's storage (S3 / WebDAV) like any file, and the takeoff is made from them."
            : tables.length ? "The drawings are kept with the takeoff only." : "Make a files table to keep your drawings in your own storage."}</span>
        </label>
        {table && <label className="stack">Folder<input className="input" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="(top level)" /></label>}
        <p className="muted small">{file.name} · {(file.size / 1048576).toFixed(1)} MB</p>
      </div>
    </Modal>
  );
}

/** A PDF from your files tables: search them all by name or folder. */
function PickPdf({ onClose, onPick }: { onClose: () => void; onPick: (recordId: string, name: string) => void }) {
  const [pdfs, setPdfs] = useState<{ id: string; name: string; folder: string; table: string }[] | null>(null);
  const [q, setQ] = useState("");
  const [tables, setTables] = useState(0);
  useEffect(() => {
    api.get<Workspace>("/workspace").then(async (w) => {
      const files = w.tables.filter((t) => t.kind === "files");
      setTables(files.length);
      const all = await Promise.all(files.map((t) => api.get<{ fields: { id: string; system_key?: string | null }[]; records: Rec[] }>(`/tables/${t.id}`)
        .then((d) => {
          const key = (k: string) => d.fields.find((f) => f.system_key === k)?.id ?? "";
          return d.records.map((r) => ({ id: r.id, name: String(r.data[key("name")] ?? ""), folder: String(r.data[key("folder")] ?? ""), table: t.name }))
            .filter((r) => /\.pdf$/i.test(r.name));
        }).catch(() => [])));
      setPdfs(all.flat().sort((a, b) => a.name.localeCompare(b.name)));
    });
  }, []);
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (pdfs ?? []).filter((f) => words.every((w) => `${f.folder}/${f.name} ${f.table}`.toLowerCase().includes(w)));
  return (
    <Modal title="Drawings from your files tables" onClose={onClose}>
      <div className="form">
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name or folder…" autoFocus aria-label="Search PDFs" />
        <div className="tk-pick-list">
          {pdfs === null ? <p className="muted">Loading…</p> : !tables ? <p className="muted">You have no files tables. Upload the PDF instead.</p>
            : !shown.length ? <p className="muted">{pdfs.length ? "No PDF matches." : "No PDFs in your files tables."}</p>
              : shown.slice(0, 300).map((f) => (
                <button key={f.id} className="menu-item tk-pick-row" onClick={() => onPick(f.id, f.name)}>
                  <Icon name="file" size={14} />
                  <span className="tk-pick-name">{f.folder && <span className="muted">{f.folder}/</span>}{f.name}</span>
                  <span className="muted small">{f.table}</span>
                </button>
              ))}
        </div>
      </div>
    </Modal>
  );
}

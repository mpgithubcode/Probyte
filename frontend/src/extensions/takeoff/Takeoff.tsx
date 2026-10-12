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

function Projects({ go, onMenu, onError }: ExtensionProps) {
  const [list, setList] = useState<ProjectSummary[] | null>(null);
  const [busy, setBusy] = useState("");
  const [pick, setPick] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { tk.projects().then(setList).catch((e) => onError(e.message)); }, []);

  const upload = async (f: File | undefined) => {
    if (!f) return;
    setBusy(`Reading ${f.name}…`);
    try {
      const p = await tk.upload(f, f.name.replace(/\.pdf$/i, ""));
      go(p.id);
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
          <input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={(e) => upload(e.target.files?.[0])} />
          <button className="btn" disabled={!!busy} onClick={() => setPick(true)}><Icon name="folder" size={14} /> From a files table</button>
          <button className="btn primary" disabled={!!busy} onClick={() => input.current?.click()}><Icon name="upload" size={14} /> Upload drawings (PDF)</button>
        </div>
      </header>
      <main className="page-body tk-projects-body"
        onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload([...e.dataTransfer.files].find((f) => /\.pdf$/i.test(f.name))); }}>
        {busy && <div className="tk-busy"><Icon name="sync" size={16} className="spin" /> {busy} (sheet numbers, titles and scales are read from every page)</div>}
        {list === null ? <p className="muted">Loading…</p> : !list.length ? (
          <div className="tk-empty">
            <Icon name="ruler" size={32} />
            <h2>Start a takeoff</h2>
            <p>Upload a drawing set (PDF). Probyte reads each sheet's number, title and scale; you choose the sheets to work on,
              add items from their legends, count and measure them, and get a takeoff list to export.</p>
            <p className="muted small">Or drop a PDF here.</p>
          </div>
        ) : (
          <div className="tk-project-grid">
            {list.map((p) => (
              <button key={p.id} className="tk-project-card" onClick={() => go(p.id)}>
                <span className="tk-thumb"><img src={tk.image(p.id, 0, 50)} alt="" loading="lazy" /></span>
                <b>{p.name}</b>
                <span className="muted small">{p.page_count} sheet{p.page_count === 1 ? "" : "s"} · {p.included} in the takeoff · {p.items} item{p.items === 1 ? "" : "s"}</span>
              </button>
            ))}
          </div>
        )}
      </main>
      {pick && <PickPdf onClose={() => setPick(false)} onPick={async (rec, name) => {
        setPick(false); setBusy(`Reading ${name}…`);
        try { go((await tk.fromFile(rec, name.replace(/\.pdf$/i, ""))).id); } catch (e) { onError((e as Error).message); }
        setBusy("");
      }} />}
    </div>
  );
}

/** A PDF from one of your files tables. */
function PickPdf({ onClose, onPick }: { onClose: () => void; onPick: (recordId: string, name: string) => void }) {
  const [ws, setWs] = useState<Workspace | null>(null);
  const [table, setTable] = useState("");
  const [pdfs, setPdfs] = useState<{ id: string; name: string; folder: string }[] | null>(null);
  useEffect(() => { api.get<Workspace>("/workspace").then((w) => { setWs(w); const t = w.tables.find((x) => x.kind === "files"); if (t) setTable(t.id); }); }, []);
  useEffect(() => {
    if (!table) return;
    setPdfs(null);
    api.get<{ fields: { id: string; system_key?: string | null }[]; records: Rec[] }>(`/tables/${table}`).then((d) => {
      const key = (k: string) => d.fields.find((f) => f.system_key === k)?.id ?? "";
      setPdfs(d.records.map((r) => ({ id: r.id, name: String(r.data[key("name")] ?? ""), folder: String(r.data[key("folder")] ?? "") }))
        .filter((r) => /\.pdf$/i.test(r.name)));
    });
  }, [table]);
  const tables = ws?.tables.filter((t) => t.kind === "files") ?? [];
  return (
    <Modal title="Drawings from a files table" onClose={onClose}>
        <div className="form">
          {!tables.length ? <p className="muted">You have no files tables. Upload the PDF instead.</p> : (<>
            <label className="stack">Files table
              <select className="input" value={table} onChange={(e) => setTable(e.target.value)}>
                {tables.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </label>
            <div className="tk-pick-list">
              {pdfs === null ? <p className="muted">Loading…</p> : !pdfs.length ? <p className="muted">No PDFs in this table.</p>
                : pdfs.map((f) => (
                  <button key={f.id} className="menu-item" onClick={() => onPick(f.id, f.name)}>
                    <Icon name="file" size={14} /> <span>{f.folder && <span className="muted">{f.folder}/</span>}{f.name}</span>
                  </button>
                ))}
            </div>
          </>)}
        </div>
    </Modal>
  );
}

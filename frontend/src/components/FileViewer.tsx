import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { FILE_ACTIONS, useExtensions } from "../extensions";
import { OfficeEditor, convertsTo, officeMode, saveOffice, startOffice, useOfficeStatus, type OfficeSession } from "./OfficeEditor";
import { Popover, usePopover } from "./Overlay";
import { createPortal } from "react-dom";
import { api } from "../api";
import { attachmentUrl, editableKind, fetchBytes, fileExt, fileWriteUrl, imageUrl, isSvg, previewPdfUrl, textUrl, thumbUrl, viewKind } from "../files";
import type { Attachment, Field, Rec } from "../types";
import { formatBytes, isEditable } from "../util";
import { CellValue, FieldInput } from "./Cells";
import { FieldIcon, Icon } from "./Icon";

const RichText = lazy(() => import("./RichText"));
const PdfView = lazy(() => import("./PdfView"));

/** The browser's own PDF viewer where it shows every page (desktop browsers); pdf.js elsewhere (phones). */
const nativePdf = () => (navigator as Navigator & { pdfViewerEnabled?: boolean }).pdfViewerEnabled === true
  && !window.matchMedia("(pointer: coarse)").matches;

function Pdf({ src, data, name }: { src?: string; data?: ArrayBuffer; name: string }) {
  const [blob, setBlob] = useState<string | null>(null);
  useEffect(() => {
    if (!data || !nativePdf()) return;
    const u = URL.createObjectURL(new Blob([data], { type: "application/pdf" }));
    setBlob(u);
    return () => URL.revokeObjectURL(u);
  }, [data]);
  if (nativePdf()) return src ? <iframe className="viewer-frame" src={src} title={name} /> : blob ? <iframe className="viewer-frame" src={blob} title={name} /> : <Loading />;
  return <Suspense fallback={<Loading />}><PdfView src={src} data={data} /></Suspense>;
}

const MAX_TEXT = 5 * 1024 * 1024;     // larger text files are downloaded rather than shown
const MAX_PARSE = 40 * 1024 * 1024;   // spreadsheets / Word files read in the browser
const SHEET_ROWS = 5000, SHEET_COLS = 200;

export interface ViewerEntry { item: Attachment; rec?: Rec }

/** Full-screen viewer (like Google Drive's): the file in the middle, actions on top, details on the right.
 * Files of files tables can be edited here (text files; Markdown as formatted text) and their fields changed. */
export function FullViewer({ entries, index, onClose, fields, canEdit, canChangeFile = canEdit, onUpdate, onDelete, onSaved, onChanged, onReplace, location, onMove }: {
  entries: ViewerEntry[]; index: number; onClose: () => void;
  fields?: Field[];                                     // the record's fields, for the details panel
  canEdit?: boolean;                                    // the record's fields
  canChangeFile?: boolean;                              // the file's contents (replace, edit; a link's "Edit files")
  onChanged?: () => void;                               // a file was saved from ONLYOFFICE (its name may have changed)
  onUpdate?: (rec: Rec, patch: Record<string, unknown>) => void;
  onDelete?: (rec: Rec) => void;
  onSaved?: (rec: Rec) => void;                         // after saving an edited file / replacing it
  onReplace?: boolean;                                  // offer "Replace file…"
  location?: string | null;                             // where the files are (bucket / folder)
  onMove?: (entry: ViewerEntry) => void;                // went to another file
}) {
  const [i, setI] = useState(Math.max(0, Math.min(index, entries.length - 1)));
  const [info, setInfo] = useState(() => window.innerWidth > 900);
  const [draft, setDraft] = useState<string | null>(null);   // the edited text, while editing
  const [original, setOriginal] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [version, setVersion] = useState(0);                 // reload the file after saving / replacing
  const [confirmDelete, setConfirmDelete] = useState(false);
  const replaceRef = useRef<HTMLInputElement>(null);
  const entry = entries[i];
  const item = entry?.item;
  const editing = draft !== null;
  const dirty = editing && draft !== original;
  // ONLYOFFICE: the open editor, whether it has changes not yet in the storage, and the "save or discard?" question
  const officeStatus = useOfficeStatus();
  const extensions = useExtensions();
  const [office, setOffice] = useState<OfficeSession | null>(null);
  const [officeDirty, setOfficeDirty] = useState(false);
  const [officeNote, setOfficeNote] = useState("");
  const [ask, setAsk] = useState<{ then: () => void } | null>(null);
  const editMenu = usePopover();
  const retried = useRef(false);  // (one fresh try after a failed download)

  useEffect(() => {  // leaving the page with unsaved changes in the editor
    if (!officeDirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [officeDirty]);

  const openOffice = async (mode: "edit" | "view", fresh = false) => {
    editMenu.close();
    if (fresh) retried.current = false;
    setBusy(true); setErr(""); setOfficeNote("");
    try { setOffice(await startOffice(item!, mode)); setOfficeDirty(false); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  const saveOfficeNow = async (): Promise<boolean> => {
    if (!office) return true;
    setBusy(true); setErr(""); setOfficeNote("Saving…");
    try {
      await saveOffice(office);
      setOfficeDirty(false); setOfficeNote(office.saves_as ? `Saved as ${office.saves_as}` : "Saved");
      setVersion((v) => v + 1);
      onChanged?.();
      return true;
    } catch (e) { setErr((e as Error).message); setOfficeNote(""); return false; }
    finally { setBusy(false); }
  };
  const endOffice = async (how: "close" | "discard") => {
    const s = office;
    setOffice(null); setOfficeDirty(false); setOfficeNote(""); setAsk(null);
    if (s) await api.post(`/office/sessions/${s.session}/${how}`).catch(() => undefined);
    setVersion((v) => v + 1);
  };

  const leave = (then: () => void) => {
    if (office) {  // the editor: unsaved changes → save, discard or keep editing
      if (officeDirty) { setAsk({ then }); return; }
      endOffice("close").then(then);
      return;
    }
    if (dirty && !window.confirm("Discard your changes to this file?")) return;
    setDraft(null); setErr(""); setConfirmDelete(false);
    then();
  };
  const go = (n: number) => n >= 0 && n < entries.length && leave(() => { setI(n); onMove?.(entries[n]); });
  const close = () => leave(onClose);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = !!t.closest("input, textarea, select, [contenteditable=true]");
      if (e.key === "Escape" && !document.querySelector(".popover") && !ask) {
        e.preventDefault();
        if (office) leave(() => undefined); else if (typing && editing) leave(() => undefined); else close();
      }
      if (typing) {
        if ((e.metaKey || e.ctrlKey) && e.key === "s" && editing) { e.preventDefault(); save(); }
        return;
      }
      if (e.key === "ArrowRight") go(i + 1);
      if (e.key === "ArrowLeft") go(i - 1);
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  });

  if (!item) return null;
  const kind = viewKind(item);
  const canEditFile = !!canChangeFile && editableKind(item) && item.size <= MAX_TEXT;
  const oMode = officeMode(officeStatus, item);
  // what extensions do with this file (Blueprint takeoff for a PDF): in the Edit menu
  const fileActions = !item.url && item.file
    ? extensions.filter((x) => x.enabled).flatMap((x) => FILE_ACTIONS.filter((a) => a.ext === x.id && a.accepts(item))) : [];
  const canOffice = !!oMode;
  const newFormat = convertsTo(officeStatus, item);   // .doc → edited and saved as .docx

  async function save() {
    if (!item || draft === null) return;
    setBusy(true); setErr("");
    try {
      const rec = await api.put<Rec>(fileWriteUrl(item, "text"), { text: draft });
      setDraft(null); setVersion((v) => v + 1);
      onSaved?.(rec);
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  }

  const replace = async (list: FileList | null) => {
    if (!list?.[0]) return;
    const body = new FormData();
    body.append("file", list[0]);
    setBusy(true); setErr("");
    try {
      const rec = await api.upload<Rec>(fileWriteUrl(item, "file"), body, "PUT");
      setVersion((v) => v + 1);
      onSaved?.(rec);
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
    if (replaceRef.current) replaceRef.current.value = "";
  };

  return createPortal(
    <div className="viewer" role="dialog" aria-label={item.name}>
      <header className="viewer-bar">
        <button className="viewer-btn" onClick={close} aria-label="Close" title="Close (Esc)"><Icon name="x" size={18} /></button>
        <span className="viewer-ext">{fileExt(item.name)}</span>
        <div className="viewer-title">
          <b title={item.name}>{item.name}</b>
          <span>{formatBytes(item.size)}{entries.length > 1 ? ` · ${i + 1} of ${entries.length}` : ""}{dirty ? " · unsaved changes" : ""}</span>
        </div>
        <span className="spacer" />
        {err && <span className="viewer-err" title={err}>{err}</span>}
        {office ? (
          <>
            <span className="office-badge" title={office.saves_as ? `Saving makes it ${office.saves_as} (a new version of this file)` : undefined}>
              ONLYOFFICE{office.mode === "view" ? " · read-only" : office.converted_from ? ` · as .${fileExt(office.saves_as ?? "")}` : ""}</span>
            {officeNote && <span className="viewer-note">{officeNote}</span>}
            {office.mode === "edit" && <button className="viewer-btn text" disabled={busy} onClick={saveOfficeNow} title="Save to the storage now">
              <Icon name="upload" size={16} /> <span>{busy ? "Saving…" : "Save"}</span></button>}
            <button className="viewer-btn primary" onClick={() => leave(() => undefined)}>Close editor</button>
          </>
        ) : editing ? (
          <>
            <button className="viewer-btn text" onClick={() => leave(() => undefined)}>Cancel</button>
            <button className="viewer-btn primary" disabled={busy || !dirty} onClick={save}>{busy ? "Saving…" : "Save"}</button>
          </>
        ) : (
          <>
            {canOffice || fileActions.length ? <button className="viewer-btn text" onClick={editMenu.open} disabled={busy} aria-haspopup="menu">
              <Icon name="edit" size={16} /> <span>Edit</span> ▾</button>
              : canEditFile && <button className="viewer-btn text" disabled={busy || original === null} onClick={() => setDraft(original ?? "")}>
                <Icon name="edit" size={16} /> <span>Edit</span></button>}
            {editMenu.anchor && (
              <Popover anchor={editMenu.anchor} onClose={editMenu.close} align="right" width={250} className="viewer-menu">
                <div className="menu-list">
                  {canEditFile && <button className="menu-item" disabled={original === null} onClick={() => { editMenu.close(); setDraft(original ?? ""); }}>
                    <Icon name="edit" size={14} /> Edit here</button>}
                  {canOffice && oMode === "edit" && canChangeFile && <button className="menu-item" onClick={() => openOffice("edit", true)}>
                    <Icon name="file" size={14} /> Edit in ONLYOFFICE{newFormat && <span className="muted small"> (saves as .{newFormat})</span>}</button>}
                  {canOffice && <button className="menu-item" onClick={() => openOffice("view", true)}>
                    <Icon name="eye" size={14} /> Open in ONLYOFFICE (read-only)</button>}
                  {fileActions.map((a) => (
                    <button key={a.ext} className="menu-item" disabled={busy} onClick={async () => {
                      editMenu.close(); setBusy(true); setErr("");
                      try { const to = await a.run(item.id, item.name); window.location.hash = to; onClose(); } catch (e) { setErr((e as Error).message); }
                      setBusy(false);
                    }}><Icon name={a.icon} size={14} /> {a.label}</button>
                  ))}
                </div>
              </Popover>
            )}
            {(kind === "pdf" || kind === "image" || kind === "video" || kind === "audio") && (item.file || item.url) && (
              <a className="viewer-btn" href={attachmentUrl(item)} target="_blank" rel="noreferrer" title="Open in a new tab"><Icon name="external" size={17} /></a>
            )}
            <a className="viewer-btn text" href={attachmentUrl(item, true)} title="Download"><Icon name="download" size={17} /> <span>Download</span></a>
            {onReplace && canChangeFile && entry.rec && (<>
              <input ref={replaceRef} type="file" hidden onChange={(e) => replace(e.target.files)} />
              <button className="viewer-btn" title="Replace file (upload a new version)" disabled={busy} onClick={() => replaceRef.current?.click()}>
                <Icon name="upload" size={17} /></button>
            </>)}
          </>
        )}
        <button className={`viewer-btn ${info ? "on" : ""}`} onClick={() => setInfo(!info)} title="Details" aria-label="Details">
          <Icon name="list" size={17} /></button>
      </header>
      <div className="viewer-main">
        <div className="viewer-stage">
          {entries.length > 1 && i > 0 && !editing && !office && <button className="viewer-nav prev" onClick={() => go(i - 1)} aria-label="Previous file"><Icon name="left" size={22} /></button>}
          {office
            ? <OfficeEditor key={office.session} session={office} onModified={() => { setOfficeDirty(true); setOfficeNote("Unsaved changes"); }}
                onError={(m, code) => {
                  // what the editor said goes to the server too (Settings can't see a browser's console)
                  api.post(`/office/sessions/${office.session}/error`, { code: code ?? null, message: m, page: window.location.origin }).catch(() => undefined);
                  // -4: the Document Server couldn't download the file. It keeps that result under the document's key,
                  // so reopening would fail the same way: retire the key and reopen once with a new one.
                  if (code === -4 && office && !retried.current) {
                    retried.current = true;
                    api.post(`/office/sessions/${office.session}/discard`).catch(() => undefined)
                      .then(() => openOffice(office.mode));
                    return;
                  }
                  setErr(m);
                }} />
            : <FileBody key={`${item.id}:${item.size}:${version}`} item={item} kind={kind} editing={editing} draft={draft}
                onDraft={setDraft} onText={setOriginal} />}
          {entries.length > 1 && i < entries.length - 1 && !editing && !office && <button className="viewer-nav next" onClick={() => go(i + 1)} aria-label="Next file"><Icon name="right" size={22} /></button>}
          {ask && (
            <div className="viewer-dialog-backdrop">
              <div className="viewer-dialog" role="alertdialog" aria-label="Unsaved changes">
                <b>Save your changes to “{item.name}”?</b>
                <p>They aren't in the storage yet. If you discard them, the file stays as it was when it was last saved.</p>
                <div className="row">
                  <button className="btn primary" disabled={busy} onClick={async () => {
                    const then = ask.then;
                    if (await saveOfficeNow()) { await endOffice("close"); then(); } else setAsk(null);
                  }}>{busy ? "Saving…" : "Save"}</button>
                  <button className="btn danger-ghost" disabled={busy} onClick={async () => { const then = ask.then; await endOffice("discard"); then(); }}>Discard</button>
                  <span className="spacer" />
                  <button className="btn" disabled={busy} onClick={() => setAsk(null)}>Keep editing</button>
                </div>
              </div>
            </div>
          )}
        </div>
        {info && (
          <aside className="viewer-info">
            <div className="viewer-info-head"><b>Details</b><button className="icon-btn" onClick={() => setInfo(false)} aria-label="Close details"><Icon name="x" size={14} /></button></div>
            <Details item={item} rec={entry.rec} fields={fields} canEdit={!!canEdit && !editing} onUpdate={onUpdate} location={location} />
            {entry.rec && onDelete && canEdit && (
              <div className="viewer-info-foot">
                {confirmDelete ? (
                  <>
                    <span className="muted small">Moves it to the trash; the file is deleted from the storage when the trash is emptied.</span>
                    <div className="row">
                      <button className="btn sm danger" onClick={() => { onDelete(entry.rec!); if (entries.length > 1) setI(Math.min(i, entries.length - 2)); else onClose(); setConfirmDelete(false); }}>Delete</button>
                      <button className="btn sm" onClick={() => setConfirmDelete(false)}>Cancel</button>
                    </div>
                  </>
                ) : <button className="btn sm danger-ghost" onClick={() => setConfirmDelete(true)}><Icon name="trash" size={14} /> Delete file</button>}
              </div>
            )}
          </aside>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** The details panel: the record's fields (editable ones can be changed here), or a plain attachment's name, type and size. */
function Details({ item, rec, fields, canEdit, onUpdate, location }: {
  item: Attachment; rec?: Rec; fields?: Field[]; canEdit: boolean; onUpdate?: (rec: Rec, patch: Record<string, unknown>) => void; location?: string | null;
}) {
  if (!rec || !fields) {
    return (
      <dl className="viewer-details">
        <dt>Name</dt><dd>{item.name}</dd>
        <dt>Type</dt><dd>{item.mime || "–"}</dd>
        <dt>Size</dt><dd>{formatBytes(item.size)} <span className="muted small">({item.size.toLocaleString()} bytes)</span></dd>
      </dl>
    );
  }
  const shown = fields.filter((f) => f.system_key !== "text" && f.system_key !== "file" && f.type !== "button");
  const folder = fields.find((f) => f.system_key === "folder");
  const path = [location, folder ? rec.data[folder.id] : null, item.name].filter(Boolean).join("/");
  return (
    <div className="viewer-details fields">
      {thumbUrl(item) && <img className="viewer-thumb" src={thumbUrl(item)!} alt="" />}
      <div className="viewer-field">
        <span className="viewer-label">Location</span>
        <span className="viewer-path" title={path}>{path}</span>
      </div>
      {shown.map((f) => {
        const editable = canEdit && !!onUpdate && isEditable(f) && !f.readonly;
        const v = rec.data[f.id];
        return (
          <div key={f.id} className="viewer-field">
            <span className="viewer-label"><FieldIcon type={f.type} /> {f.name}</span>
            {editable
              ? <FieldInput key={`${rec.id}:${f.id}`} field={f} value={v} onCommit={(nv) => onUpdate!(rec, { [f.id]: nv })} />
              : <span className="viewer-value">{v === null || v === undefined || v === "" ? <span className="muted">–</span> : <CellValue field={f} value={v} />}</span>}
          </div>
        );
      })}
    </div>
  );
}

/** The file itself, shown the way it can be best in a browser. */
function FileBody({ item, kind, editing, draft, onDraft, onText }: {
  item: Attachment; kind: ReturnType<typeof viewKind>; editing: boolean; draft: string | null;
  onDraft: (s: string) => void; onText: (s: string | null) => void;
}) {
  const url = attachmentUrl(item);
  switch (kind) {
    case "image":
      if (isSvg(item)) return <SvgView item={item} editing={editing} draft={draft} onDraft={onDraft} onText={onText} />;
      return <ImageView item={item} />;
    case "pdf": return item.file || item.url ? <Pdf src={url} name={item.name} /> : <BlobPdf url={attachmentUrl(item, true)} name={item.name} />;
    case "video": return <div className="viewer-media"><video src={url} controls autoPlay={false} preload="metadata" /></div>;
    case "audio": return <div className="viewer-media audio"><span className="viewer-ext big">{fileExt(item.name)}</span><audio src={url} controls preload="metadata" /></div>;
    case "text": case "markdown":
      return <TextView item={item} markdown={kind === "markdown"} editing={editing} draft={draft} onDraft={onDraft} onText={onText} />;
    case "sheet": return <SheetView item={item} />;
    case "document": return <DocumentView item={item} />;
    default: return <NoPreview item={item} />;
  }
}

function ImageView({ item }: { item: Attachment }) {
  const [zoom, setZoom] = useState(false);
  return (
    <div className={`viewer-image ${zoom ? "zoom" : ""}`} onClick={() => setZoom(!zoom)} title={zoom ? "Fit to screen" : "Actual size"}>
      <img src={imageUrl(item)} alt={item.name} />
    </div>
  );
}

/** An SVG: the picture; editing shows its code (saved back as the file). */
function SvgView({ item, editing, draft, onDraft, onText }: {
  item: Attachment; editing: boolean; draft: string | null; onDraft: (s: string) => void; onText: (s: string | null) => void;
}) {
  useEffect(() => {
    onText(null);
    if (!item.file) return;
    let live = true;
    fetchBytes(textUrl(item)).then((b) => live && onText(new TextDecoder().decode(b))).catch(() => undefined);
    return () => { live = false; };
  }, [item.id]);
  if (editing) {
    return (
      <div className="viewer-doc code">
        <textarea className="viewer-code-edit" autoFocus spellCheck={false} value={draft ?? ""} onChange={(e) => onDraft(e.target.value)} />
      </div>
    );
  }
  return <div className="viewer-image svg"><img src={attachmentUrl(item)} alt={item.name} /></div>;
}

/** A PDF attachment (served as a download): read it, then show it. */
function BlobPdf({ url, name }: { url: string; name: string }) {
  const [data, setData] = useState<ArrayBuffer | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { fetchBytes(url).then(setData).catch((e) => setErr(e.message)); }, [url]);
  if (err) return <div className="viewer-msg error">{err}</div>;
  return data ? <Pdf data={data} name={name} /> : <Loading />;
}

function TextView({ item, markdown, editing, draft, onDraft, onText }: {
  item: Attachment; markdown: boolean; editing: boolean; draft: string | null; onDraft: (s: string) => void; onText: (s: string | null) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const big = item.size > MAX_TEXT;
  useEffect(() => {
    onText(null);
    if (big) return;
    let live = true;
    fetchBytes(textUrl(item))
      .then((b) => { const t = new TextDecoder().decode(b); if (live) { setText(t); onText(t); } })
      .catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [item.id]);
  if (big) return <NoPreview item={item} why="This file is too large to show here." />;
  if (err) return <div className="viewer-msg error">{err}</div>;
  if (text === null) return <Loading />;
  if (markdown) {
    return (
      <div className="viewer-doc">
        <Suspense fallback={<Loading />}>
          <RichText markdown={text} editable={editing} onChange={editing ? onDraft : undefined} />
        </Suspense>
      </div>
    );
  }
  return (
    <div className="viewer-doc code">
      {editing
        ? <textarea className="viewer-code-edit" autoFocus spellCheck={false} value={draft ?? ""} onChange={(e) => onDraft(e.target.value)} />
        : <pre className="viewer-code">{text || <span className="muted">(empty file)</span>}</pre>}
    </div>
  );
}

/** Excel, OpenDocument, Numbers and CSV files as tables, one tab per sheet. */
function SheetView({ item }: { item: Attachment }) {
  const [sheets, setSheets] = useState<{ name: string; rows: string[][]; more: boolean }[] | null>(null);
  const [tab, setTab] = useState(0);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (item.size > MAX_PARSE) { setErr("This spreadsheet is too large to show here; download it instead."); return; }
    let live = true;
    (async () => {
      const [XLSX, bytes] = await Promise.all([import("xlsx"), fetchBytes(item.file ? attachmentUrl(item, true) : attachmentUrl(item, true))]);
      const ext = fileExt(item.name);
      const wb = ext === "csv" || ext === "tsv"
        ? XLSX.read(new TextDecoder().decode(bytes), { type: "string", FS: ext === "tsv" ? "\t" : undefined })
        : XLSX.read(bytes, { type: "array", cellDates: true });
      const out = wb.SheetNames.map((name) => {
        const rows = XLSX.utils.sheet_to_json<string[]>(wb.Sheets[name], { header: 1, raw: false, blankrows: true, defval: "" });
        return { name, rows: rows.slice(0, SHEET_ROWS).map((r) => r.slice(0, SHEET_COLS).map((c) => String(c ?? ""))), more: rows.length > SHEET_ROWS };
      });
      if (live) setSheets(out);
    })().catch((e) => live && setErr(`This spreadsheet couldn't be read: ${(e as Error).message}`));
    return () => { live = false; };
  }, [item.id]);
  if (err) return <NoPreview item={item} why={err} />;
  if (!sheets) return <Loading />;
  const s = sheets[tab] ?? sheets[0];
  const cols = Math.max(1, ...s.rows.map((r) => r.length));
  return (
    <div className="viewer-sheet">
      <div className="sheet-scroll">
        <table className="sheet-table">
          <thead><tr><th className="corner" />{Array.from({ length: cols }, (_, c) => <th key={c}>{colName(c)}</th>)}</tr></thead>
          <tbody>
            {s.rows.map((r, n) => (
              <tr key={n}><th>{n + 1}</th>{Array.from({ length: cols }, (_, c) => <td key={c}>{r[c] ?? ""}</td>)}</tr>
            ))}
          </tbody>
        </table>
        {s.more && <div className="muted small pad">Showing the first {SHEET_ROWS.toLocaleString()} rows. Download the file to see everything.</div>}
      </div>
      {sheets.length > 1 && (
        <div className="sheet-tabs">
          {sheets.map((x, n) => <button key={x.name} className={n === tab ? "active" : ""} onClick={() => setTab(n)}>{x.name}</button>)}
        </div>
      )}
    </div>
  );
}

const colName = (n: number): string => (n < 26 ? String.fromCharCode(65 + n) : colName(Math.floor(n / 26) - 1) + String.fromCharCode(65 + (n % 26)));

/** Word, PowerPoint and OpenDocument files: as PDF pages made by the server's converter; .docx in the browser when
 * the converter isn't there. */
function DocumentView({ item }: { item: Attachment }) {
  const [pdf, setPdf] = useState<ArrayBuffer | null>(null);
  const [fallback, setFallback] = useState<string | null>(null);  // why the converted preview isn't there
  const box = useRef<HTMLDivElement>(null);
  const [docxErr, setDocxErr] = useState("");
  const isDocx = useMemo(() => ["docx", "docm", "dotx"].includes(fileExt(item.name)), [item.name]);

  useEffect(() => {
    let live = true;
    if (!item.file) { setFallback("only-docx"); return; }
    fetchBytes(previewPdfUrl(item)).then((b) => live && setPdf(b)).catch((e) => live && setFallback((e as Error).message));
    return () => { live = false; };
  }, [item.id]);

  useEffect(() => {
    if (!fallback || !isDocx || !box.current) return;
    if (item.size > MAX_PARSE) { setDocxErr("This document is too large to show here."); return; }
    let live = true;
    (async () => {
      const [{ renderAsync }, bytes] = await Promise.all([import("docx-preview"), fetchBytes(attachmentUrl(item, true))]);
      if (!live || !box.current) return;
      await renderAsync(bytes, box.current, undefined, { inWrapper: true, ignoreLastRenderedPageBreak: true, experimental: false,
        renderHeaders: true, renderFooters: true, useBase64URL: true });
      // links inside a document can only go to web pages
      box.current.querySelectorAll("a").forEach((a) => {
        if (!/^(https?:|mailto:|#)/i.test(a.getAttribute("href") ?? "")) a.removeAttribute("href");
        a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener noreferrer");
      });
      box.current.querySelectorAll("script, iframe, object, embed").forEach((x) => x.remove());
    })().catch((e) => live && setDocxErr(`This document couldn't be shown: ${(e as Error).message}`));
    return () => { live = false; };
  }, [fallback, isDocx, item.id]);

  // desktop browsers: their own PDF viewer, from the address (cached); elsewhere pdf.js draws the pages just read
  if (pdf) return nativePdf() ? <Pdf src={previewPdfUrl(item)} name={item.name} /> : <Pdf data={pdf} name={item.name} />;
  if (!fallback) return <Loading text="Preparing the preview…" />;
  if (isDocx) return docxErr ? <NoPreview item={item} why={docxErr} /> : <div className="viewer-docx" ref={box}><Loading /></div>;
  return <NoPreview item={item} why={fallback === "only-docx" ? undefined : fallback} />;
}

function Loading({ text = "Loading…" }: { text?: string }) {
  return <div className="viewer-msg"><Icon name="sync" size={18} className="spin" /> {text}</div>;
}

function NoPreview({ item, why }: { item: Attachment; why?: string }) {
  const thumb = thumbUrl(item);
  return (
    <div className="viewer-msg none">
      {thumb ? <img className="viewer-cover" src={thumb} alt="" /> : <span className="viewer-ext big">{fileExt(item.name)}</span>}
      <b>{item.name}</b>
      <span>{why ?? "There's no preview for this kind of file."}</span>
      <a className="btn primary" href={attachmentUrl(item, true)}><Icon name="download" size={14} /> Download</a>
    </div>
  );
}

/** A small read-only preview (record panel), with a button for the full-screen viewer. */
export function FileView({ item, onExpand }: { item: Attachment; onExpand?: () => void }) {
  const kind = viewKind(item);
  const thumb = thumbUrl(item);
  return (
    <div className="file-card" onClick={onExpand} role={onExpand ? "button" : undefined}>
      {kind === "image" ? <img src={imageUrl(item)} alt={item.name} />
        : thumb ? <img src={thumb} alt={item.name} />
          : <span className="viewer-ext big">{fileExt(item.name)}</span>}
      {onExpand && <span className="file-card-open"><Icon name="expand" size={14} /> Open preview</span>}
    </div>
  );
}

/** Attachments in the full-screen viewer (from thumbnails anywhere). */
export function FilePreview({ items, index, onClose }: { items: Attachment[]; index: number; onClose: () => void }) {
  return <FullViewer entries={items.map((item) => ({ item }))} index={index} onClose={onClose} />;
}

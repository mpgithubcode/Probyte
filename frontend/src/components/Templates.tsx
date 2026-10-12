import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Icon } from "./Icon";

/** Templates: a table (fields, views, maybe records) or a view, as a JSON file that can be saved, downloaded,
 * shared and used to make new tables and views (see backend/app/templates.py). */

export interface TemplateInfo {
  id: string; kind: "table" | "view"; name: string; description: string; icon?: string | null; builtin: boolean;
  summary: { fields?: number; views?: number; records?: number; field_names?: string[]; type?: string };
}
export interface UploadedTemplate { body: Record<string, unknown>; kind: "table" | "view"; name: string; description: string; summary: TemplateInfo["summary"] }

export const listTemplates = (kind?: "table" | "view") => api.get<TemplateInfo[]>(`/templates${kind ? `?kind=${kind}` : ""}`);

/** Read a template file someone shared (checked by the server; nothing is stored). */
export async function readTemplateFile(file: File): Promise<UploadedTemplate> {
  const body = new FormData();
  body.append("file", file);
  return api.upload<UploadedTemplate>("/templates/upload", body);
}

/** Download a template of a table or view (made on the server, not saved). */
export async function downloadTemplate(what: { kind: "table" | "view"; table_id?: string; view_id?: string; include_records?: boolean }) {
  const res = await fetch("/api/templates/export", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(what) });
  if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail ?? res.statusText);
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "template.json";
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export const describe = (t: { summary: TemplateInfo["summary"]; kind: string }) => t.kind === "view"
  ? `${t.summary.type ?? ""} view`
  : [`${t.summary.fields} fields`, `${t.summary.views} views`, t.summary.records ? `${t.summary.records} sample records` : ""].filter(Boolean).join(" · ");

/** Pick a table template: built-in starters, your saved ones, or a file. */
export function TemplateGallery({ selected, onSelect }: {
  selected: { id?: string; uploaded?: UploadedTemplate } | null;
  onSelect: (t: { id?: string; uploaded?: UploadedTemplate; name: string; records: number }) => void;
}) {
  const [list, setList] = useState<TemplateInfo[] | null>(null);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => { listTemplates("table").then(setList).catch((e) => setErr(e.message)); }, []);
  const upload = async (f: File | undefined) => {
    if (!f) return;
    setErr("");
    try {
      const t = await readTemplateFile(f);
      if (t.kind !== "table") throw new Error("That's a view template: use it from a table's “+” next to its views");
      onSelect({ uploaded: t, name: t.name, records: t.summary.records ?? 0 });
    } catch (e) { setErr((e as Error).message); }
    if (fileRef.current) fileRef.current.value = "";
  };
  const mine = (list ?? []).filter((t) => !t.builtin), built = (list ?? []).filter((t) => t.builtin);
  const card = (t: TemplateInfo) => (
    <button key={t.id} type="button" className={`template-card ${selected?.id === t.id ? "active" : ""}`}
      onClick={() => onSelect({ id: t.id, name: t.name, records: t.summary.records ?? 0 })}>
      <span className="template-icon">{t.icon || "📄"}</span>
      <span className="template-text"><b>{t.name}</b><span className="muted small">{t.description || describe(t)}</span>
        {t.description && <span className="muted small">{describe(t)}</span>}</span>
    </button>
  );
  return (
    <div className="template-gallery">
      {list === null && !err && <div className="muted">Loading templates…</div>}
      {!!mine.length && <><div className="menu-heading">Your templates</div><div className="template-grid">{mine.map(card)}</div></>}
      {!!built.length && <><div className="menu-heading">Starters</div><div className="template-grid">{built.map(card)}</div></>}
      <input ref={fileRef} type="file" accept=".json,application/json" hidden onChange={(e) => upload(e.target.files?.[0])} />
      <div className="row">
        <button type="button" className="btn sm" onClick={() => fileRef.current?.click()}><Icon name="upload" size={14} /> Use a template file…</button>
        {selected?.uploaded && <span className="muted small">From the file: <b>{selected.uploaded.name}</b> ({describe(selected.uploaded)})</span>}
      </div>
      {err && <div className="error small">{err}</div>}
    </div>
  );
}

/** Save as template / download, for a table or a view (in their settings windows). */
export function TemplateActions({ kind, tableId, viewId, defaultName, records }: {
  kind: "table" | "view"; tableId?: string; viewId?: string; defaultName: string; records?: number;
}) {
  const [name, setName] = useState(defaultName);
  const [description, setDescription] = useState("");
  const [withRecords, setWithRecords] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const what = { kind, table_id: tableId, view_id: viewId, include_records: withRecords };
  const save = async () => {
    setErr(""); setMsg("");
    try {
      await api.post("/templates", { ...what, name: name.trim() || defaultName, description });
      setMsg(kind === "table" ? "Saved: it's offered under New table → From a template." : "Saved: it's offered in the “+” next to the view tabs.");
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="form">
      <p className="muted small">{kind === "table"
        ? "A template keeps this table's fields (with their options, rules and defaults) and views, and if you like its records. Links to other tables aren't included. Use it to make new tables, or download it to share."
        : "A template keeps this view's kind and settings: layout, hidden fields, filters, sorting and grouping. Used in another table, it's matched to that table's fields by name."}</p>
      <label className="stack">Template name<input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
      {kind === "table" && (<>
        <label className="stack">Description<input className="input" value={description} placeholder="What it's for" onChange={(e) => setDescription(e.target.value)} /></label>
        <label className="inline-check"><input type="checkbox" checked={withRecords} onChange={(e) => setWithRecords(e.target.checked)} />
          Include the records{records ? ` (${Math.min(records, 2000).toLocaleString()})` : ""} as sample data</label>
      </>)}
      <div className="settings-actions">
        <button className="btn sm primary" onClick={save}><Icon name="plus" size={14} /> Save as template</button>
        <button className="btn sm" onClick={() => downloadTemplate(what).catch((e) => setErr(e.message))}><Icon name="download" size={14} /> Download template file</button>
      </div>
      {msg && <div className="ok small">{msg}</div>}
      {err && <div className="error small">{err}</div>}
    </div>
  );
}

/** Settings → Templates: your saved templates and the starters; download, delete, add from a file. */
export function TemplatesPanel() {
  const [list, setList] = useState<TemplateInfo[] | null>(null);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const load = () => listTemplates().then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const add = async (f: File | undefined) => {
    if (!f) return;
    setErr("");
    try { const t = await readTemplateFile(f); await api.post("/templates", { body: t.body }); load(); } catch (e) { setErr((e as Error).message); }
    if (fileRef.current) fileRef.current.value = "";
  };
  const remove = async (t: TemplateInfo) => { try { await api.del(`/templates/${t.id}`); load(); } catch (e) { setErr((e as Error).message); } };
  return (
    <div className="templates-panel">
      <p className="muted small">Templates make new tables (New table → From a template) and new views (the “+” next to a table's views). Save one from a table's or view's settings, or add a template file someone shared.</p>
      {(list ?? []).map((t) => (
        <div key={t.id} className="account-row">
          <span className="template-icon sm">{t.icon || (t.kind === "view" ? "👁" : "📄")}</span>
          <div className="stack-tight">
            <b>{t.name} {t.builtin && <span className="badge">starter</span>}</b>
            <span className="muted small">{t.kind === "table" ? "Table" : "View"} · {describe(t)}</span>
          </div>
          <span className="spacer" />
          <a className="btn sm" href={`/api/templates/${t.id}/download`} title="Download the template file"><Icon name="download" size={14} /></a>
          {!t.builtin && <button className="icon-btn" title="Delete" onClick={() => remove(t)}><Icon name="trash" size={14} /></button>}
        </div>
      ))}
      <input ref={fileRef} type="file" accept=".json,application/json" hidden onChange={(e) => add(e.target.files?.[0])} />
      <button className="btn sm" onClick={() => fileRef.current?.click()}><Icon name="upload" size={14} /> Add a template file…</button>
      {err && <div className="error">{err}</div>}
    </div>
  );
}

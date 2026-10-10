import { useMemo, useState } from "react";
import { api } from "../api";
import type { Dataset, Field, FieldType, Refs, View } from "../types";
import { applyView, displayText, hiddenIds, isDateOnly, isEditable, keyOf, valueType } from "../util";
import { FieldIcon, Icon } from "./Icon";
import { Modal } from "./Overlay";

// SheetJS is only loaded when someone imports or exports.
const loadXLSX = () => import("xlsx");

// ------------------------------------------------------------------ export

const FORMATS = [
  { id: "xlsx", label: "Excel workbook (.xlsx)" },
  { id: "csv", label: "CSV (.csv)" },
  { id: "tsv", label: "Tab-separated (.tsv)" },
  { id: "ods", label: "OpenDocument spreadsheet (.ods)" },
  { id: "xls", label: "Excel 97–2003 (.xls)" },
  { id: "json", label: "JSON (.json)" },
] as const;
type Format = (typeof FORMATS)[number]["id"];

const pad = (n: number) => String(n).padStart(2, "0");
const localStamp = (d: Date) => `${keyOf(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** A cell for the file: numbers, booleans and dates stay typed where the format allows it. */
function exportCell(f: Field, v: unknown, refs: Refs, typed: boolean): unknown {
  if (v === null || v === undefined || v === "") return null;
  const vt = valueType(f);
  if (vt === "number" && typeof v === "number") return v;
  if (vt === "boolean" || f.type === "checkbox") return !!v;
  if (vt === "date" && typeof v === "string") {
    if (isDateOnly(v)) return typed ? new Date(`${v}T00:00:00`) : v;
    const d = new Date(v);
    return isNaN(+d) ? v : typed ? d : localStamp(d);
  }
  return displayText(f, v, refs);
}

export function ExportModal({ ds, view, search, onClose }: { ds: Dataset; view: View; search: string; onClose: () => void }) {
  const [format, setFormat] = useState<Format>("xlsx");
  const [allFields, setAllFields] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const hidden = hiddenIds(ds.fields, view.config);
  const fields = ds.fields.filter((f) => f.type !== "button" && (allFields || !hidden.has(f.id)));
  const records = useMemo(() => applyView(ds.records, ds.fields, view.config, search, ds.refs), [ds, view.config, search]);

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const base = `${ds.name} - ${view.name}`.replace(/[\\/:*?"<>|]+/g, "-");
      if (format === "json") {
        const rows = records.map((r) => Object.fromEntries(fields.map((f) => {
          const v = exportCell(f, r.data[f.id], ds.refs, false);
          return [f.name, v];
        })));
        download(new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" }), `${base}.json`);
      } else {
        const XLSX = await loadXLSX();
        const typed = format !== "csv" && format !== "tsv";
        const aoa = [fields.map((f) => f.name), ...records.map((r) => fields.map((f) => exportCell(f, r.data[f.id], ds.refs, typed)))];
        const ws = XLSX.utils.aoa_to_sheet(aoa, { cellDates: true });
        if (format === "csv" || format === "tsv") {
          const text = XLSX.utils.sheet_to_csv(ws, { FS: format === "tsv" ? "\t" : ",", blankrows: false });
          // A byte-order mark makes Excel read the file as UTF-8.
          download(new Blob(["﻿" + text], { type: format === "csv" ? "text/csv" : "text/tab-separated-values" }), `${base}.${format}`);
        } else {
          const wb = XLSX.utils.book_new();
          XLSX.utils.book_append_sheet(wb, ws, view.name.slice(0, 31).replace(/[\\/?*[\]:]/g, "-") || "Sheet1");
          XLSX.writeFile(wb, `${base}.${format}`, { bookType: format, cellDates: true });
        }
      }
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Export" onClose={onClose} width={460}
      footer={<><span className="spacer" /><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy} onClick={run}><Icon name="download" size={14} /> {busy ? "Exporting…" : "Export"}</button></>}>
      <div className="form">
        <label className="stack">Format
          <select className="input" value={format} onChange={(e) => setFormat(e.target.value as Format)}>
            {FORMATS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
        </label>
        <label className="inline-check">
          <input type="checkbox" checked={allFields} onChange={(e) => setAllFields(e.target.checked)} />
          Include fields hidden in this view
        </label>
        <p className="hint">{records.length} record{records.length === 1 ? "" : "s"} and {fields.length} field{fields.length === 1 ? "" : "s"}, using
          this view's filters, sort{search ? " and search" : ""}.</p>
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ------------------------------------------------------------------ import

type Cell = string | number | boolean | Date | null;
interface Sheet { name: string; rows: Cell[][] }

const NEW_TYPES: { type: FieldType; label: string }[] = [
  { type: "text", label: "Single line text" }, { type: "long_text", label: "Long text" }, { type: "number", label: "Number" },
  { type: "date", label: "Date" }, { type: "checkbox", label: "Checkbox" }, { type: "single_select", label: "Single select" },
  { type: "multi_select", label: "Multiple select" }, { type: "email", label: "Email" }, { type: "url", label: "URL" },
];

/** Where a column goes: an existing field, a new field of some type, or nowhere. */
type Target = { kind: "skip" } | { kind: "field"; id: string } | { kind: "new"; type: FieldType };

const blank = (c: Cell) => c === null || c === undefined || (typeof c === "string" && !c.trim());

function guessType(values: Cell[]): FieldType {
  const vals = values.filter((v) => !blank(v)).slice(0, 200);
  if (!vals.length) return "text";
  if (vals.every((v) => typeof v === "boolean" || /^(true|false|yes|no)$/i.test(String(v)))) return "checkbox";
  if (vals.every((v) => typeof v === "number" || (typeof v === "string" && /^-?[\d,]*\.?\d+$/.test(v.trim())))) return "number";
  if (vals.every((v) => v instanceof Date || (typeof v === "string" && /^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?/.test(v)))) return "date";
  if (vals.every((v) => typeof v === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim()))) return "email";
  if (vals.every((v) => typeof v === "string" && /^https?:\/\//i.test(v.trim()))) return "url";
  const distinct = new Set(vals.map((v) => String(v).trim().toLowerCase()));
  if (vals.length >= 6 && distinct.size <= Math.max(3, vals.length / 4) && distinct.size <= 20) return "single_select";
  if (vals.some((v) => String(v).length > 120 || String(v).includes("\n"))) return "long_text";
  return "text";
}

/** A cell as sent to the server: dates as record values, the rest as is. */
function sendCell(c: Cell): string | number | boolean | null {
  if (c instanceof Date) {
    if (isNaN(+c)) return null;
    const midnight = c.getHours() === 0 && c.getMinutes() === 0 && c.getSeconds() === 0;
    return midnight ? keyOf(c) : c.toISOString().replace(/\.\d{3}Z$/, "Z");
  }
  return c;
}

export function ImportModal({ ds, onClose, onDone }: { ds: Dataset; onClose: () => void; onDone: (msg: string) => void }) {
  const remote = ds.table?.kind === "remote";
  const [fileName, setFileName] = useState("");
  const [sheets, setSheets] = useState<Sheet[] | null>(null);
  const [sheetIdx, setSheetIdx] = useState(0);
  const [header, setHeader] = useState(true);
  const [targets, setTargets] = useState<Target[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const fillable = ds.fields.filter((f) => isEditable(f) && f.type !== "attachment" && f.type !== "button");

  const sheet = sheets?.[sheetIdx];
  const width = sheet ? Math.max(0, ...sheet.rows.slice(0, 500).map((r) => r.length)) : 0;
  const columns = useMemo(() => Array.from({ length: width }, (_, i) => {
    const name = header ? String(sheet!.rows[0]?.[i] ?? "").trim() || `Column ${i + 1}` : `Column ${i + 1}`;
    const values = (header ? sheet!.rows.slice(1) : sheet!.rows).map((r) => r[i] ?? null);
    return { name, values };
  }), [sheet, header, width]);
  const dataRows = sheet ? (header ? sheet.rows.slice(1) : sheet.rows).filter((r) => r.some((c) => !blank(c))) : [];

  // Columns whose name matches a field go there; the rest become new fields (or are skipped on linked tables).
  const autoMap = (cols: { name: string; values: Cell[] }[]): Target[] => {
    const used = new Set<string>();
    return cols.map((c) => {
      const f = fillable.find((x) => x.name.trim().toLowerCase() === c.name.toLowerCase() && !used.has(x.id));
      if (f) { used.add(f.id); return { kind: "field", id: f.id }; }
      // e.g. Record ID or a formula, from an earlier export: computed here, so leave it out
      if (ds.fields.some((x) => x.name.trim().toLowerCase() === c.name.toLowerCase())) return { kind: "skip" };
      return remote ? { kind: "skip" } : { kind: "new", type: guessType(c.values) };
    });
  };

  const readFile = async (file: File) => {
    setError("");
    setFileName(file.name);
    setBusy("Reading the file…");
    try {
      let parsed: Sheet[];
      if (/\.json$/i.test(file.name)) {
        const data = JSON.parse(await file.text());
        const list: Record<string, unknown>[] = Array.isArray(data) ? data : Array.isArray(data?.records) ? data.records : [];
        if (!list.length) throw new Error("The JSON file needs to be a list of objects");
        const keys = [...new Set(list.flatMap((o) => Object.keys(o ?? {})))];
        parsed = [{ name: "JSON", rows: [keys, ...list.map((o) => keys.map((k) => {
          const v = o?.[k];
          return v === undefined || v === null ? null : typeof v === "object" ? JSON.stringify(v) : (v as Cell);
        }))] }];
      } else {
        const XLSX = await loadXLSX();
        const wb = XLSX.read(await file.arrayBuffer(), { cellDates: true, dense: true });
        parsed = wb.SheetNames.map((n) => ({
          name: n,
          rows: XLSX.utils.sheet_to_json<Cell[]>(wb.Sheets[n], { header: 1, raw: true, defval: null, blankrows: false }),
        })).filter((s) => s.rows.length);
        if (!parsed.length) throw new Error("That file has no data");
      }
      setSheets(parsed);
      setSheetIdx(0);
      setHeader(true);
    } catch (e) {
      setSheets(null);
      setError(`Couldn't read ${file.name}: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  // Re-map whenever the columns change (new file, sheet or header setting).
  const colKey = columns.map((c) => c.name).join("\u0001");
  const [mappedFor, setMappedFor] = useState("");
  if (sheet && colKey !== mappedFor) { setMappedFor(colKey); setTargets(autoMap(columns)); }

  const usedFields = new Set(targets.flatMap((t) => (t.kind === "field" ? [t.id] : [])));
  const active = targets.filter((t) => t.kind !== "skip").length;

  const run = async () => {
    if (!sheet) return;
    setError("");
    const CHUNK = 1000;
    let sent: (string | null)[] | null = null;  // field ids after the first request created any new fields
    let created = 0, failed = 0;
    const firstErrors: string[] = [];
    try {
      for (let i = 0; i < dataRows.length; i += CHUNK) {
        setBusy(`Importing ${Math.min(i + CHUNK, dataRows.length)} of ${dataRows.length}…`);
        const cols = targets.map((t, j) => sent ? { target: sent[j] } :
          t.kind === "field" ? { target: t.id } : t.kind === "new" ? { new: { name: columns[j].name, type: t.type } } : { target: null });
        const rows = dataRows.slice(i, i + CHUNK).map((r) => targets.map((_, j) => sendCell(r[j] ?? null)));
        const res = await api.post<{ created: number; errors: string[]; error_count: number; targets: (string | null)[] }>(
          `/tables/${ds.id}/import`, { columns: cols, rows });
        sent = res.targets;
        created += res.created;
        failed += res.error_count;
        firstErrors.push(...res.errors.map((e) => e.replace(/^Row (\d+)/, (_, n) => `Row ${+n + i + (header ? 1 : 0)}`)));
      }
      onDone(`Imported ${created} record${created === 1 ? "" : "s"}${failed ? ` · ${failed} skipped (${firstErrors[0]})` : ""}`);
    } catch (e) {
      setError(`${(e as Error).message}${created ? ` (${created} records were imported before this)` : ""}`);
      if (created) onDone(`Imported ${created} records before an error`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal title="Import" width={720} onClose={onClose}
      footer={<>
        {sheet && <span className="muted small">{dataRows.length} rows · {active} of {columns.length} columns</span>}
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!sheet || !active || !dataRows.length || !!busy} onClick={run}>
          {busy ?? `Import ${dataRows.length} rows`}
        </button>
      </>}>
      <div className="form">
        <label className="file-drop">
          <input type="file" accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.ods,.numbers,.json"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) readFile(f); e.target.value = ""; }} />
          <Icon name="upload" size={18} />
          <span>{fileName ? <><b>{fileName}</b> · choose another file</> : <>Choose a file: <b>CSV, Excel, OpenDocument, Numbers, TSV or JSON</b></>}</span>
        </label>
        {busy && !sheet && <p className="muted">{busy}</p>}
        {sheet && (
          <>
            <div className="row import-opts">
              {sheets!.length > 1 && (
                <label className="tool-label">Sheet
                  <select className="input sm" value={sheetIdx} onChange={(e) => setSheetIdx(+e.target.value)}>
                    {sheets!.map((s, i) => <option key={s.name} value={i}>{s.name}</option>)}
                  </select>
                </label>
              )}
              <label className="inline-check"><input type="checkbox" checked={header} onChange={(e) => setHeader(e.target.checked)} /> First row has column names</label>
            </div>
            <div className="import-map">
              <div className="import-map-head"><span>Column in the file</span><span>Goes into</span></div>
              {columns.map((c, i) => {
                const t = targets[i] ?? { kind: "skip" };
                const value = t.kind === "field" ? `f:${t.id}` : t.kind === "new" ? "new" : "skip";
                const samples = c.values.filter((v) => !blank(v)).slice(0, 3).map((v) => (v instanceof Date ? String(sendCell(v)).length === 10 ? keyOf(v) : localStamp(v) : String(v)));
                return (
                  <div key={i} className={`import-map-row ${t.kind === "skip" ? "skipped" : ""}`}>
                    <div className="import-col">
                      <b>{c.name}</b>
                      <span className="muted small">{samples.length ? samples.join(" · ") : "empty"}</span>
                    </div>
                    <div className="import-target">
                      <select className="input sm" value={value} onChange={(e) => {
                        const v = e.target.value;
                        setTargets(targets.map((x, j) => j !== i ? x : v === "skip" ? { kind: "skip" } : v === "new" ? { kind: "new", type: guessType(c.values) }
                          : { kind: "field", id: v.slice(2) }));
                      }}>
                        <option value="skip">Don't import</option>
                        {!remote && <option value="new">+ New field “{c.name}”</option>}
                        <optgroup label="Existing fields">
                          {fillable.map((f) => (
                            <option key={f.id} value={`f:${f.id}`} disabled={usedFields.has(f.id) && value !== `f:${f.id}`}>{f.name}</option>
                          ))}
                        </optgroup>
                      </select>
                      {t.kind === "new" && (
                        <select className="input sm" value={t.type} aria-label="New field type"
                          onChange={(e) => setTargets(targets.map((x, j) => (j === i ? { kind: "new", type: e.target.value as FieldType } : x)))}>
                          {NEW_TYPES.map((n) => <option key={n.type} value={n.type}>{n.label}</option>)}
                        </select>
                      )}
                      {t.kind === "field" && (() => { const f = ds.fields.find((x) => x.id === t.id); return f && <span className="muted small import-type"><FieldIcon type={f.type} /> {f.type.replace("_", " ")}</span>; })()}
                    </div>
                  </div>
                );
              })}
            </div>
            <p className="hint">Values are converted to each field's type: numbers, dates, yes/no, select options (new options are added),
              linked records by their title and users by username. Rows are added to the table; nothing is overwritten.</p>
          </>
        )}
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}


import { useContext, useEffect, useMemo, useState } from "react";
import type { Field, Rec, Refs } from "../types";
import { displayText, isEditable } from "../util";
import { FieldInput, RefsContext } from "./Cells";
import { Icon } from "./Icon";
import { Popover } from "./Overlay";
import type { Ops } from "./viewTypes";

/** Records ticked in a view (cards, list rows): kept while they're still shown. */
export function useSelection(records: Rec[]) {
  const [sel, setSel] = useState<Set<string>>(new Set());
  useEffect(() => {
    setSel((s) => { if (!s.size) return s; const ids = new Set(records.map((r) => r.id)); const next = new Set([...s].filter((id) => ids.has(id))); return next.size === s.size ? s : next; });
  }, [records]);
  const selected = useMemo(() => records.filter((r) => sel.has(r.id)), [records, sel]);
  return {
    sel, selected,
    toggle: (id: string) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; }),
    setAll: (on: boolean) => setSel(on ? new Set(records.map((r) => r.id)) : new Set()),
    clear: () => setSel(new Set()),
  };
}

/** The tick box on a card or list row (shown on hover, and on all of them while any is ticked). */
export function SelectBox({ on, label, onToggle }: { on: boolean; label: string; onToggle: () => void }) {
  return (
    <input type="checkbox" className="select-box" checked={on} aria-label={`Select ${label}`}
      onClick={(e) => e.stopPropagation()} onChange={onToggle} />
  );
}

/** The selected records as a CSV file (the view's fields, as shown). */
export function downloadCsv(name: string, fields: Field[], recs: Rec[], refs?: Refs) {
  const cols = fields.filter((f) => f.type !== "button" && f.id !== "__source");
  const q = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const lines = [cols.map((f) => q(f.name)).join(","), ...recs.map((r) => cols.map((f) => q(displayText(f, r.data[f.id], refs))).join(","))];
  const url = URL.createObjectURL(new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = `${name.replace(/[\\/:*?"<>|]+/g, "-") || "records"}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** The bar for selected records in any view: set a field on all, duplicate, export, delete (each with Undo). */
export function SelectionBar({ selected, total, fields, shown, ops, name, onAll, onClear }: {
  selected: Rec[]; total: number; fields: Field[]; ops: Ops; name: string;
  shown: Field[];       // the view's visible fields (what Export writes)
  onAll?: () => void;   // select every record the view shows
  onClear: () => void;
}) {
  const { refs } = useContext(RefsContext);
  if (!selected.length || !ops.bulk) return null;
  return (
    <BulkBar count={selected.length} total={total} fields={fields.filter((f) => ops.can.edit && isEditable(f) && f.type !== "button")} can={ops.can}
      onUpdate={(patch) => ops.bulk!("update", selected, patch)}
      onDelete={() => { ops.bulk!("delete", selected); onClear(); }}
      onDuplicate={ops.duplicate && ops.can.add ? () => { ops.duplicate!(selected); onClear(); } : undefined}
      onExport={() => downloadCsv(`${name} (${selected.length} selected)`, shown, selected, refs)}
      onAll={onAll} onClear={onClear} />
  );
}

/** Actions for the selected rows: set a field on all of them, duplicate, export or delete them. */
export function BulkBar({ count, total, fields, can, onUpdate, onDelete, onDuplicate, onExport, onAll, onClear }: {
  count: number; total?: number; fields: Field[]; can: { edit: boolean; delete: boolean };
  onUpdate: (patch: Record<string, unknown>) => void; onDelete: () => void; onClear: () => void;
  onDuplicate?: () => void; onExport?: () => void; onAll?: () => void;
}) {
  const [editAnchor, setEditAnchor] = useState<HTMLElement | null>(null);
  const [fieldId, setFieldId] = useState("");
  const [value, setValue] = useState<unknown>(null);
  const [confirm, setConfirm] = useState(false);
  const field = fields.find((f) => f.id === fieldId);
  useEffect(() => setConfirm(false), [count]);
  return (
    <div className="bulk-bar" role="toolbar" aria-label="Selected rows">
      <b>{count.toLocaleString()} selected</b>
      {onAll && total !== undefined && count < total && <button className="btn sm ghost" onClick={onAll}>Select all {total.toLocaleString()}</button>}
      <span className="bulk-sep" />
      {can.edit && fields.length > 0 && (
        <button className="btn sm" onClick={(e) => { setEditAnchor(e.currentTarget); setFieldId(fields[0].id); setValue(null); }}>
          <Icon name="edit" size={14} /> Edit
        </button>
      )}
      {onDuplicate && <button className="btn sm" onClick={onDuplicate}><Icon name="copy" size={14} /> Duplicate</button>}
      {onExport && <button className="btn sm" onClick={onExport}><Icon name="download" size={14} /> Export CSV</button>}
      {can.delete && (confirm ? (
        <button className="btn sm danger" onClick={onDelete}><Icon name="trash" size={14} /> Delete {count}?</button>
      ) : (
        <button className="btn sm" onClick={() => setConfirm(true)}><Icon name="trash" size={14} /> Delete</button>
      ))}
      <span className="spacer" />
      <button className="icon-btn" onClick={onClear} aria-label="Clear selection"><Icon name="x" size={16} /></button>
      {editAnchor && (
        <Popover anchor={editAnchor} onClose={() => setEditAnchor(null)} width={320}>
          <div className="pop-body bulk-edit">
            <label className="stack">Field
              <select className="input sm" value={fieldId} onChange={(e) => { setFieldId(e.target.value); setValue(null); }}>
                {fields.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
              </select>
            </label>
            {field && (
              <label className="stack">New value
                <FieldInput key={field.id} field={field} value={value} onCommit={setValue} />
              </label>
            )}
            <p className="muted small">Sets this value on all {count} selected records. Leave it empty to clear the field.</p>
            <div className="row">
              <span className="spacer" />
              <button className="btn sm" onClick={() => setEditAnchor(null)}>Cancel</button>
              <button className="btn sm primary" disabled={!field} onClick={() => { onUpdate({ [field!.id]: value ?? null }); setEditAnchor(null); }}>
                Apply to {count}
              </button>
            </div>
          </div>
        </Popover>
      )}
    </div>
  );
}

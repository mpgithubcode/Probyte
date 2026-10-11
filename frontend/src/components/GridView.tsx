import { useContext, useEffect, useMemo, useRef, useState } from "react";
import type { Field, Rec } from "../types";
import { buildGroups, fitField, isEditable, orderFields, recordKind, setFieldShown, viewGroups, viewSorts, type GroupNode } from "../util";
import { isTouch } from "./useNarrow";
import { CellValue, Chip, FieldInput, RefsContext } from "./Cells";
import { anchorOf } from "../recur";
import { FieldIcon, Icon } from "./Icon";
import { Popover } from "./Overlay";
import { useVirtualRows } from "./useVirtualRows";
import type { ViewProps } from "./viewTypes";

const ROW_H = 36;    // keep in sync with .grid td height
const HEADER_H = 36;
const WIDTHS: Record<string, number> = {
  long_text: 260, recurrence: 200, checkbox: 90, rating: 120, date: 180, multi_select: 220, link: 220, lookup: 220, attachment: 160,
  autonumber: 90, button: 130, created_time: 180, last_modified_time: 180, formula: 200, record_id: 300,
};

type Row = { kind: "group"; g: GroupNode } | { kind: "rec"; r: Rec; n: number };

export function GridView({ ds, view, fields, visible, records, ops, setConfig }: ViewProps) {
  const canEdit = (f: Field) => ops.can.edit && isEditable(f);
  const { refs } = useContext(RefsContext);
  const [editing, setEditing] = useState<{ rec: string; field: string } | null>(null);
  const [menu, setMenu] = useState<{ field: Field; anchor: HTMLElement } | null>(null);
  // dragging a column header to a new place (mouse only): the order is saved with the view
  const [dragCol, setDragCol] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<string | null>(null);
  const moveColumn = (from: string, to: string) => {
    if (from === to) return;
    const ids = orderFields(fields, view.config.fieldOrder).map((f) => f.id);
    const without = ids.filter((id) => id !== from);
    const at = without.indexOf(to) + (ids.indexOf(from) < ids.indexOf(to) ? 1 : 0);  // dropped on a column: take its place
    without.splice(at, 0, from);
    setConfig({ fieldOrder: without });
  };

  // ---- grouping
  const groupSpecs = viewGroups(view.config);
  const groupKey = groupSpecs.map((g) => `${g.field}:${g.dir}:${g.hideTitle ? 1 : 0}${g.noArrow ? 1 : 0}`).join(",");
  const groupedBy = new Set(groupSpecs.map((g) => g.field));
  const groups = useMemo(() => (groupSpecs.length ? buildGroups(records, groupSpecs, fields, refs) : null), [records, groupKey, fields, refs]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  useEffect(() => setCollapsed(new Set()), [groupKey]);
  const rows = useMemo<Row[]>(() => {
    if (!groups) return records.map((r, i) => ({ kind: "rec", r, n: i + 1 }));
    const out: Row[] = [];
    const walk = (nodes: GroupNode[]) => {
      for (const g of nodes) {
        if (!g.spec.hideTitle) out.push({ kind: "group", g });
        if (!g.spec.hideTitle && !g.spec.noArrow && collapsed.has(g.path)) continue;
        if (g.children) walk(g.children);
        else g.records.forEach((r, i) => out.push({ kind: "rec", r, n: i + 1 }));
      }
    };
    walk(groups);
    return out;
  }, [groups, records, collapsed]);
  const win = useVirtualRows(rows.length, ROW_H, HEADER_H);

  // ---- selection
  const selectable = !!ops.bulk && (ops.can.edit || ops.can.delete);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const lastClicked = useRef<string | null>(null);
  useEffect(() => {  // forget records that left the view
    setSel((s) => { const ids = new Set(records.map((r) => r.id)); const next = new Set([...s].filter((id) => ids.has(id))); return next.size === s.size ? s : next; });
  }, [records]);
  const selected = useMemo(() => records.filter((r) => sel.has(r.id)), [records, sel]);
  const allOn = records.length > 0 && sel.size === records.length;
  const toggle = (r: Rec, shift: boolean) => setSel((s) => {
    const next = new Set(s);
    const order = rows.flatMap((x) => (x.kind === "rec" ? [x.r.id] : []));
    if (shift && lastClicked.current && order.includes(lastClicked.current)) {
      const [a, b] = [order.indexOf(lastClicked.current), order.indexOf(r.id)].sort((x, y) => x - y);
      const on = !s.has(r.id);
      for (const id of order.slice(a, b + 1)) { if (on) next.add(id); else next.delete(id); }
    } else if (next.has(r.id)) next.delete(r.id); else next.add(r.id);
    lastClicked.current = r.id;
    return next;
  });
  const setMany = (recs: Rec[], on: boolean) => setSel((s) => {
    const next = new Set(s);
    for (const r of recs) { if (on) next.add(r.id); else next.delete(r.id); }
    return next;
  });

  // An event's Completed box is calculated (ticked once it's over), so it can't be clicked.
  const componentF = fields.find((x) => x.system_key === "component");
  const eventDone = (f: Field, r: Rec) => f.system_key === "done"
    && (ds.table?.kind === "events" || (!!componentF && r.data[componentF.id] === "Event"));
  const cell = (r: Rec, f: Field) => {
    const isEditing = editing?.rec === r.id && editing.field === f.id;
    const readonly = !canEdit(f);
    if (f.type === "button") {
      return (
        <td key={f.id} className="cell center">
          <button className="cell-button" style={{ background: f.options.color ?? "var(--accent)" }} disabled={!ops.can.edit && f.options.action !== "url"}
            onClick={(e) => { e.stopPropagation(); ops.runButton(r, f); }}>{f.options.label ?? "Click"}</button>
        </td>
      );
    }
    if (f.type === "checkbox") {
      return (
        <td key={f.id} className="cell center">
          <input type="checkbox" className="checkbox" checked={!!r.data[f.id]} disabled={readonly || eventDone(f, r)}
            title={eventDone(f, r) ? "Events are completed once they're over" : undefined}
            onChange={(e) => ops.update(r, { [f.id]: e.target.checked })} />
        </td>
      );
    }
    return (
      <td key={f.id} className={`cell ${isEditing ? "editing" : ""} ${readonly ? "readonly" : ""}`}
        onClick={() => !readonly && !isEditing && setEditing({ rec: r.id, field: f.id })}>
        {isEditing ? (
          <FieldInput field={fitField(f, recordKind(fields, r), r.data[f.id])} value={r.data[f.id]} inline autoFocus anchor={f.type === "recurrence" ? anchorOf(fields, r) : undefined}
            onCommit={(v) => ops.update(r, { [f.id]: v })}
            onDone={() => setEditing((e) => (e?.rec === r.id && e.field === f.id ? null : e))} />
        ) : (
          <div className="cell-content"><CellValue field={f} value={r.data[f.id]} /></div>
        )}
      </td>
    );
  };

  const groupRow = (g: GroupNode) => {
    const open = !collapsed.has(g.path);
    const allIn = g.records.every((r) => sel.has(r.id));
    // a new record here gets this group's value (and its parent groups' values)
    const sample = g.records[0];
    const canAddHere = ops.can.add && !g.empty && groupSpecs.slice(0, g.depth + 1).every((s) => {
      const f = fields.find((x) => x.id === s.field);
      return f && isEditable(f);
    });
    const preset = sample ? Object.fromEntries(groupSpecs.slice(0, g.depth + 1).map((s) => [s.field, sample.data[s.field]])) : {};
    return (
      <tr key={`g:${g.path}`} className={`group-row depth-${g.depth}`}>
        <td className="rownum">
          {selectable && (
            <input type="checkbox" className="checkbox row-check" checked={allIn} title="Select this group"
              onChange={(e) => setMany(g.records, e.target.checked)} />
          )}
        </td>
        <td colSpan={visible.length + 1}>
          <div className="group-head" style={{ paddingLeft: 10 + g.depth * 22 }}>
            <button className={`group-toggle ${g.spec.noArrow ? "static" : ""}`} disabled={g.spec.noArrow}
              onClick={() => !g.spec.noArrow && setCollapsed((c) => { const n = new Set(c); if (open) n.add(g.path); else n.delete(g.path); return n; })}
              aria-expanded={g.spec.noArrow ? undefined : open}>
              {!g.spec.noArrow && <span className={`chev ${open ? "open" : ""}`}><Icon name="right" size={12} /></span>}
              <span className="muted small">{g.field.name}</span>
              {g.color ? <Chip name={g.label} color={g.color} /> : <b>{g.label}</b>}
              <span className="group-count">{g.records.length}</span>
            </button>
            {canAddHere && (
              <button className="icon-btn group-add" title={`New record in ${g.label}`} onClick={() => ops.create(preset, true)}>
                <Icon name="plus" size={13} />
              </button>
            )}
          </div>
        </td>
      </tr>
    );
  };

  const sorts = viewSorts(view.config);
  return (
    <div className={`grid-wrap ${sel.size ? "selecting" : ""}`} ref={win.ref}>
      <table className="grid">
        <thead>
          <tr>
            <th className="rownum">
              {selectable && records.length > 0 && (
                <input type="checkbox" className="checkbox row-check all" checked={allOn} title={allOn ? "Clear selection" : "Select all"}
                  ref={(el) => { if (el) el.indeterminate = sel.size > 0 && !allOn; }}
                  onChange={() => setSel(allOn ? new Set() : new Set(records.map((r) => r.id)))} />
              )}
            </th>
            {visible.map((f) => (
              <th key={f.id} style={{ width: WIDTHS[f.type] ?? 200 }} title={f.options._error ?? undefined}
                className={`${dragCol === f.id ? "col-dragging" : ""} ${overCol === f.id && dragCol && dragCol !== f.id
                  ? (visible.findIndex((x) => x.id === dragCol) < visible.findIndex((x) => x.id === f.id) ? "col-over-after" : "col-over") : ""}`}
                draggable={!f.is_primary && !isTouch}
                onDragStart={(e) => { setDragCol(f.id); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", f.id); }}
                onDragOver={(e) => { if (dragCol && !f.is_primary) { e.preventDefault(); setOverCol(f.id); } }}
                onDragEnd={() => { setDragCol(null); setOverCol(null); }}
                onDrop={(e) => { e.preventDefault(); if (dragCol) moveColumn(dragCol, f.id); setDragCol(null); setOverCol(null); }}
                onClick={(e) => setMenu({ field: f, anchor: e.currentTarget })}>
                <div className="th-inner">
                  <FieldIcon type={f.type} />
                  <span className="th-name">{f.name}</span>
                  {f.system_key && f.system_key !== "remote" && f.system_key !== "record_id" && <span title="Synced with the server (locked)"><Icon name="lock" size={11} className="th-lock" /></span>}
                  {f.options._error && <span className="th-warn" title={f.options._error}>!</span>}
                  {sorts.map((s, i) => s.field === f.id && (
                    <span key={i} className="muted" title={`Sort ${i + 1}`}>{s.dir === "asc" ? "↑" : "↓"}{sorts.length > 1 ? i + 1 : ""}</span>))}
                  {groupedBy.has(f.id) && <Icon name="group" size={12} className="muted" />}
                </div>
              </th>
            ))}
            <th className="add-col">
              {ds.kind === "table" && ops.can.manage && <button className="icon-btn" title="Add field" onClick={() => ops.editField(null)}><Icon name="plus" /></button>}
            </th>
          </tr>
        </thead>
        <tbody>
          {win.padTop > 0 && <tr className="spacer" style={{ height: win.padTop }}><td colSpan={visible.length + 2} /></tr>}
          {rows.slice(win.start, win.end).map((row) => {
            if (row.kind === "group") return groupRow(row.g);
            const r = row.r;
            const on = sel.has(r.id);
            return (
              <tr key={r.id} className={on ? "selected" : ""}>
                <td className="rownum" onClick={() => ops.open(r)} title="Open record">
                  <span className="stripe" style={{ background: ops.colorFor(r) ?? "transparent" }} />
                  {selectable && (
                    <input type="checkbox" className="checkbox row-check" checked={on} aria-label="Select row"
                      onClick={(e) => { e.stopPropagation(); toggle(r, e.shiftKey); }} onChange={() => undefined} />
                  )}
                  <span className="num">{row.n}</span>
                  <button className="expand" title="Open record" onClick={(e) => { e.stopPropagation(); ops.open(r); }}><Icon name="expand" size={13} /></button>
                  {r.sync_status === "dirty" && <span className="dirty-dot" title="Not yet synced to CalDAV" />}
                </td>
                {visible.map((f) => cell(r, f))}
                <td />
              </tr>
            );
          })}
          {win.padBottom > 0 && <tr className="spacer" style={{ height: win.padBottom }}><td colSpan={visible.length + 2} /></tr>}
          {ops.can.add && (
            <tr className="add-row">
              <td colSpan={visible.length + 2}>
                <button className="ghost-btn" onClick={() => ops.create()}><Icon name="plus" size={14} /> New record</button>
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {sel.size > 0 && ops.bulk && (
        <BulkBar count={sel.size} fields={fields.filter((f) => canEdit(f) && f.type !== "button")} can={ops.can}
          onUpdate={(patch) => ops.bulk!("update", selected, patch)}
          onDelete={() => { ops.bulk!("delete", selected); setSel(new Set()); }}
          onClear={() => setSel(new Set())} />
      )}

      {menu && (
        <Popover anchor={menu.anchor} onClose={() => setMenu(null)} width={200}>
          <div className="menu-list">
            {ds.kind === "table" && ops.can.manage && (
              <button className="menu-item" onClick={() => { ops.editField(menu.field); setMenu(null); }}>
                <Icon name="edit" size={14} /> Edit field
              </button>
            )}
            <button className="menu-item" onClick={() => { setConfig({ sorts: [{ field: menu.field.id, dir: "asc" }], sort: undefined }); setMenu(null); }}>
              <Icon name="sort" size={14} /> Sort ascending
            </button>
            <button className="menu-item" onClick={() => { setConfig({ sorts: [{ field: menu.field.id, dir: "desc" }], sort: undefined }); setMenu(null); }}>
              <Icon name="sort" size={14} /> Sort descending
            </button>
            {(groupedBy.has(menu.field.id) || groupSpecs.length < 3) && (
              <button className="menu-item" onClick={() => {
                setConfig({ groupBy: undefined, groups: groupedBy.has(menu.field.id) ? groupSpecs.filter((s) => s.field !== menu.field.id)
                  : [...groupSpecs, { field: menu.field.id, dir: "asc" }] });
                setMenu(null);
              }}>
                <Icon name="group" size={14} /> {groupedBy.has(menu.field.id) ? "Stop grouping by this field" : groupSpecs.length ? "Add as subgroup" : "Group by this field"}
              </button>
            )}
            {!menu.field.is_primary && (
              <button className="menu-item" onClick={() => {
                setConfig(setFieldShown(menu.field, false, view.config));
                setMenu(null);
              }}>
                <Icon name="eye" size={14} /> Hide field
              </button>
            )}
          </div>
        </Popover>
      )}
    </div>
  );
}

/** Actions for the selected rows: set a field on all of them, or delete them. */
function BulkBar({ count, fields, can, onUpdate, onDelete, onClear }: {
  count: number; fields: Field[]; can: { edit: boolean; delete: boolean };
  onUpdate: (patch: Record<string, unknown>) => void; onDelete: () => void; onClear: () => void;
}) {
  const [editAnchor, setEditAnchor] = useState<HTMLElement | null>(null);
  const [fieldId, setFieldId] = useState("");
  const [value, setValue] = useState<unknown>(null);
  const [confirm, setConfirm] = useState(false);
  const field = fields.find((f) => f.id === fieldId);
  useEffect(() => setConfirm(false), [count]);
  return (
    <div className="bulk-bar" role="toolbar" aria-label="Selected rows">
      <b>{count} selected</b>
      {can.edit && fields.length > 0 && (
        <button className="btn sm" onClick={(e) => { setEditAnchor(e.currentTarget); setFieldId(fields[0].id); setValue(null); }}>
          <Icon name="edit" size={14} /> Edit
        </button>
      )}
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

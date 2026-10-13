import { useState } from "react";
import type { Field, Rec } from "../types";
import { isEditable, recordTitle } from "../util";
import { CellValue, Chip } from "./Cells";
import { Icon } from "./Icon";
import { Popover } from "./Overlay";
import { isTouch } from "./useNarrow";
import { CARD_FIELDS, densityOf, type ViewProps } from "./viewTypes";
import { SelectBox, SelectionBar, useSelection } from "./BulkBar";

/** A card's title and its first few filled-in fields, one line each ("Folder  Photos"); how many follows the view's density. */
export function CardBody({ rec, fields, skip, max = CARD_FIELDS.comfortable }: { rec: Rec; fields: Field[]; skip?: string[]; max?: number }) {
  const title = recordTitle(fields, rec);
  const rest = fields.filter((f) => !f.is_primary && !skip?.includes(f.id) && f.id !== "__source" && f.type !== "button" && f.type !== "record_id")
    .filter((f) => { const v = rec.data[f.id]; return v !== null && v !== undefined && v !== "" && v !== false && !(Array.isArray(v) && !v.length); })
    .slice(0, max);
  return (
    <>
      <div className="card-title">{title}</div>
      {rest.length > 0 && (
        <dl className="card-fields">
          {rest.map((f) => (
            <div key={f.id} className={`card-field ${f.type === "long_text" ? "wide" : ""}`}>
              <dt className="card-label">{f.name}</dt>
              <dd className="card-value"><CellValue field={f} value={rec.data[f.id]} /></dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

const PAGE = 50; // cards rendered per column before "Show more"

export function KanbanView({ ds, fields, visible, records, view, ops, setConfig }: ViewProps) {
  const canEdit = (f: Field) => ops.can.edit && isEditable(f);
  const groupField = fields.find((f) => f.id === view.config.groupBy && f.type === "single_select");
  const [dragId, setDragId] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [limits, setLimits] = useState<Record<string, number>>({});
  const [moveMenu, setMoveMenu] = useState<{ rec: Rec; anchor: HTMLElement } | null>(null);
  const selection = useSelection(records);
  const selectable = !!ops.bulk && (ops.can.edit || ops.can.delete);

  if (!groupField) {
    const options = fields.filter((f) => f.type === "single_select");
    return (
      <div className="empty-state">
        <p>Choose a single select field to group cards by.</p>
        <select className="input" style={{ width: 240 }} value="" onChange={(e) => setConfig({ groupBy: e.target.value })}>
          <option value="" disabled>Group by…</option>
          {options.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
        {!options.length && <p className="muted">This database has no single select fields yet.</p>}
      </div>
    );
  }

  const columns: { name: string | null; color: string }[] = [
    { name: null, color: "#94a3b8" },
    ...(groupField.options.choices ?? []).map((c) => ({ name: c.name, color: c.color })),
  ];
  const known = new Set(columns.map((c) => c.name));
  const keyOf = (r: Rec) => { const v = r.data[groupField.id]; return v && known.has(v) ? v : null; };
  const canDrag = canEdit(groupField);
  const blank = (r: Rec) => [...fields.filter((f) => f.is_primary), ...visible].every((f) => {
    if (f.id === groupField.id || f.id === "__source" || f.computed) return true;  // the column and calculated values don't count
    const v = r.data[f.id];
    return v === null || v === undefined || v === "" || v === false || (Array.isArray(v) && !v.length);
  });
  const shown = view.config.hideBlankCards ? records.filter((r) => !blank(r)) : records;

  return (
    <div className={`kanban density-${densityOf(view.config)} ${selection.sel.size ? "selecting" : ""}`}>
      <SelectionBar selected={selection.selected} total={shown.length} fields={fields} shown={visible} ops={ops} name={ds.name}
        onAll={() => selection.setAll(true)} onClear={selection.clear} />
      {columns.map((col) => {
        const items = shown.filter((r) => keyOf(r) === col.name);
        if (col.name === null && (!items.length || view.config.hideUnassigned)) return null;
        if (view.config.hideEmptyColumns && !items.length) return null;
        const colKey = col.name ?? "__none";
        return (
          <div key={colKey} data-col={colKey} className={`kanban-col ${over === colKey ? "over" : ""}`}
            onDragOver={(e) => { if (canDrag && dragId) { e.preventDefault(); setOver(colKey); } }}
            onDragLeave={() => setOver((o) => (o === colKey ? null : o))}
            onDrop={(e) => {
              e.preventDefault();
              const rec = records.find((r) => r.id === dragId);
              if (rec && keyOf(rec) !== col.name) ops.update(rec, { [groupField.id]: col.name });
              setDragId(null); setOver(null);
            }}>
            <div className="kanban-head">
              {col.name ? <Chip name={col.name} color={col.color} /> : <span className="muted">No {groupField.name}</span>}
              <span className="count">{items.length}</span>
            </div>
            <div className="kanban-cards">
              {items.slice(0, limits[colKey] ?? PAGE).map((r) => (
                <div key={r.id} className={`card ${dragId === r.id ? "dragging" : ""} ${selection.sel.has(r.id) ? "selected" : ""}`} draggable={canDrag && !isTouch}
                  style={{ borderLeftColor: ops.colorFor(r) ?? "transparent" }}
                  onDragStart={(e) => { setDragId(r.id); e.dataTransfer.effectAllowed = "move"; }}
                  onDragEnd={() => { setDragId(null); setOver(null); }}
                  onClick={() => (selection.sel.size && isTouch ? selection.toggle(r.id) : ops.open(r))}
                  onContextMenu={selectable ? (e) => { e.preventDefault(); selection.toggle(r.id); } : undefined}>
                  {isTouch && canDrag && (
                    <button className="card-move" title="Move to…" aria-label="Move to another column"
                      onClick={(e) => { e.stopPropagation(); setMoveMenu({ rec: r, anchor: e.currentTarget }); }}>
                      <Icon name="kanban" size={14} />
                    </button>
                  )}
                  {selectable && <SelectBox on={selection.sel.has(r.id)} label={recordTitle(fields, r)} onToggle={() => selection.toggle(r.id)} />}
                  <CardBody rec={r} fields={visible} skip={[groupField.id]} max={CARD_FIELDS[densityOf(view.config)]} />
                </div>
              ))}
              {items.length > (limits[colKey] ?? PAGE) && (
                <button className="ghost-btn" onClick={() => setLimits((l) => ({ ...l, [colKey]: (l[colKey] ?? PAGE) + PAGE * 4 }))}>
                  Show {Math.min(items.length - (limits[colKey] ?? PAGE), PAGE * 4)} more
                </button>
              )}
            </div>
            {ops.can.add && isEditable(groupField) && (
              <button className="ghost-btn" onClick={() => ops.create({ [groupField.id]: col.name }, true)}>
                <Icon name="plus" size={14} /> New
              </button>
            )}
          </div>
        );
      })}
      {moveMenu && (
        <Popover anchor={moveMenu.anchor} onClose={() => setMoveMenu(null)} width={220}>
          <div className="menu-heading">Move to</div>
          <div className="menu-list">
            {columns.map((c) => (
              <button key={c.name ?? "__none"} className={`menu-item ${keyOf(moveMenu.rec) === c.name ? "active" : ""}`}
                onClick={() => { if (keyOf(moveMenu.rec) !== c.name) ops.update(moveMenu.rec, { [groupField.id]: c.name }); setMoveMenu(null); }}>
                {c.name ? <Chip name={c.name} color={c.color} /> : <span className="muted">No {groupField.name}</span>}
                {keyOf(moveMenu.rec) === c.name && <span className="tick">✓</span>}
              </button>
            ))}
          </div>
        </Popover>
      )}
    </div>
  );
}

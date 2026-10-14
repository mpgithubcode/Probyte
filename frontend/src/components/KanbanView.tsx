import { useState } from "react";
import type { Field, Rec } from "../types";
import { isEditable, recordTitle } from "../util";
import { CellValue, Chip } from "./Cells";
import { Icon } from "./Icon";
import { Popover } from "./Overlay";
import { isTouch } from "./useNarrow";
import { densityOf, type ViewProps } from "./viewTypes";
import { SelectBox, SelectionBar, useSelection } from "./BulkBar";

/** A card's title and every filled-in visible field, one row each ("Folder  Photos"); long values wrap, so the card grows
 * downwards (max: a limit, for small previews). */
export function CardBody({ rec, fields, skip, max }: { rec: Rec; fields: Field[]; skip?: string[]; max?: number }) {
  const title = recordTitle(fields, rec);
  const rest = fields.filter((f) => !f.is_primary && !skip?.includes(f.id) && f.id !== "__source" && f.type !== "button" && f.type !== "record_id")
    .filter((f) => { const v = rec.data[f.id]; return v !== null && v !== undefined && v !== "" && v !== false && !(Array.isArray(v) && !v.length); })
    .slice(0, max ?? Infinity);
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
  const [dragCol, setDragCol] = useState<string | null>(null);   // a column being dragged by its header
  const [colMenu, setColMenu] = useState<{ key: string; anchor: HTMLElement } | null>(null);
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

  // the columns in the view's order (dragged by their headers), then any option added since
  const all: { name: string | null; color: string }[] = [
    { name: null, color: "#94a3b8" },
    ...(groupField.options.choices ?? []).map((c) => ({ name: c.name, color: c.color })),
  ];
  const keyOfCol = (c: { name: string | null }) => c.name ?? "__none";
  const order = view.config.columnOrder ?? [];
  const pos = (c: { name: string | null; color: string }) => { const i = order.indexOf(keyOfCol(c)); return i < 0 ? order.length + all.indexOf(c) : i; };
  const columns = [...all].sort((a, b) => pos(a) - pos(b));
  const moveColumn = (key: string, to: number) => {
    const keys = columns.map(keyOfCol).filter((k) => k !== key);
    keys.splice(Math.max(0, Math.min(to, keys.length)), 0, key);
    setConfig({ columnOrder: keys });
  };
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
          <div key={colKey} data-col={colKey} className={`kanban-col ${over === colKey ? "over" : ""} ${dragCol === colKey ? "dragging" : ""}`}
            onDragOver={(e) => {
              if (dragCol) { e.preventDefault(); if (dragCol !== colKey) moveColumn(dragCol, columns.findIndex((c) => keyOfCol(c) === colKey)); return; }
              if (canDrag && dragId) { e.preventDefault(); setOver(colKey); }
            }}
            onDragLeave={() => setOver((o) => (o === colKey ? null : o))}
            onDrop={(e) => {
              e.preventDefault();
              if (dragCol) { setDragCol(null); return; }
              const rec = records.find((r) => r.id === dragId);
              if (rec && keyOf(rec) !== col.name) ops.update(rec, { [groupField.id]: col.name });
              setDragId(null); setOver(null);
            }}>
            <div className="kanban-head" draggable={!isTouch} title={isTouch ? undefined : "Drag to move the column"}
              onDragStart={(e) => { setDragCol(colKey); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", colKey); }}
              onDragEnd={() => setDragCol(null)}>
              {col.name ? <Chip name={col.name} color={col.color} /> : <span className="muted">No {groupField.name}</span>}
              <span className="count">{items.length}</span>
              <button className="icon-btn kanban-col-menu" aria-label={`Move the ${col.name ?? "No " + groupField.name} column`} title="Move column"
                onClick={(e) => setColMenu({ key: colKey, anchor: e.currentTarget })}><Icon name="more" size={14} /></button>
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
                  <CardBody rec={r} fields={visible} skip={[groupField.id]} />
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
      {colMenu && (() => {
        const i = columns.findIndex((c) => keyOfCol(c) === colMenu.key);
        return (
          <Popover anchor={colMenu.anchor} onClose={() => setColMenu(null)} width={200}>
            <div className="menu-list">
              <button className="menu-item" disabled={i <= 0} onClick={() => { moveColumn(colMenu.key, i - 1); setColMenu(null); }}><Icon name="left" size={14} /> Move left</button>
              <button className="menu-item" disabled={i >= columns.length - 1} onClick={() => { moveColumn(colMenu.key, i + 1); setColMenu(null); }}><Icon name="right" size={14} /> Move right</button>
              <button className="menu-item" disabled={i <= 0} onClick={() => { moveColumn(colMenu.key, 0); setColMenu(null); }}>Move to the start</button>
              <button className="menu-item" disabled={i >= columns.length - 1} onClick={() => { moveColumn(colMenu.key, columns.length); setColMenu(null); }}>Move to the end</button>
              {view.config.columnOrder && <button className="menu-item" onClick={() => { setConfig({ columnOrder: undefined }); setColMenu(null); }}>Back to the options' order</button>}
            </div>
          </Popover>
        );
      })()}
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

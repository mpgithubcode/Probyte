import { useContext, useMemo, useState } from "react";
import type { Field, ListAlign, Rec, ViewConfig } from "../types";
import { buildGroups, isEditable, recordTitle, viewGroups, type GroupNode } from "../util";
import { CellValue, Chip, RefsContext } from "./Cells";
import { Icon } from "./Icon";
import { useVirtualRows } from "./useVirtualRows";
import type { ViewProps } from "./viewTypes";

const isSet = (v: unknown) => v !== null && v !== undefined && v !== "" && v !== false && !(Array.isArray(v) && !v.length);

export interface ListProp { field: Field; align: ListAlign; label: boolean }

/** Roughly how wide a property is (value + its name when shown); used to decide which properties fit. */
function propWidth(p: ListProp): number {
  const f = p.field;
  const base: Record<string, number> = { checkbox: 18, number: 60, rating: 80, autonumber: 40, date: f.options.includeTime ? 150 : 95,
    created_time: 150, last_modified_time: 150, single_select: 95, multi_select: 130, link: 130, user: 110, url: 150, email: 150,
    recurrence: 150, formula: 100, lookup: 130, rollup: 80 };
  return (base[f.type] ?? 130) + (p.label ? Math.min(f.name.length * 7 + 6, 100) : 0);
}

/** The list's properties in order: arranged ones first, then other visible fields on the right (the Completed box last). */
export function listLayout(visible: Field[], config: ViewConfig): ListProp[] {
  const usable = visible.filter((f) => !f.is_primary && f.id !== "__source" && f.type !== "button");
  const names = config.listLabels !== false;
  const placed = (config.listProps ?? []).flatMap((p) => {
    const f = usable.find((x) => x.id === p.field);
    return f ? [{ field: f, align: p.align, label: p.label ?? names }] : [];
  });
  const rest = usable.filter((f) => !placed.some((p) => p.field.id === f.id) && f.type !== "long_text")
    .map((f) => ({ field: f, align: "right" as const, label: names }));
  return [...placed, ...rest.filter((p) => p.field.system_key !== "done"), ...rest.filter((p) => p.field.system_key === "done")];
}

const ZONES: ListAlign[] = ["start", "left", "center", "right"];

/**
 * One line per record. The whole list is one grid whose columns every row shares (CSS subgrid), so each
 * property's column is as wide as its widest value and everything lines up straight at any width:
 *   dot | start props | title | left props | (space) | center props | (space) | right props
 * Properties that don't fit are left out from the end of each side; a value that still doesn't fit ends in "…".
 */
export function ListView({ ds, fields, visible, records, view, ops }: ViewProps) {
  const { refs } = useContext(RefsContext);
  const specs = viewGroups(view.config);
  const specKey = specs.map((g) => `${g.field}:${g.dir}:${g.hideTitle ? 1 : 0}${g.noArrow ? 1 : 0}`).join(",");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // rows sit indented under their group headers (depth = how many headers are above them)
  const rows = useMemo<({ g: GroupNode } | { r: Rec; depth: number })[]>(() => {
    if (!specs.length) return records.map((r) => ({ r, depth: 0 }));
    const out: ({ g: GroupNode } | { r: Rec; depth: number })[] = [];
    const walk = (nodes: GroupNode[], depth: number) => nodes.forEach((g) => {
      if (!g.spec.hideTitle) out.push({ g });
      if (!g.spec.hideTitle && !g.spec.noArrow && collapsed.has(g.path)) return;
      const inner = depth + (g.spec.hideTitle ? 0 : 1);
      if (g.children) walk(g.children, inner); else g.records.forEach((r) => out.push({ r, depth: inner }));
    });
    walk(buildGroups(records, specs, fields, refs), 0);
    return out;
  }, [records, specKey, fields, refs, collapsed]);
  const win = useVirtualRows(rows.length, 42, 12);  // 42px rows (.list-row, .list-group), 12px top padding
  const width = win.width;
  const narrow = width > 0 && width < 760;
  const maxDepth = Math.max(0, ...rows.map((x) => ("r" in x ? x.depth : 0)));

  // Which properties fit: each side fills its room in order and stops at the first that doesn't fit, so columns
  // drop off from the end as the list narrows. The Completed box (wherever it is) gets its room first.
  const all = listLayout(visible, view.config);
  const shown = useMemo(() => {
    if (!width) return all;
    const gap = narrow ? 10 : 16;
    const need = (p: ListProp) => propWidth(p) + gap;
    const keep = new Set<string>();
    let room = width - 40 - 28 - maxDepth * 22 - (narrow ? 100 : 170);  // padding, dot (+ indent), the title
    const take = (items: ListProp[]) => {
      for (const p of [...items.filter((x) => x.field.system_key === "done"), ...items.filter((x) => x.field.system_key !== "done")]) {
        if (need(p) > room) break;
        keep.add(p.field.id);
        room -= need(p);
      }
    };
    for (const z of ZONES) take(all.filter((p) => p.align === z));
    return all.filter((p) => keep.has(p.field.id));
  }, [all.map((p) => `${p.field.id}:${p.align}:${p.label}`).join(), width, narrow, maxDepth]);
  const zone = (z: ListAlign) => shown.filter((p) => p.align === z);
  const hasCenter = zone("center").length > 0;
  // columns: dot, start props, title, left props, flexible space, [center props, flexible space,] right props
  const prop = "minmax(0, max-content)";
  const cols = ["max-content", ...zone("start").map(() => prop), `minmax(${narrow ? 70 : 90}px, max-content)`, ...zone("left").map(() => prop),
    "minmax(0, 1fr)", ...(hasCenter ? [...zone("center").map(() => prop), "minmax(0, 1fr)"] : []), ...zone("right").map(() => prop)];

  // An event's Completed box is calculated (ticked once it's over), so it can't be clicked.
  const componentF = fields.find((x) => x.system_key === "component");
  const eventDone = (f: Field, r: Rec) => f.system_key === "done"
    && (ds.table?.kind === "events" || (!!componentF && r.data[componentF.id] === "Event"));
  const cell = (p: ListProp, r: Rec) => {
    const f = p.field, v = r.data[f.id];
    const box = f.type === "checkbox";
    return (
      <span key={f.id} className={`list-prop ${p.align} ${box ? "check" : ""}`} title={f.name}>
        {p.label && (isSet(v) || box) && <span className="list-prop-name">{f.name}</span>}
        {box ? (  // true/false fields are boxes, ticked or not, and can be ticked right here
          <input type="checkbox" className="checkbox" checked={!!v} aria-label={f.name}
            disabled={!ops.can.edit || !isEditable(f) || eventDone(f, r)}
            onClick={(e) => e.stopPropagation()} onChange={(e) => ops.update(r, { [f.id]: e.target.checked })} />
        ) : isSet(v) && <span className="list-prop-value"><CellValue field={f} value={v} /></span>}
      </span>
    );
  };

  return (
    <div className="list-view" ref={win.ref}>
      <div className="list-grid" style={{ gridTemplateColumns: cols.join(" ") }}>
        {win.padTop > 0 && <div className="list-span" style={{ height: win.padTop }} />}
        {rows.slice(win.start, win.end).map((row) => {
          if ("g" in row) {
            const g = row.g, open = !collapsed.has(g.path), arrow = !g.spec.noArrow;
            return (
              <button key={`g:${g.path}`} className={`list-group list-span group-toggle ${arrow ? "" : "static"}`} aria-expanded={arrow ? open : undefined}
                style={{ paddingLeft: 8 + g.depth * 22 }} disabled={!arrow}
                onClick={() => arrow && setCollapsed((c) => { const n = new Set(c); if (open) n.add(g.path); else n.delete(g.path); return n; })}>
                {g.depth > 0 && <span className="muted small">{g.field.name}</span>}
                {arrow && <span className={`chev ${open ? "open" : ""}`}><Icon name="right" size={12} /></span>}
                {g.color ? <Chip name={g.label} color={g.color} /> : <b>{g.label}</b>}
                <span className="group-count">{g.records.length}</span>
              </button>
            );
          }
          const r = row.r;
          const color = ops.colorFor(r, view.config.colorField);
          return (
            <div key={r.id} className="list-row" onClick={() => ops.open(r)}>
              <span className="list-start" style={{ paddingLeft: row.depth * 22 }}>
                <span className="list-dot" style={{ background: color ?? "var(--border-strong)" }} />
              </span>
              {zone("start").map((p) => cell(p, r))}
              <span className="list-title">
                <span className="list-title-text">{recordTitle(fields, r)}</span>
                {r.sync_status === "dirty" && !ds.dav?.builtin && <span className="dirty-dot static" title="Not yet synced to CalDAV" />}
              </span>
              {zone("left").map((p) => cell(p, r))}
              <span />
              {hasCenter && <>{zone("center").map((p) => cell(p, r))}<span /></>}
              {zone("right").map((p) => cell(p, r))}
            </div>
          );
        })}
        {win.padBottom > 0 && <div className="list-span" style={{ height: win.padBottom }} />}
      </div>
      {!records.length && <div className="muted pad">No records match this view.</div>}
      {ops.can.add && <button className="ghost-btn list-new" onClick={() => ops.create({}, true)}><Icon name={ops.addLabel ? "upload" : "plus"} size={14} /> {ops.addLabel ?? "New"}</button>}
    </div>
  );
}

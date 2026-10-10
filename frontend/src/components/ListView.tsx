import { useContext, useMemo, useState } from "react";
import type { Field, Rec, ViewConfig } from "../types";
import { buildGroups, recordTitle, viewGroups, type GroupNode } from "../util";
import { CellValue, Chip, RefsContext } from "./Cells";
import { Icon } from "./Icon";
import { useVirtualRows } from "./useVirtualRows";
import type { ViewProps } from "./viewTypes";

const isSet = (v: unknown) => v !== null && v !== undefined && v !== "" && v !== false && !(Array.isArray(v) && !v.length);

/** Column width for a property in the list (value + its name when names are shown). */
function propWidth(f: Field, labels: boolean): number {
  const base: Record<string, number> = { checkbox: 34, number: 80, rating: 90, autonumber: 60, date: f.options.includeTime ? 170 : 110,
    created_time: 170, last_modified_time: 170, single_select: 120, multi_select: 150, link: 150, user: 130, url: 170, email: 170,
    recurrence: 170, formula: 120, lookup: 150, rollup: 100 };
  const name = labels ? Math.min(f.name.length * 7 + 8, 96) : 0;
  return (base[f.type] ?? 150) + name;
}

/** The list's properties in order, each with its zone: arranged ones first, then other visible fields on the right. */
export function listLayout(visible: Field[], config: ViewConfig): { field: Field; align: "left" | "center" | "right" }[] {
  const usable = visible.filter((f) => !f.is_primary && f.id !== "__source" && f.type !== "button");
  const placed = (config.listProps ?? []).flatMap((p) => { const f = usable.find((x) => x.id === p.field); return f ? [{ field: f, align: p.align }] : []; });
  const rest = usable.filter((f) => !placed.some((p) => p.field.id === f.id) && f.type !== "long_text").map((f) => ({ field: f, align: "right" as const }));
  return [...placed, ...rest];
}

/** Minimal one-line-per-record layout: title on the left, a few properties on the right. */
export function ListView({ fields, visible, records, view, ops }: ViewProps) {
  const props = listLayout(visible, view.config);
  const labels = view.config.listLabels !== false;
  const zones = (["left", "center", "right"] as const).map((zone) => {
    const items = props.filter((p) => p.align === zone);
    // fixed column widths: the same on every row, so properties line up straight down the list
    return { zone, items, cols: items.map((p) => `minmax(${Math.min(80, propWidth(p.field, labels))}px, ${propWidth(p.field, labels)}px)`).join(" ") };
  });
  // one zone of a row: fixed columns (the same on every row), so values line up straight down the list
  const zone = (z: (typeof zones)[number], r: Rec) => (
    <span className={`list-zone ${z.zone}`} style={{ gridTemplateColumns: z.cols || undefined }}>
      {z.items.map(({ field: f }) => (
        <span key={f.id} className="list-prop" title={isSet(r.data[f.id]) ? f.name : undefined}>
          {isSet(r.data[f.id]) && <>
            {labels && <span className="list-prop-name">{f.name}</span>}
            <span className="list-prop-value"><CellValue field={f} value={r.data[f.id]} /></span>
          </>}
        </span>
      ))}
    </span>
  );
  const { refs } = useContext(RefsContext);
  const specs = viewGroups(view.config);
  const specKey = specs.map((g) => `${g.field}:${g.dir}:${g.hideTitle ? 1 : 0}${g.noArrow ? 1 : 0}`).join(",");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const rows = useMemo<({ g: GroupNode } | { r: Rec })[]>(() => {
    if (!specs.length) return records.map((r) => ({ r }));
    const out: ({ g: GroupNode } | { r: Rec })[] = [];
    const walk = (nodes: GroupNode[]) => nodes.forEach((g) => {
      if (!g.spec.hideTitle) out.push({ g });
      if (!g.spec.hideTitle && !g.spec.noArrow && collapsed.has(g.path)) return;
      if (g.children) walk(g.children); else g.records.forEach((r) => out.push({ r }));
    });
    walk(buildGroups(records, specs, fields, refs));
    return out;
  }, [records, specKey, fields, refs, collapsed]);
  const win = useVirtualRows(rows.length, 42, 12);  // 42px rows (.list-row, .list-group), 12px top padding
  return (
    <div className="list-view" ref={win.ref}>
      {win.padTop > 0 && <div style={{ height: win.padTop, flexShrink: 0 }} />}
      {rows.slice(win.start, win.end).map((row) => {
        if ("g" in row) {
          const g = row.g, open = !collapsed.has(g.path), arrow = !g.spec.noArrow;
          return (
            <button key={`g:${g.path}`} className={`list-group group-toggle ${arrow ? "" : "static"}`} aria-expanded={arrow ? open : undefined}
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
          <div key={r.id} className={`list-row ${zones[1].items.length ? "has-center" : ""}`} onClick={() => ops.open(r)}>
            <span className="list-start">
              <span className="list-dot" style={{ background: color ?? "var(--border-strong)" }} />
              <span className="list-title">{recordTitle(fields, r)}</span>
              <span className="list-dirty">{r.sync_status === "dirty" && <span className="dirty-dot static" title="Not yet synced to CalDAV" />}</span>
              {zone(zones[0], r)}
            </span>
            {zones[1].items.length > 0 && zone(zones[1], r)}
            {zone(zones[2], r)}
          </div>
        );
      })}
      {win.padBottom > 0 && <div style={{ height: win.padBottom, flexShrink: 0 }} />}
      {!records.length && <div className="muted pad">No records match this view.</div>}
      {ops.can.add && <button className="ghost-btn list-new" onClick={() => ops.create({}, true)}><Icon name="plus" size={14} /> New</button>}
    </div>
  );
}

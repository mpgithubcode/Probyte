import { useContext, useMemo, useState } from "react";
import type { Field, Rec, ViewConfig } from "../types";
import { buildGroups, isEditable, recordTitle, viewGroups, type GroupNode } from "../util";
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
  // the Completed box sits at the far right unless it was placed somewhere
  const done = rest.filter((p) => p.field.system_key === "done");
  return [...placed, ...rest.filter((p) => p.field.system_key !== "done"), ...done];
}

/** Minimal one-line-per-record layout: title on the left, a few properties on the right. */
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
  const labels = view.config.listLabels !== false && !narrow;
  // Show the properties that fit at a readable width, in order (the Completed box first); the rest are left out
  // whole rather than squeezing every column into a sliver. Room for the title is kept.
  const all = listLayout(visible, view.config);
  const props = useMemo(() => {
    if (!width) return all;
    const gap = narrow ? 6 : 12;
    const need = (p: (typeof all)[number]) => propWidth(p.field, labels) + gap;
    const indent = 22 * Math.max(0, ...rows.map((x) => ("r" in x ? x.depth : 0)));
    const title = narrow ? 110 : 180;
    const inner = width - 40 - 24 - indent;  // padding, dot + gaps, group indent
    const keep = new Set<string>();
    // each zone fills its own room, in order, the Completed box first; what doesn't fit is left out
    const fill = (zone: "left" | "center" | "right", room: number) => {
      const items = all.filter((p) => p.align === zone);
      for (const p of [...items.filter((x) => x.field.system_key === "done"), ...items.filter((x) => x.field.system_key !== "done")]) {
        if (need(p) > room) break;  // columns drop off from the end as the list gets narrower
        keep.add(p.field.id);
        room -= need(p);
      }
      return room;
    };
    if (all.some((p) => p.align === "center")) {
      // the center sits in the middle: both sides get the same room
      const before = keep.size;
      fill("center", inner * 0.4);
      const centerUsed = all.filter((p) => p.align === "center" && keep.has(p.field.id)).reduce((n, p) => n + need(p), 0) + (keep.size > before ? 28 : 0);
      const side = (inner - centerUsed) / 2;
      fill("left", side - title);
      fill("right", side);
    } else {
      fill("right", fill("left", inner - title));  // left first, the right side takes what's left
    }
    return all.filter((p) => keep.has(p.field.id));
  }, [all.map((p) => `${p.field.id}:${p.align}`).join(), width, labels, narrow, rows]);
  const zones = (["left", "center", "right"] as const).map((zone) => {
    const items = props.filter((p) => p.align === zone);
    // the same column widths on every row, so properties line up straight down the list; they shrink together
    // (never below zero) when the row is narrow, instead of running off the edge
    return { zone, items, cols: items.map((p) => `minmax(0, ${propWidth(p.field, labels)}px)`).join(" ") };
  });
  // An event's Completed box is calculated (ticked once it's over), so it can't be clicked.
  const componentF = fields.find((x) => x.system_key === "component");
  const eventDone = (f: Field, r: Rec) => f.system_key === "done"
    && (ds.table?.kind === "events" || (!!componentF && r.data[componentF.id] === "Event"));
  const value = (f: Field, r: Rec) => {
    if (f.type === "checkbox") {  // true/false fields are boxes, ticked or not, and can be ticked right here
      const locked = !ops.can.edit || !isEditable(f) || eventDone(f, r);
      return (
        <span className="list-prop-value">
          <input type="checkbox" className="checkbox" checked={!!r.data[f.id]} disabled={locked} aria-label={f.name}
            onClick={(e) => e.stopPropagation()} onChange={(e) => ops.update(r, { [f.id]: e.target.checked })} />
        </span>
      );
    }
    return isSet(r.data[f.id]) ? <span className="list-prop-value"><CellValue field={f} value={r.data[f.id]} /></span> : null;
  };
  // one zone of a row: fixed columns (the same on every row), so values line up straight down the list
  const zone = (z: (typeof zones)[number], r: Rec) => (
    <span className={`list-zone ${z.zone}`} style={{ gridTemplateColumns: z.cols || undefined }}>
      {z.items.map(({ field: f }) => (
        <span key={f.id} className={`list-prop ${f.type === "checkbox" ? "check" : ""}`} title={f.name}>
          {(isSet(r.data[f.id]) || f.type === "checkbox") && labels && <span className="list-prop-name">{f.name}</span>}
          {value(f, r)}
        </span>
      ))}
    </span>
  );
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
          <div key={r.id} className={`list-row ${zones[1].items.length ? "has-center" : ""}`} onClick={() => ops.open(r)}
            style={row.depth ? { paddingLeft: 10 + row.depth * 22 } : undefined}>
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

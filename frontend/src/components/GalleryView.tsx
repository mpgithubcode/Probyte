import { useContext, useState } from "react";
import type { Attachment, Rec } from "../types";
import { buildGroups, recordTitle, viewGroups, type GroupNode } from "../util";
import { Chip, RefsContext } from "./Cells";
import { fileExt, previewStatus, thumbUrl } from "../files";
import { Icon } from "./Icon";
import { isTouch } from "./useNarrow";
import { CardBody } from "./KanbanView";
import { densityOf, type ViewProps } from "./viewTypes";
import { SelectBox, SelectionBar, useSelection } from "./BulkBar";

const looksLikeImage = (v: unknown): v is string => typeof v === "string" && /^(https?:|data:image\/)/i.test(v.trim());

/** Cover image URL: the first attachment / file with a preview image, or a URL/text value. */
function coverSrc(v: unknown): string | null {
  if (Array.isArray(v)) {
    for (const a of v as Attachment[]) {
      const src = a && typeof a === "object" ? thumbUrl(a) : null;
      if (src) return src;
    }
    return null;
  }
  return looksLikeImage(v) ? v : null;
}

const PAGE = 120; // cards rendered before "Show more"

export function GalleryView({ ds, fields, visible, records, view, ops }: ViewProps) {
  const [limit, setLimit] = useState(PAGE);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const { refs } = useContext(RefsContext);
  const coverField = fields.find((f) => f.id === view.config.coverField);
  // Only show cover areas when at least one card actually has an image.
  const cover = coverField && records.some((r) => coverSrc(r.data[coverField.id])) ? coverField : undefined;
  const specs = viewGroups(view.config);
  const density = densityOf(view.config);
  const selection = useSelection(records);
  const selectable = !!ops.bulk && (ops.can.edit || ops.can.delete);
  const bar = <SelectionBar selected={selection.selected} total={records.length} fields={fields} shown={visible} ops={ops} name={ds.name}
    onAll={() => selection.setAll(true)} onClear={selection.clear} />;

  const card = (r: Rec) => {
    const src = cover ? coverSrc(r.data[cover.id]) : null;
    return (
      <div key={r.id} className={`card gallery-card ${selection.sel.has(r.id) ? "selected" : ""}`} style={{ borderTopColor: ops.colorFor(r) ?? "transparent" }}
        onClick={() => (selection.sel.size && isTouch ? selection.toggle(r.id) : ops.open(r))}
        onContextMenu={selectable ? (e) => { e.preventDefault(); selection.toggle(r.id); } : undefined}>
        {selectable && <SelectBox on={selection.sel.has(r.id)} label={recordTitle(fields, r)} onToggle={() => selection.toggle(r.id)} />}
        {cover && (
          <div className="gallery-cover">
            {src ? <img src={src} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.display = "none"; }}
                className={Array.isArray(r.data[cover.id]) && !/^image\//.test(r.data[cover.id][0]?.mime ?? "image/") ? "doc" : undefined} />
              : Array.isArray(r.data[cover.id]) && r.data[cover.id][0]?.name
                ? <span className="cover-missing"><span className="file-ext big">{fileExt(r.data[cover.id][0].name)}</span>
                    {previewStatus(r.data[cover.id][0]) && <span className={`cover-status ${previewStatus(r.data[cover.id][0])!.state}`}>
                      {previewStatus(r.data[cover.id][0])!.text}</span>}</span> : null}
          </div>
        )}
        <CardBody rec={r} fields={visible} skip={cover ? [cover.id] : undefined} />
      </div>
    );
  };
  const more = (list: Rec[]) => list.length > limit && (
    <button className="card gallery-card add" onClick={() => setLimit((l) => l + PAGE)}>
      Show {Math.min(list.length - limit, PAGE)} more of {list.length - limit}
    </button>
  );

  const section = (g: GroupNode): JSX.Element => {
    const arrow = !g.spec.hideTitle && !g.spec.noArrow;
    const open = !arrow || !collapsed.has(g.path);
    const body = g.children ? g.children.map(section) : <div className="gallery">{g.records.slice(0, limit).map(card)}{more(g.records)}</div>;
    if (g.spec.hideTitle) return <section key={g.path} className="gallery-group untitled">{body}</section>;
    return (
      <section key={g.path} className="gallery-group" style={{ paddingLeft: g.depth ? 18 : 0 }}>
        <button className={`group-toggle gallery-group-head ${arrow ? "" : "static"}`} aria-expanded={arrow ? open : undefined} disabled={!arrow}
          onClick={() => arrow && setCollapsed((c) => { const n = new Set(c); if (open) n.add(g.path); else n.delete(g.path); return n; })}>
          {arrow && <span className={`chev ${open ? "open" : ""}`}><Icon name="right" size={12} /></span>}
          {g.depth > 0 && <span className="muted small">{g.field.name}</span>}
          {g.color ? <Chip name={g.label} color={g.color} /> : <b>{g.label}</b>}
          <span className="group-count">{g.records.length}</span>
        </button>
        {open && body}
      </section>
    );
  };

  if (specs.length) {
    return (
      <div className={`gallery-groups density-${density} ${selection.sel.size ? "selecting" : ""}`}>
        {bar}
        {buildGroups(records, specs, fields, refs).map(section)}
        {ops.can.add && (
          <button className="ghost-btn" onClick={() => ops.create({}, true)}><Icon name={ops.addLabel ? "upload" : "plus"} size={14} /> {ops.addLabel ?? "New record"}</button>
        )}
      </div>
    );
  }

  return (
    <div className={`gallery density-${density} ${selection.sel.size ? "selecting" : ""}`}>
      {bar}
      {records.slice(0, limit).map(card)}
      {more(records)}
      {ops.can.add && (
        <button className="card gallery-card add" onClick={() => ops.create({}, true)}>
          <Icon name={ops.addLabel ? "upload" : "plus"} /> {ops.addLabel ?? "New record"}
        </button>
      )}
    </div>
  );
}

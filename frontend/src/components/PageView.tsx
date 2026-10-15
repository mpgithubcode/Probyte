import { Suspense, lazy, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Dataset, Field, FieldView, PageBlock, PageLayout, Rec } from "../types";
import { anchorOf } from "../recur";
import { BUILTIN_KEYS, fieldApplies, fitField, isEditable, primaryField, recordKind, recordTitle } from "../util";
import { FieldInput } from "./Cells";
import { FieldIcon, Icon } from "./Icon";
import { Popover, usePopover } from "./Overlay";
import { RecordHistory } from "./RecordHistory";
import { RecordValue, VIEW_LABEL, viewsFor } from "./LinkedView";
import type { Can } from "./viewTypes";
import type { RecordNav } from "./RecordModal";
import { showToast } from "../toast";

const PageDoc = lazy(() => import("./PageDoc"));
const EMBEDS = /^\{\{field:([\w-]+)\}\}$/gm;

let seq = 0;
const newId = () => `b${Date.now().toString(36)}${(seq++).toString(36)}`;

/** The page a table's records get when it has none laid out yet: their fields, then the first long text as the body. */
export function defaultBlocks(fields: Field[]): PageBlock[] {
  const body = fields.find((f) => f.type === "long_text" && !f.system_key);
  return [{ id: "props", type: "properties" }, ...(body ? [{ id: "body", type: "doc" as const, field: body.id }] : [])];
}

/** A record as a page (like Notion's): its title large, then blocks: its fields (each shown its own way: a link as a
 * table or cards…), headings, dividers, and a long text field as the page's body, with fields placed in its text.
 * The layout is the table's; whoever may change the table's fields can change it ("Edit page"). */
export function PageView({ ds, rec, fields, can, canDesign, onUpdate, onDelete, onClose, onRecordView, onLayout, onButton, nav, link }: {
  ds: Dataset; rec: Rec; fields: Field[]; can: Can;
  canDesign: boolean;                                  // may change the layout
  onUpdate: (patch: Record<string, any>) => void; onDelete: () => void; onClose: () => void;
  onRecordView: () => void;                            // show the record panel instead
  onLayout: (page: PageLayout) => void;                // a changed layout (saved by the caller)
  onButton: (f: Field) => void;
  nav?: RecordNav; link?: string;
}) {
  const page = ds.table?.page;
  const [designing, setDesigning] = useState(false);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => setConfirm(false), [rec.id]);
  const kind = recordKind(fields, rec);
  const usable = fields.filter((f) => f.id !== "__source" && f.type !== "record_id" && !BUILTIN_KEYS.has(f.system_key ?? "") && fieldApplies(f, kind));
  const primary = primaryField(fields);
  const blocks = page?.blocks.length ? page.blocks : defaultBlocks(usable);
  const save = (b: PageBlock[]) => onLayout({ enabled: page?.enabled ?? false, blocks: b });

  // fields placed somewhere (a block of their own, or in a page body's text) aren't repeated in the properties
  const placed = useMemo(() => {
    const s = new Set<string>(primary ? [primary.id] : []);
    for (const b of blocks) {
      if (b.field) s.add(b.field);
      if (b.type === "doc" && b.field) for (const m of String(rec.data[b.field] ?? "").matchAll(EMBEDS)) s.add(m[1]);
    }
    return s;
  }, [blocks, rec.data, primary]);
  const rest = usable.filter((f) => !placed.has(f.id));

  // J / K and Escape, unless typing somewhere or something is open over the page
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable=true], .popover") || document.querySelector(".backdrop, .popover")) return;
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      if (nav && (e.key === "j" || e.key === "J")) { e.preventDefault(); nav.next?.(); }
      if (nav && (e.key === "k" || e.key === "K")) { e.preventDefault(); nav.prev?.(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [nav, onClose]);

  const value = (f: Field, view?: FieldView): ReactNode => {
    if (f.type === "button") {
      return <button className="cell-button" style={{ background: f.options.color ?? "var(--accent)" }}
        disabled={!can.edit && f.options.action !== "url"} onClick={() => onButton(f)}>{f.options.label ?? "Click"}</button>;
    }
    if (f.mapping && rec.table_id && !f.mapping[rec.table_id]) return <span className="muted">Not mapped for this source</span>;
    return <RecordValue field={fitField(f, kind, rec.data[f.id])} value={rec.data[f.id]} rec={rec} canEdit={can.edit} onUpdate={onUpdate} view={view}
      anchor={f.type === "recurrence" ? anchorOf(fields, rec) : undefined} />;
  };
  const labelled = (f: Field, view?: FieldView) => (
    <div className="page-field">
      <span className="page-label" title={f.description ?? undefined}><FieldIcon type={f.type} /> {f.name}{f.required && <span className="req-mark" title="Required"> *</span>}</span>
      <div className="page-value">{value(f, view)}</div>
    </div>
  );

  const move = (i: number, d: -1 | 1) => { const j = i + d; if (j < 0 || j >= blocks.length) return; const a = [...blocks]; [a[i], a[j]] = [a[j], a[i]]; save(a); };
  const patch = (i: number, p: Partial<PageBlock>) => save(blocks.map((b, j) => (j === i ? { ...b, ...p } : b)));
  const remove = (i: number) => save(blocks.filter((_, j) => j !== i));
  const add = (b: Omit<PageBlock, "id">) => save([...blocks, { id: newId(), ...b }]);

  const block = (b: PageBlock, i: number): ReactNode => {
    const f = b.field ? usable.find((x) => x.id === b.field) : undefined;
    let body: ReactNode;
    if (b.type === "properties") {
      body = rest.length ? <div className="page-props">{rest.map((x) => <div key={x.id}>{labelled(x)}</div>)}</div>
        : designing ? <div className="muted small">Every field has a place of its own on this page.</div> : null;
    } else if (b.type === "heading") {
      body = designing ? <input className="input page-heading-input" value={b.text ?? ""} placeholder="Heading" aria-label="Heading"
        onChange={(e) => patch(i, { text: e.target.value })} /> : b.text ? <h2 className="page-heading">{b.text}</h2> : null;
    } else if (b.type === "divider") {
      body = <hr className="page-divider" />;
    } else if (!f) {
      body = designing ? <div className="muted small">A field that's no longer in this table</div> : null;
    } else if (b.type === "doc") {
      const editable = can.edit && isEditable(f);
      body = (
        <div className="page-body-block">
          <Suspense fallback={<div className="muted">Loading…</div>}>
            <PageDoc key={`${rec.id}:${f.id}`} value={String(rec.data[f.id] ?? "")} editable={editable}
              onChange={(md) => onUpdate({ [f.id]: md.trim() ? md : null })}
              fields={usable.filter((x) => x.id !== f.id && x.id !== primary?.id && x.type !== "long_text")}
              renderField={(id) => { const ef = usable.find((x) => x.id === id); return ef ? labelled(ef) : <span className="muted small">A field that's no longer here</span>; }} />
          </Suspense>
          {!editable && !rec.data[f.id] && <p className="muted">Nothing written yet.</p>}
        </div>
      );
    } else {
      body = labelled(f, b.view);
    }
    const views = f && b.type === "field" ? viewsFor(f, "record") : [];
    return (
      <div key={b.id} className={`page-block pb-${b.type} ${b.width === "half" ? "half" : ""} ${designing ? "designing" : ""}`}>
        {designing && (
          <div className="page-block-tools">
            <span className="page-block-kind">{b.type === "properties" ? "Other fields" : b.type === "doc" ? `Page body: ${f?.name ?? "?"}` : b.type === "field" ? f?.name ?? "?" : b.type === "heading" ? "Heading" : "Divider"}</span>
            {views.length > 1 && (
              <select className="input sm" aria-label="Show as" value={b.view && views.includes(b.view) ? b.view : ""} onChange={(e) => patch(i, { view: (e.target.value || undefined) as FieldView | undefined })}>
                <option value="">As the field says ({VIEW_LABEL[f!.options.views?.record && views.includes(f!.options.views.record) ? f!.options.views.record : views[0]]})</option>
                {views.map((v) => <option key={v} value={v}>{VIEW_LABEL[v]}</option>)}
              </select>
            )}
            {(b.type === "field" || b.type === "properties") && (
              <button className="btn sm ghost" onClick={() => patch(i, { width: b.width === "half" ? undefined : "half" })} title="Full width, or half (two side by side on a wide screen)">
                {b.width === "half" ? "Half width" : "Full width"}</button>
            )}
            <span className="spacer" />
            <button className="icon-btn sm" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up"><Icon name="up" size={14} /></button>
            <button className="icon-btn sm" disabled={i === blocks.length - 1} onClick={() => move(i, 1)} aria-label="Move down"><Icon name="down" size={14} /></button>
            <button className="icon-btn sm" onClick={() => remove(i)} aria-label="Remove block" title="Remove from the page (the field and its values stay)"><Icon name="trash" size={14} /></button>
          </div>
        )}
        {body}
      </div>
    );
  };

  const titleEditable = can.edit && primary && isEditable(primary) && ["text", "long_text", "email", "url", "phone"].includes(primary.type);
  return (
    <div className="page-view" role="dialog" aria-label={recordTitle(fields, rec)}>
      <div className="page-bar">
        <button className="icon-btn" onClick={onClose} aria-label="Close" title="Close (Esc)"><Icon name="x" /></button>
        <span className="page-crumb muted">{ds.table?.icon ? `${ds.table.icon} ` : ""}{ds.name}</span>
        {nav && nav.total > 1 && (
          <span className="record-nav">
            <button className="icon-btn" onClick={nav.prev} disabled={!nav.prev} title="Previous record (K)" aria-label="Previous record"><Icon name="up" size={16} /></button>
            <span className="muted small">{nav.index + 1} of {nav.total}</span>
            <button className="icon-btn" onClick={nav.next} disabled={!nav.next} title="Next record (J)" aria-label="Next record"><Icon name="down" size={16} /></button>
          </span>
        )}
        <span className="spacer" />
        <div className="segmented" role="group" aria-label="Show the record as">
          <button className="active" aria-pressed="true">Page</button>
          <button aria-pressed="false" onClick={onRecordView}>Record</button>
        </div>
        {canDesign && <button className={`btn sm ${designing ? "primary" : ""}`} onClick={() => setDesigning(!designing)}>
          {designing ? "Done" : <><Icon name="edit" size={14} /> <span className="page-edit-label">Edit page</span></>}</button>}
        <PageMore link={link} canDelete={can.delete} onDelete={() => setConfirm(true)} />
      </div>
      <div className="page-scroll">
        <article className="page-paper">
          {designing && (
            <div className="page-design-note">
              <label className="inline-check"><input type="checkbox" checked={!!page?.enabled}
                onChange={(e) => onLayout({ enabled: e.target.checked, blocks })} /> Open this table's records as pages</label>
              <span className="muted small">The layout is the same for every record of “{ds.name}”. Fields can also be placed in a page body's text (its “+ Field” button).</span>
            </div>
          )}
          {confirm && (
            <div className="page-confirm">
              <span>Delete this record?</span>
              <button className="btn danger sm" onClick={onDelete}>Delete</button>
              <button className="btn sm" onClick={() => setConfirm(false)}>Cancel</button>
            </div>
          )}
          <header className="page-title">
            {titleEditable ? <FieldInput key={rec.id} field={primary!} value={rec.data[primary!.id]} onCommit={(v) => onUpdate({ [primary!.id]: v })} />
              : <h1>{recordTitle(fields, rec)}</h1>}
          </header>
          <div className="page-blocks">{blocks.map(block)}</div>
          {designing && <AddBlock fields={usable} blocks={blocks} primary={primary?.id} onAdd={add} />}
          <details className="page-history">
            <summary>History</summary>
            <RecordHistory key={rec.id} recordId={rec.id} fields={fields} />
          </details>
        </article>
      </div>
    </div>
  );
}

function PageMore({ link, canDelete, onDelete }: { link?: string; canDelete: boolean; onDelete: () => void }) {
  const more = usePopover();
  if (!link && !canDelete) return null;
  return (
    <>
      <button className="icon-btn" onClick={more.open} aria-label="Record actions" title="More"><Icon name="more" size={18} /></button>
      {more.anchor && (
        <Popover anchor={more.anchor} onClose={more.close} align="right" width={200}>
          <div className="menu-list">
            {link && <button className="menu-item" onClick={() => { more.close(); navigator.clipboard?.writeText(link).then(() => showToast("Link to the record copied"), () => showToast(link)); }}>
              <Icon name="link" size={14} /> Copy link</button>}
            {canDelete && <button className="menu-item danger" onClick={() => { more.close(); onDelete(); }}><Icon name="trash" size={14} /> Delete…</button>}
          </div>
        </Popover>
      )}
    </>
  );
}

/** Adding to the page: a field (shown its own way), a heading, a divider, a long text as the page body, the other fields. */
function AddBlock({ fields, blocks, primary, onAdd }: {
  fields: Field[]; blocks: PageBlock[]; primary?: string; onAdd: (b: Omit<PageBlock, "id">) => void;
}) {
  const pick = usePopover();
  const [what, setWhat] = useState<"field" | "doc">("field");
  const used = new Set(blocks.map((b) => b.field).filter(Boolean));
  const choices = fields.filter((f) => f.id !== primary && !used.has(f.id) && (what === "doc" ? f.type === "long_text" : true));
  const open = (w: "field" | "doc") => (e: { currentTarget: HTMLElement }) => { setWhat(w); pick.open(e); };
  return (
    <div className="page-add">
      <span className="muted small">Add</span>
      <button className="btn sm" onClick={open("field")}><Icon name="plus" size={14} /> Field</button>
      <button className="btn sm" onClick={open("doc")}><Icon name="text" size={14} /> Page body</button>
      <button className="btn sm" onClick={() => onAdd({ type: "heading", text: "" })}>Heading</button>
      <button className="btn sm" onClick={() => onAdd({ type: "divider" })}>Divider</button>
      {!blocks.some((b) => b.type === "properties") && <button className="btn sm" onClick={() => onAdd({ type: "properties" })}>Other fields</button>}
      {pick.anchor && (
        <Popover anchor={pick.anchor} onClose={pick.close} width={260}>
          <div className="menu-list">
            {what === "doc" && <div className="muted small pad">A long text field, as the page's text (formatted, with fields placed in it)</div>}
            {choices.length ? choices.map((f) => (
              <button key={f.id} className="menu-item" onClick={() => { pick.close(); onAdd({ type: what, field: f.id }); }}><FieldIcon type={f.type} /> {f.name}</button>
            )) : <div className="muted pad">{what === "doc" ? "No long text field left: add one to the table first" : "Every field is on the page"}</div>}
          </div>
        </Popover>
      )}
    </div>
  );
}

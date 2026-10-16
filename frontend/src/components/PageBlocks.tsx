import { Suspense, lazy, useContext, useEffect, useMemo, useRef, useState, type CSSProperties, type HTMLAttributes, type ReactNode } from "react";
import { api } from "../api";
import type { Attachment, Field, FieldView, PageBlock, PageLayout, Rec, Refs } from "../types";
import { anchorOf } from "../recur";
import { BUILTIN_KEYS, fieldApplies, fitField, isEditable, primaryField, recordKind, recordMatches, recordTitle } from "../util";
import { AttachmentThumbs, CellValue, fileItems, RefsContext } from "./Cells";
import { FieldIcon, Icon } from "./Icon";
import { columns, PeekRecord, RecordsDisplay, RecordValue, useLinked } from "./LinkedView";
import { useNarrow } from "./useNarrow";

const PageDoc = lazy(() => import("./PageDoc"));
const EMBEDS = /^\{\{field:([\w-]+)\}\}$/gm;

// ------------------------------------------------------------------------------------------------ the layout as a tree

/** Where a block is: the page (null), a section ("s:<id>") or one tab of a tabs block ("t:<tabs id>:<tab id>"). */
export type Place = string | null;

/** Every block, nested ones too. */
export function allBlocks(blocks: PageBlock[]): PageBlock[] {
  return blocks.flatMap((b) => [b, ...allBlocks(b.blocks ?? []), ...(b.tabs ?? []).flatMap((t) => allBlocks(t.blocks))]);
}

/** The blocks with fn applied to each (null: removed), nested ones too. */
export function mapBlocks(blocks: PageBlock[], fn: (b: PageBlock) => PageBlock | null): PageBlock[] {
  return blocks.flatMap((b) => {
    const m = fn(b);
    if (!m) return [];
    return [{ ...m, ...(m.blocks ? { blocks: mapBlocks(m.blocks, fn) } : {}), ...(m.tabs ? { tabs: m.tabs.map((t) => ({ ...t, blocks: mapBlocks(t.blocks, fn) })) } : {}) }];
  });
}

function withPlace(blocks: PageBlock[], place: Place, fn: (list: PageBlock[]) => PageBlock[]): PageBlock[] {
  if (place === null) return fn(blocks);
  const [kind, id, tab] = place.split(":");
  return mapBlocks(blocks, (b) => b.id !== id ? b
    : kind === "s" ? { ...b, blocks: fn(b.blocks ?? []) } : { ...b, tabs: b.tabs?.map((t) => (t.id === tab ? { ...t, blocks: fn(t.blocks) } : t)) });
}

/** Where a block is, and its position there. */
export function placeOf(blocks: PageBlock[], id: string): { place: Place; index: number } | null {
  const i = blocks.findIndex((b) => b.id === id);
  if (i >= 0) return { place: null, index: i };
  for (const b of allBlocks(blocks)) {
    const j = (b.blocks ?? []).findIndex((x) => x.id === id);
    if (j >= 0) return { place: `s:${b.id}`, index: j };
    for (const t of b.tabs ?? []) {
      const k = t.blocks.findIndex((x) => x.id === id);
      if (k >= 0) return { place: `t:${b.id}:${t.id}`, index: k };
    }
  }
  return null;
}

export const removeBlock = (blocks: PageBlock[], id: string) => mapBlocks(blocks, (b) => (b.id === id ? null : b));

/** Put a block at a place (at index; else at the end), taking it from wherever it was. */
export function placeBlock(blocks: PageBlock[], block: PageBlock, place: Place, index?: number): PageBlock[] {
  if (place && place.split(":")[1] === block.id) return blocks;   // (not into itself)
  if (place && allBlocks([block]).some((b) => place.split(":")[1] === b.id && b.id !== block.id)) return blocks;   // nor inside itself
  const rest = removeBlock(blocks, block.id);
  return withPlace(rest, place, (list) => { const a = [...list]; a.splice(index ?? a.length, 0, block); return a; });
}

export const updateBlock = (blocks: PageBlock[], id: string, patch: Partial<PageBlock>) => mapBlocks(blocks, (b) => {
  if (b.id !== id) return b;
  const n = { ...b, ...patch };
  (Object.keys(n) as (keyof PageBlock)[]).forEach((k) => n[k] === undefined && delete n[k]);
  return n;
});

/** The places blocks can go: the page, each section, each tab. */
export function places(blocks: PageBlock[]): { place: Place; label: string }[] {
  return [{ place: null, label: "The page" }, ...allBlocks(blocks).flatMap((b) => b.type === "section" ? [{ place: `s:${b.id}`, label: `Section: ${b.text || "Untitled"}` }]
    : b.type === "tabs" ? (b.tabs ?? []).map((t) => ({ place: `t:${b.id}:${t.id}`, label: `Tab: ${t.text}` })) : [])];
}

let seq = 0;
export const newId = () => `b${Date.now().toString(36)}${(seq++).toString(36)}`;
/** A copy with new ids (a layout for one kind of record, started from another). */
export const copyBlocks = (blocks: PageBlock[]) => mapBlocks(blocks, (b) => ({ ...b, id: newId(), ...(b.tabs ? { tabs: b.tabs.map((t) => ({ ...t, id: newId() })) } : {}) }));

/** A field placed in a page body's text ({{field:<id>}}): a combined database's record keeps its source table's ids. */
export const embeddedField = (fields: Field[], id: string, tableId: string) => fields.find((f) => f.id === id || f.mapping?.[tableId] === id);
/** The fields a page body can have placed in it, by the ids its text keeps (a combined database: the source's). */
export const embeddable = (fields: Field[], tableId: string) =>
  fields.filter((f) => !f.mapping || f.mapping[tableId]).map((f) => (f.mapping ? { ...f, id: f.mapping[tableId] } : f));

// ------------------------------------------------------------------------------------------------ which layout, which fields

/** Fields that take the page's whole width when laid out by default; the rest take half. */
const WIDE = new Set(["long_text", "link", "lookup", "conditional_lookup", "attachment", "multi_select", "recurrence"]);

/** The fields a page can show (not the record ID, the built-in dates; the title is the primary field). */
export function pageFields(fields: Field[], rec?: Rec): Field[] {
  const kind = rec ? recordKind(fields, rec) : undefined;
  return fields.filter((f) => f.id !== "__source" && f.type !== "record_id" && !BUILTIN_KEYS.has(f.system_key ?? "")
    && (kind === undefined || fieldApplies(f, kind)));
}

/** A page laid out by default: every field (but the title) as its own block, short ones two to a row, and the first long
 * text as the page's body at the end. */
export function defaultBlocks(fields: Field[]): PageBlock[] {
  const p = primaryField(fields);
  const shown = pageFields(fields).filter((f) => f.id !== p?.id && f.type !== "button");
  const body = shown.find((f) => f.type === "long_text" && !f.system_key);
  return [
    ...shown.filter((f) => f !== body).map((f): PageBlock => ({ id: `f-${f.id}`, type: "field", field: f.id, ...(WIDE.has(f.type) ? {} : { span: 2 }) })),
    ...(body ? [{ id: "body", type: "doc" as const, field: body.id }] : []),
  ];
}

/** The key of a record's kind for the page's layouts by kind (its single select's option; a checkbox: "true" / "false"). */
export const variantKey = (v: unknown) => (typeof v === "boolean" ? String(v) : v === null || v === undefined || v === "" ? "" : String(v));

/** A table's or combined database's page layout. */
export const pageOf = (ds: { kind: string; table?: { page?: PageLayout | null } | null; page?: PageLayout | null }) =>
  (ds.kind === "table" ? ds.table?.page : ds.page) ?? null;

/** The blocks a record's page shows: the layout for its kind (Table settings → Page → Layouts), else the page's. */
export function layoutFor(page: PageLayout | null | undefined, fields: Field[], rec: Rec): PageBlock[] {
  const v = page?.variants;
  const own = v ? v.layouts[variantKey(rec.data[v.field] ?? (fields.find((f) => f.id === v.field)?.type === "checkbox" ? false : ""))] : undefined;
  if (own) return own;
  return page?.blocks.length ? page.blocks : defaultBlocks(fields);
}

// ------------------------------------------------------------------------------------------------ drawing

/** What the layout editor (Table settings → Page) puts on each block: its tools, its resize handles, dragging. */
export interface BlockDesign {
  tools: (b: PageBlock) => ReactNode;
  handles: (b: PageBlock) => ReactNode;
  drag: (b: PageBlock) => HTMLAttributes<HTMLDivElement>;
  isOver: (b: PageBlock) => boolean;
}

interface Ctx {
  fields: Field[]; usable: Field[]; rec: Rec; canEdit: boolean; publicMode: boolean;
  onUpdate: (patch: Record<string, any>) => void; onButton?: (f: Field) => void; design?: BlockDesign;
  rest: Field[]; primary?: Field; refs: Refs;
}

/** The page's blocks, in 4 columns (2 on a phone): each spans some of them and can have a fixed height (its content then
 * scrolls). A block with a condition shows only for records that match it (in the editor: dimmed when this one doesn't). */
export function PageBlocks({ blocks, fields, rec, canEdit, onUpdate, onButton, design, publicMode = false }: {
  blocks: PageBlock[]; fields: Field[]; rec: Rec; canEdit: boolean; publicMode?: boolean;
  onUpdate: (patch: Record<string, any>) => void; onButton?: (f: Field) => void; design?: BlockDesign;
}) {
  const { refs } = useContext(RefsContext);
  const usable = pageFields(fields, rec);
  const primary = primaryField(fields);
  // fields placed somewhere (a block of their own, or in a page body's text) aren't repeated in "Other fields"
  const placed = useMemo(() => {
    const s = new Set<string>(primary ? [primary.id] : []);
    for (const b of allBlocks(blocks)) {
      if (b.field) s.add(b.field);
      for (const it of b.items ?? []) s.add(it.field);
      if (b.type === "doc" && b.field) for (const m of String(rec.data[b.field] ?? "").matchAll(EMBEDS)) { const ef = embeddedField(fields, m[1], rec.table_id); if (ef) s.add(ef.id); }
    }
    return s;
  }, [blocks, rec.data, primary]);
  const ctx: Ctx = { fields, usable, rec, canEdit, publicMode, onUpdate, onButton, design, refs, primary,
    rest: usable.filter((f) => !placed.has(f.id) && f.type !== "button") };
  return <Grid blocks={blocks} ctx={ctx} />;
}

function Grid({ blocks, ctx }: { blocks: PageBlock[]; ctx: Ctx }) {
  return (
    <div className="page-blocks">
      {blocks.map((b) => {
        const matches = recordMatches(ctx.rec, ctx.fields, b.when, ctx.refs);
        if (!matches && !ctx.design) return null;
        const body = <BlockBody b={b} ctx={ctx} />;
        const span = b.type === "heading" || b.type === "divider" ? 4 : b.span ?? 4;
        const style = { "--span": span, ...(b.height ? { height: b.height } : {}) } as CSSProperties;
        const d = ctx.design;
        return (
          <div key={b.id} className={`page-block pb-${b.type} span-${span} ${b.height ? "fixed" : ""} ${d ? "designing" : ""} ${d?.isOver(b) ? "drop-here" : ""} ${matches ? "" : "unmatched"}`}
            style={style} data-block={b.id} {...(d ? d.drag(b) : {})}>
            {d?.tools(b)}
            {d && b.when?.items.length ? <div className="block-when small">{matches ? "Shown: this record meets its condition" : "Hidden for this record (its condition)"}</div> : null}
            <div className="page-block-body">{body}</div>
            {d?.handles(b)}
          </div>
        );
      })}
    </div>
  );
}

function Labelled({ f, view, ctx }: { f: Field; view?: FieldView; ctx: Ctx }) {
  const kind = recordKind(ctx.fields, ctx.rec);
  const value = f.type === "button"
    ? <button className="cell-button" style={{ background: f.options.color ?? "var(--accent)" }} disabled={!ctx.canEdit && f.options.action !== "url"}
        onClick={() => ctx.onButton?.(f)}>{f.options.label ?? "Click"}</button>
    : f.id !== "__source" && f.mapping && ctx.rec.table_id && !f.mapping[ctx.rec.table_id] ? <span className="muted">Not mapped for this source</span>
    : <RecordValue field={fitField(f, kind, ctx.rec.data[f.id])} value={ctx.rec.data[f.id]} rec={ctx.rec} canEdit={ctx.canEdit} onUpdate={ctx.onUpdate} view={view}
        anchor={f.type === "recurrence" ? anchorOf(ctx.fields, ctx.rec) : undefined} />;
  return (
    <div className="page-field">
      <span className="page-label" title={f.description ?? undefined}><FieldIcon type={f.type} /> {f.name}{f.required && <span className="req-mark" title="Required"> *</span>}</span>
      <div className="page-value">{value}</div>
    </div>
  );
}

function BlockBody({ b, ctx }: { b: PageBlock; ctx: Ctx }): ReactNode {
  const f = b.field ? ctx.usable.find((x) => x.id === b.field) ?? ctx.fields.find((x) => x.id === b.field) : undefined;
  switch (b.type) {
    case "properties":
      return ctx.rest.length ? <div className="page-props">{ctx.rest.map((x) => <div key={x.id}><Labelled f={x} ctx={ctx} /></div>)}</div>
        : ctx.design ? <div className="muted small">Every field has a place of its own on this page.</div> : null;
    case "heading":
      return b.text ? <h2 className="page-heading" id={`h-${b.id}`}>{b.text}</h2> : ctx.design ? <h2 className="page-heading muted">Heading</h2> : null;
    case "divider":
      return <hr className="page-divider" />;
    case "section":
      return <Section b={b} ctx={ctx} />;
    case "tabs":
      return <Tabs b={b} ctx={ctx} />;
    case "related":
      return <Related b={b} ctx={ctx} />;
    case "stats":
      return <Stats b={b} ctx={ctx} />;
  }
  if (!f) return ctx.design ? <div className="muted small">A field that's no longer in this table</div> : null;
  if (b.type === "files") return <Files b={b} f={f} ctx={ctx} />;
  if (b.type === "doc") {
    const editable = ctx.canEdit && isEditable(f);
    return (
      <div className="page-body-block">
        <div className="page-label"><FieldIcon type={f.type} /> {f.name}</div>
        <Suspense fallback={<div className="muted">Loading…</div>}>
          <PageDoc key={`${ctx.rec.id}:${f.id}`} value={String(ctx.rec.data[f.id] ?? "")} editable={editable}
            onChange={(md) => ctx.onUpdate({ [f.id]: md.trim() ? md : null })}
            fields={embeddable(ctx.usable.filter((x) => x.id !== f.id && x.id !== ctx.primary?.id && x.type !== "long_text" && x.type !== "button"), ctx.rec.table_id)}
            renderField={(id) => { const ef = embeddedField(ctx.usable, id, ctx.rec.table_id); return ef ? <Labelled f={ef} ctx={ctx} /> : <span className="muted small">A field that's no longer here</span>; }} />
        </Suspense>
        {!editable && !ctx.rec.data[f.id] && !ctx.design && <p className="muted">Nothing written yet.</p>}
      </div>
    );
  }
  return <Labelled f={f} view={b.view} ctx={ctx} />;
}

/** A titled group of blocks that folds away (open unless it starts folded; the editor and printing show it open). */
function Section({ b, ctx }: { b: PageBlock; ctx: Ctx }) {
  const [open, setOpen] = useState(!b.closed);
  const shown = open || !!ctx.design;
  return (
    <section className={`page-section ${shown ? "open" : "folded"}`}>
      <button type="button" className="page-section-head" aria-expanded={shown} id={`h-${b.id}`} onClick={() => !ctx.design && setOpen(!open)}>
        <span className={`chev ${shown ? "open" : ""}`}><Icon name="right" size={13} /></span> {b.text || "Section"}
      </button>
      <div className="page-section-body">
        <Grid blocks={b.blocks ?? []} ctx={ctx} />
        {ctx.design && !(b.blocks ?? []).length && <div className="muted small block-drop-hint">Empty: move blocks here with their ⚙ menu (Put in…).</div>}
      </div>
    </section>
  );
}

/** Blocks in tabs (every tab is drawn, the others hidden, so printing shows them all). */
function Tabs({ b, ctx }: { b: PageBlock; ctx: Ctx }) {
  const tabs = b.tabs ?? [];
  const [active, setActive] = useState(tabs[0]?.id);
  useEffect(() => { if (!tabs.some((t) => t.id === active)) setActive(tabs[0]?.id); }, [tabs.map((t) => t.id).join()]);   // eslint-disable-line react-hooks/exhaustive-deps
  // the outline opens a tab
  useEffect(() => {
    const on = (e: Event) => { const id = (e as CustomEvent<string>).detail; if (tabs.some((t) => t.id === id)) setActive(id); };
    window.addEventListener("page-open-tab", on);
    return () => window.removeEventListener("page-open-tab", on);
  });
  return (
    <div className="page-tabs">
      <div className="page-tab-strip" role="tablist">
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" id={`h-${t.id}`} aria-selected={t.id === active} className={t.id === active ? "active" : ""}
            onClick={() => setActive(t.id)}>{t.text}</button>
        ))}
      </div>
      {tabs.map((t) => (
        <div key={t.id} role="tabpanel" className={`page-tab-panel ${t.id === active ? "active" : ""}`} data-tab-title={t.text}>
          <Grid blocks={t.blocks} ctx={ctx} />
          {ctx.design && !t.blocks.length && <div className="muted small block-drop-hint">Empty: move blocks here with their ⚙ menu (Put in…).</div>}
        </div>
      ))}
    </div>
  );
}

/** Big numbers side by side (a summary of the record: totals, rollups, formulas), or bars. */
function Stats({ b, ctx }: { b: PageBlock; ctx: Ctx }) {
  const items = (b.items ?? []).map((it) => ({ ...it, f: ctx.fields.find((x) => x.id === it.field) })).filter((x) => x.f);
  if (!items.length) return ctx.design ? <div className="muted small">Choose the numbers to show (⚙).</div> : null;
  return (
    <div className="page-stats">
      {items.map(({ f, view }) => (
        <div key={f!.id} className="page-stat">
          <span className="page-stat-label">{f!.name}</span>
          <span className="page-stat-value">{ctx.rec.data[f!.id] === null || ctx.rec.data[f!.id] === undefined ? <span className="muted">–</span>
            : <CellValue field={f!} value={ctx.rec.data[f!.id]} view={view === "bar" ? "bar" : "stat"} />}</span>
        </div>
      ))}
    </div>
  );
}

interface RelatedData { fields: Field[]; records: Rec[]; refs: Refs; total: number }

/** Records of another table that link to this one (in one of its views' filter and order): open one over the page, or add
 * one already linked here. */
function Related({ b, ctx }: { b: PageBlock; ctx: Ctx }) {
  const narrow = useNarrow();
  const [data, setData] = useState<RelatedData | null>(null);
  const [err, setErr] = useState("");
  const [version, setVersion] = useState(0);
  const [peek, setPeek] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const outer = useContext(RefsContext);
  const live = ctx.rec.id !== "__preview";
  const elsewhere = !!b.to && b.to !== ctx.rec.table_id;   // a combined database: a list for another source's records
  useEffect(() => {
    if (!b.table || !b.field || !live || elsewhere) return;
    let on = true;
    api.post<RelatedData>(`/tables/${b.table}/records/related`, { field: b.field, record: ctx.rec.id, view: b.filterView, limit: b.limit ?? 100 })
      .then((d) => { if (on) { setData(d); setErr(""); } }).catch((e) => on && setErr((e as Error).message));
    return () => { on = false; };
  }, [b.table, b.field, b.filterView, b.limit, ctx.rec.id, version, live]);
  const refs = useMemo<Refs>(() => data ? { records: { ...outer.refs.records, ...data.refs.records }, users: { ...outer.refs.users, ...data.refs.users },
    display: { ...outer.refs.display, ...data.refs.display } } : outer.refs, [data, outer.refs]);
  if (ctx.publicMode || (elsewhere && !ctx.design)) return null;
  const byId = new Map((data?.records ?? []).map((r) => [r.id, r]));
  const cols = data ? columns({ options: { views: { fields: b.fields } } }, data.fields) : [];
  const title = (id: string) => (data && byId.get(id) ? recordTitle(data.fields, byId.get(id)!) : "Untitled");
  const view = narrow && b.display === "table" ? "cards" : b.display ?? "list";
  const add = async () => {
    setAdding(true);
    try {
      const r = await api.post<Rec>(`/tables/${b.table}/records`, { data: { [b.field!]: [ctx.rec.id] } });
      setVersion((v) => v + 1);
      setPeek(r.id);
    } catch (e) { setErr((e as Error).message); }
    setAdding(false);
  };
  const open = peek && data ? byId.get(peek) : undefined;
  return (
    <RefsContext.Provider value={{ ...outer, refs }}>
      <div className="page-related">
        <div className="page-label page-related-head">
          <Icon name="link" size={13} /> {b.text || "Related"}{data && <span className="muted small"> · {data.total}</span>}
          <span className="spacer" />
          {ctx.canEdit && live && <button type="button" className="btn sm" disabled={adding} onClick={add}><Icon name="plus" size={13} /> New</button>}
        </div>
        {!live || elsewhere ? <div className="linked-empty muted">Records linking to this one show here{elsewhere ? " (for records of its own table)" : ""}.</div>
          : err && !data ? <div className="linked-empty muted">{err}</div>
          : !data ? <div className="linked-empty muted">Loading…</div>
          : !data.records.length ? <div className="linked-empty muted">Nothing links here yet</div>
          : <RecordsDisplay ids={data.records.map((r) => r.id)} byId={byId} fields={data.fields} cols={cols} view={view} title={title}
              actions={(id) => <span className="linked-actions"><button type="button" className="icon-btn sm" title="Open" aria-label={`Open ${title(id)}`}
                onClick={(e) => { e.stopPropagation(); setPeek(id); }}><Icon name="expand" size={13} /></button></span>}
              onOpen={setPeek} />}
        {data && data.total > data.records.length && <div className="muted small">Showing {data.records.length} of {data.total}</div>}
      </div>
      {open && data && <PeekRecord tableId={b.table!} fields={data.fields} rec={open} onClose={() => setPeek(null)} onSaved={() => setVersion((v) => v + 1)} />}
    </RefsContext.Provider>
  );
}

/** A field's files as a gallery, with a drop zone and a button to add photos or files (a phone offers its camera). An
 * attachment field keeps them; a link to a files table uploads into that table and links them. */
function Files({ b: _b, f, ctx }: { b: PageBlock; f: Field; ctx: Ctx }) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const value = ctx.rec.data[f.id];
  const linkIds: string[] = f.type === "link" && Array.isArray(value) ? value : [];
  const { data } = useLinked(f.type === "link" ? f.options.foreignTableId ?? "" : "", linkIds, 0);
  const items: Attachment[] = f.type === "attachment" ? fileItems(value)
    : (data?.records ?? []).flatMap((r) => { const ff = data!.fields.find((x) => x.system_key === "file"); return ff ? fileItems(r.data[ff.id]) : []; });
  const canAdd = ctx.canEdit && !ctx.publicMode && isEditable(f) && ctx.rec.id !== "__preview" && (f.type === "attachment" || !!f.options.foreignTableId);
  const upload = async (list: FileList | File[] | null) => {
    const files = Array.from(list ?? []);
    if (!files.length || !canAdd) return;
    setErr("");
    try {
      if (f.type === "attachment") {
        const added: Attachment[] = [];
        for (const [i, file] of files.entries()) {
          setBusy(`Uploading ${i + 1} of ${files.length}…`);
          const body = new FormData(); body.append("file", file);
          added.push(await api.upload<Attachment>("/attachments", body));
        }
        ctx.onUpdate({ [f.id]: [...fileItems(value), ...added] });
      } else {
        const ids: string[] = [];
        for (const [i, file] of files.entries()) {
          setBusy(`Uploading ${i + 1} of ${files.length}…`);
          const body = new FormData(); body.append("files", file);
          const r = await api.upload<{ records: { id: string }[] }>(`/tables/${f.options.foreignTableId}/files`, body);
          ids.push(...r.records.map((x) => x.id));
        }
        ctx.onUpdate({ [f.id]: [...linkIds, ...ids] });
      }
    } catch (e) { setErr((e as Error).message); }
    setBusy("");
    if (input.current) input.current.value = "";
  };
  return (
    <div className={`page-files ${over ? "over" : ""}`}
      onDragOver={canAdd ? (e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } } : undefined}
      onDragLeave={() => setOver(false)}
      onDrop={canAdd ? (e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); } : undefined}>
      <div className="page-label"><FieldIcon type={f.type} /> {f.name}{items.length ? <span className="muted small"> · {items.length}</span> : null}</div>
      {items.length ? <div className="page-files-gallery"><AttachmentThumbs items={items} size={112} /></div>
        : <div className="linked-empty muted">{canAdd ? "No files yet: drop photos or files here" : "No files"}</div>}
      {canAdd && (
        <div className="page-files-add">
          <input ref={input} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
          <button type="button" className="btn sm" disabled={!!busy} onClick={() => input.current?.click()}><Icon name="upload" size={14} /> {busy || "Add photos or files"}</button>
          <span className="muted small">or drop them here</span>
        </div>
      )}
      {err && <div className="error small">{err}</div>}
    </div>
  );
}

/** The page's outline: its headings, sections and tabs (and related lists), to jump to. */
export function outlineOf(blocks: PageBlock[], shows: (b: PageBlock) => boolean = () => true): { id: string; text: string; depth: number; tab?: string }[] {
  const out: { id: string; text: string; depth: number; tab?: string }[] = [];
  const walk = (list: PageBlock[], depth: number) => {
    for (const b of list) {
      if (!shows(b)) continue;   // (a block this record's conditions hide)
      if (b.type === "heading" && b.text) out.push({ id: b.id, text: b.text, depth });
      if (b.type === "section") { out.push({ id: b.id, text: b.text || "Section", depth }); walk(b.blocks ?? [], depth + 1); }
      if (b.type === "related") out.push({ id: b.id, text: b.text || "Related", depth });
      if (b.type === "tabs") for (const t of b.tabs ?? []) { out.push({ id: t.id, text: t.text, depth, tab: t.id }); walk(t.blocks, depth + 1); }
    }
  };
  walk(blocks, 0);
  return out;
}

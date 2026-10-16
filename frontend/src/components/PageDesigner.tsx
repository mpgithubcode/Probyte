import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import type { Dataset, Field, FieldView, FilterGroup, PageBlock, PageLayout, Rec, View } from "../types";
import { countConditions, primaryField, recordTitle, valueType } from "../util";
import { VIEW_LABEL, displaysFor } from "../display";
import { FieldIcon, Icon } from "./Icon";
import { Popover } from "./Overlay";
import { FilterEditor } from "./ViewControls";
import { allBlocks, copyBlocks, defaultBlocks, newId, pageFields, pageOf, PageBlocks, placeBlock, placeOf, places, removeBlock, updateBlock,
  variantKey, type BlockDesign, type Place } from "./PageBlocks";
import { PageTitle } from "./PageView";

const SPANS: { n: number; label: string }[] = [{ n: 1, label: "¼" }, { n: 2, label: "½" }, { n: 3, label: "¾" }, { n: 4, label: "Full" }];
const WIDE = new Set(["long_text", "link", "lookup", "conditional_lookup", "attachment"]);

interface Inbound { table_id: string; table_name: string; field_id: string; field_name: string; views: { id: string; name: string }[]; to?: string }

const infoCache = new Map<string, Promise<{ fields: Field[]; views: View[] }>>();
function useTableInfo(tableId?: string) {
  const [info, setInfo] = useState<{ fields: Field[]; views: View[] } | null>(null);
  useEffect(() => {
    setInfo(null);
    if (!tableId) return;
    if (!infoCache.has(tableId)) infoCache.set(tableId, api.get<{ fields: Field[]; views: View[] }>(`/tables/${tableId}`).then((r) => ({ fields: r.fields, views: r.views ?? [] })));
    infoCache.get(tableId)!.then(setInfo).catch(() => { infoCache.delete(tableId); setInfo({ fields: [], views: [] }); });
  }, [tableId]);
  return info;
}

/** Table settings → Page: whether records open as pages, and their layout, on one of the records as it would look.
 * Each field is a block: shown or hidden (the list), placed by dragging its grip (or Put in… a section or tab), sized
 * by dragging its edges (or ¼ ½ ¾ Full), shown its own way, and shown only for some records if it has a condition. Plus
 * headings, dividers, sections, tabs, related records of other tables, files, numbers side by side and a long text as
 * the page's body; what stays under the title, an outline, a template for new records' bodies, and a layout of its own
 * for some kinds of records. Every change is saved as it's made. */
export function PageDesigner({ ds, onChange }: { ds: Dataset; onChange: (page: PageLayout | null) => void }) {
  const fields = ds.fields;
  // while it's open, the layout here is the one that counts (a refresh of the table arriving meanwhile may still carry the
  // one from before the last change)
  const initial = (): PageLayout => {
    const p = pageOf(ds);
    return p ? { ...p, blocks: p.blocks.length ? p.blocks : defaultBlocks(fields) } : { enabled: false, blocks: defaultBlocks(fields) };
  };
  const [layout, setLayout] = useState<PageLayout>(initial);
  useEffect(() => setLayout(initial()), [ds.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const save = (next: PageLayout) => { setLayout(next); onChange(next); };

  // a layout per kind of record
  const [editing, setEditing] = useState<string | null>(null);   // the kind being laid out (null: every record / the default)
  const vField = layout.variants ? fields.find((f) => f.id === layout.variants!.field) : undefined;
  const own = editing !== null ? layout.variants?.layouts[editing] : undefined;
  const blocks = editing === null ? layout.blocks : own ?? layout.blocks;
  const setBlocks = (b: PageBlock[]) => editing === null || !layout.variants ? save({ ...layout, blocks: b })
    : save({ ...layout, variants: { ...layout.variants, layouts: { ...layout.variants.layouts, [editing]: b } } });
  const editable = editing === null || !!own;

  const primary = primaryField(fields);
  const all = pageFields(fields).filter((f) => f.id !== primary?.id && f.type !== "button");

  // a record to lay it out on (of the kind being laid out)
  const sample = ds.records.filter((r) => editing === null || !vField || variantKey(r.data[vField.id] ?? (vField.type === "checkbox" ? false : "")) === editing).slice(0, 100);
  const [recId, setRecId] = useState("");
  const rec: Rec = sample.find((r) => r.id === recId) ?? sample[0] ?? { id: "__preview", table_id: ds.id, data: {} };

  const shown = useMemo(() => new Set(allBlocks(blocks).flatMap((b) => [b.field, ...(b.items ?? []).map((i) => i.field)]).filter(Boolean) as string[]), [blocks]);
  const toggle = (f: Field) => shown.has(f.id)
    ? setBlocks(allBlocks(blocks).filter((b) => b.field === f.id).reduce((acc, b) => removeBlock(acc, b.id), blocks))
    : setBlocks([...blocks, { id: newId(), type: "field", field: f.id, ...(WIDE.has(f.type) ? {} : { span: 2 }) }]);
  const patch = (id: string, p: Partial<PageBlock>) => setBlocks(updateBlock(blocks, id, p));
  const add = (b: Omit<PageBlock, "id">) => setBlocks([...blocks, { id: newId(), ...b } as PageBlock]);
  const move = (id: string, d: -1 | 1) => {
    const at = placeOf(blocks, id);
    const b = allBlocks(blocks).find((x) => x.id === id);
    if (!at || !b) return;
    setBlocks(placeBlock(blocks, b, at.place, Math.max(0, at.index + d)));
  };

  // dragging blocks to move them (by their grip); dragging edges to size them (live, saved when let go)
  const dragged = useRef<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [live, setLive] = useState<{ id: string; span?: number; height?: number } | null>(null);
  const [menu, setMenu] = useState<{ id: string; anchor: HTMLElement } | null>(null);   // a block's ⚙
  const resize = (id: string, axis: "x" | "y") => (e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault(); e.stopPropagation();
    const el = e.currentTarget.closest(".page-block") as HTMLElement;
    const grid = el.parentElement!;
    const cols = getComputedStyle(grid).gridTemplateColumns.split(" ").length;   // 4, or 2 on a phone
    const col = grid.clientWidth / cols;
    const box = el.getBoundingClientRect();
    const x0 = e.clientX, y0 = e.clientY;
    let last: { span?: number; height?: number } = {};
    const moveTo = (ev: PointerEvent) => {
      if (axis === "x") {
        const n = Math.max(1, Math.min(cols, Math.round((box.width + ev.clientX - x0) / col)));
        last = { span: cols === 4 ? n : n === 1 ? 1 : 4 };
      } else {
        last = { height: Math.max(60, Math.round((box.height + ev.clientY - y0) / 10) * 10) };
      }
      setLive({ id, ...last });
    };
    const up = () => {
      window.removeEventListener("pointermove", moveTo); window.removeEventListener("pointerup", up);
      setLive(null);
      if (last.span !== undefined) patch(id, { span: last.span === 4 ? undefined : last.span });
      if (last.height !== undefined) patch(id, { height: last.height });
    };
    window.addEventListener("pointermove", moveTo); window.addEventListener("pointerup", up);
  };
  const shownBlocks = live ? updateBlock(blocks, live.id, { ...(live.span !== undefined ? { span: live.span } : {}), ...(live.height ? { height: live.height } : {}) }) : blocks;
  const nameOf = (b: PageBlock) => {
    const f = b.field ? fields.find((x) => x.id === b.field) : undefined;
    return ({ properties: "Other fields", heading: "Heading", divider: "Divider", section: `Section${b.text ? `: ${b.text}` : ""}`, tabs: "Tabs",
      related: b.text || "Related list", stats: "Numbers", files: `Files: ${f?.name ?? "?"}`, doc: `${f?.name ?? "?"} · page body`, field: f?.name ?? "?" } as Record<string, string>)[b.type];
  };

  const design: BlockDesign = !editable ? { drag: () => ({}), isOver: () => false, tools: () => null, handles: () => null } : {
    drag: (b) => ({
      draggable: armed === b.id,
      onDragStart: (e) => { e.stopPropagation(); dragged.current = b.id; e.dataTransfer.effectAllowed = "move"; },
      onDragOver: (e) => { if (dragged.current && dragged.current !== b.id) { e.preventDefault(); e.stopPropagation(); setOver(b.id); } },
      onDragLeave: () => setOver((o) => (o === b.id ? null : o)),
      onDrop: (e) => {
        e.preventDefault(); e.stopPropagation();
        const from = allBlocks(blocks).find((x) => x.id === dragged.current), to = placeOf(blocks, b.id);
        if (from && to) setBlocks(placeBlock(blocks, from, to.place, to.index));
        dragged.current = null; setOver(null);
      },
      onDragEnd: () => { dragged.current = null; setOver(null); setArmed(null); },
    }),
    isOver: (b) => over === b.id,
    tools: (b) => {
      const f = b.field ? fields.find((x) => x.id === b.field) : undefined;
      return (
        <div className="block-tools" onMouseDown={(e) => e.stopPropagation()}>
          <span className="block-grip" title="Drag to move" onMouseDown={() => setArmed(b.id)} onMouseUp={() => setArmed(null)}><Icon name="menu" size={14} /></span>
          <span className="block-name">{nameOf(b)}</span>
          {b.when?.items.length ? <span className="block-badge" title="Shown only for records that meet its condition">if…</span> : null}
          {b.type === "heading" && <input className="input sm block-heading" defaultValue={b.text ?? ""} key={b.text} placeholder="Heading text" aria-label="Heading text"
            onBlur={(e) => e.target.value !== (b.text ?? "") && patch(b.id, { text: e.target.value })} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />}
          <span className="spacer" />
          <button type="button" className="icon-btn sm" aria-label={`${nameOf(b)} settings`} title="How it's shown, its size, its place, when it shows"
            onClick={(e) => setMenu({ id: b.id, anchor: e.currentTarget })}><Icon name="settings" size={14} /></button>
          <button type="button" className="icon-btn sm" onClick={() => setBlocks(removeBlock(blocks, b.id))}
            aria-label={f ? `Hide ${f.name}` : `Remove ${nameOf(b)}`} title={f ? "Hide from the page (the field and its values stay)" : "Remove"}>
            <Icon name={f ? "eye" : "trash"} size={14} /></button>
        </div>
      );
    },
    handles: (b) => b.type === "heading" || b.type === "divider" ? null : (
      <>
        <span className="resize-x" title="Drag to make it wider or narrower" onPointerDown={resize(b.id, "x")} onMouseDown={(e) => e.preventDefault()} />
        <span className="resize-y" title="Drag to set its height (double-click: as tall as its content)" onPointerDown={resize(b.id, "y")} onMouseDown={(e) => e.preventDefault()}
          onDoubleClick={() => patch(b.id, { height: undefined })} />
      </>
    ),
  };

  const mb = menu ? allBlocks(blocks).find((x) => x.id === menu.id) : undefined;
  const sizedTypes = new Set(["properties", "field", "doc", "section", "tabs", "related", "files", "stats"]);
  const variantFields = fields.filter((f) => f.type === "single_select" || f.type === "checkbox");
  const kinds = vField ? (vField.type === "checkbox" ? ["true", "false"] : (vField.options.choices ?? []).map((c) => c.name)) : [];
  const kindLabel = (k: string) => (vField?.type === "checkbox" ? (k === "true" ? `${vField.name}: ticked` : `${vField.name}: not ticked`) : k);

  return (
    <div className="designer">
      {menu && mb && (
        <Popover anchor={menu.anchor} onClose={() => setMenu(null)} align="right" width={320}>
          <BlockMenu b={mb} fields={fields} blocks={blocks} onPatch={(p) => patch(mb.id, p)} onMove={(d) => move(mb.id, d)}
            onPlace={(place) => { setBlocks(placeBlock(blocks, mb, place)); setMenu(null); }} sized={sizedTypes.has(mb.type)} />
        </Popover>
      )}
      <div className="designer-head">
        <label className="inline-check"><input type="checkbox" checked={layout.enabled} onChange={(e) => save({ ...layout, enabled: e.target.checked })} />
          <b>Open records as pages</b></label>
        <span className="muted small">A page shows a record like a document, laid out here. The title is always the primary field (Fields → Primary field).</span>
      </div>
      <div className="designer-split">
        <aside className="designer-fields">
          <div className="designer-sub">Fields</div>
          <div className="designer-field locked" title="The title: the primary field"><FieldIcon type={primary?.type ?? "text"} /> {primary?.name ?? "Title"}
            <span className="muted small">title</span></div>
          {all.map((f) => (
            <button key={f.id} type="button" className={`designer-field ${shown.has(f.id) ? "on" : ""}`} aria-pressed={shown.has(f.id)} disabled={!editable}
              onClick={() => toggle(f)} title={shown.has(f.id) ? "Shown: click to hide" : "Hidden: click to show"}>
              <FieldIcon type={f.type} /> <span className="designer-field-name">{f.name}</span>
              <Icon name="eye" size={14} className="designer-eye" />
            </button>
          ))}
          <div className="designer-sub">Add</div>
          <AddBlocks ds={ds} fields={fields} blocks={blocks} disabled={!editable} onAdd={add} />
          <div className="designer-sub">The page</div>
          <PageSettings fields={fields} layout={layout} onChange={save} />
          <div className="designer-sub">Layouts by kind</div>
          <label className="stack small">A layout of its own for each
            <select className="input sm" value={layout.variants?.field ?? ""} aria-label="A layout for each" onChange={(e) => {
              setEditing(null);
              save({ ...layout, variants: e.target.value ? { field: e.target.value, layouts: {} } : undefined });
            }}>
              <option value="">(one layout for every record)</option>
              {variantFields.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </label>
          <button className="btn sm ghost designer-reset" disabled={!editable} onClick={() => setBlocks(editing === null ? defaultBlocks(fields) : copyBlocks(layout.blocks))}>
            Reset to the default layout</button>
        </aside>
        <div className="designer-canvas">
          <div className="designer-sample">
            {vField && (
              <select className="input sm" aria-label="Layout being edited" value={editing ?? ""} onChange={(e) => { setEditing(e.target.value === "" ? null : e.target.value); setRecId(""); }}>
                <option value="">Every record (the default layout)</option>
                {kinds.map((k) => <option key={k} value={k}>{kindLabel(k)}{layout.variants?.layouts[k] ? " · its own layout" : ""}</option>)}
              </select>
            )}
            <span className="muted small">Shown on</span>
            <select className="input sm" value={rec.id} aria-label="Record to preview" onChange={(e) => setRecId(e.target.value)} disabled={!sample.length}>
              {sample.length ? sample.map((r) => <option key={r.id} value={r.id}>{recordTitle(fields, r)}</option>) : <option value="__preview">(no records of this kind yet)</option>}
            </select>
          </div>
          {editing !== null && (
            <div className="designer-variant-note">
              {own ? <>Records whose {vField?.name} is <b>{kindLabel(editing)}</b> have this layout of their own.
                <button className="btn sm" onClick={() => { const { [editing]: _gone, ...rest } = layout.variants!.layouts; save({ ...layout, variants: { ...layout.variants!, layouts: rest } }); }}>Use the default layout</button></>
                : <>Records whose {vField?.name} is <b>{kindLabel(editing)}</b> use the default layout.
                <button className="btn sm primary" onClick={() => save({ ...layout, variants: { ...layout.variants!, layouts: { ...layout.variants!.layouts, [editing]: copyBlocks(layout.blocks) } } })}>
                  Give them a layout of their own</button></>}
            </div>
          )}
          <article className={`page-paper designing ${editable ? "" : "readonly"}`}>
            <header className="page-title"><PageTitle fields={fields} rec={rec} canEdit={false} onUpdate={() => undefined} /></header>
            <PageBlocks blocks={shownBlocks} fields={fields} rec={rec} canEdit={false} onUpdate={() => undefined} design={design} />
            {!blocks.length && <p className="muted">Nothing on the page yet: show fields from the list.</p>}
          </article>
        </div>
      </div>
    </div>
  );
}

/** Adding blocks: headings, dividers, sections, tabs, numbers side by side, files, a related list, the other fields. */
function AddBlocks({ ds, fields, blocks, disabled, onAdd }: { ds: Dataset; fields: Field[]; blocks: PageBlock[]; disabled: boolean; onAdd: (b: Omit<PageBlock, "id">) => void }) {
  const [pick, setPick] = useState<{ what: "files" | "related" | "stats"; anchor: HTMLElement } | null>(null);
  const [inbound, setInbound] = useState<Inbound[] | null>(null);
  useEffect(() => { if (pick?.what === "related" && !inbound) api.get<Inbound[]>(ds.kind === "combo" ? `/combos/${ds.id}/inbound` : `/tables/${ds.id}/inbound`).then(setInbound).catch(() => setInbound([])); }, [pick, ds.id, inbound]);
  const fileFields = fields.filter((f) => f.type === "attachment" || (f.type === "link" && f.options._filesTable));
  const numbers = fields.filter((f) => valueType(f) === "number" && f.type !== "rating");
  const open = (what: "files" | "related" | "stats") => (e: { currentTarget: HTMLElement }) => setPick({ what, anchor: e.currentTarget });
  let menu: ReactNode = null;
  if (pick?.what === "files") {
    menu = fileFields.length ? fileFields.map((f) => <button key={f.id} className="menu-item" onClick={() => { setPick(null); onAdd({ type: "files", field: f.id }); }}><FieldIcon type={f.type} /> {f.name}</button>)
      : <div className="muted pad small">No attachment field, or link to a files table, yet</div>;
  } else if (pick?.what === "related") {
    menu = inbound === null ? <div className="muted pad">Loading…</div> : inbound.length ? inbound.map((i) => (
      <button key={`${i.field_id}:${i.to ?? ""}`} className="menu-item" onClick={() => { setPick(null); onAdd({ type: "related", table: i.table_id, field: i.field_id, text: i.table_name, display: "list", ...(i.to ? { to: i.to } : {}) }); }}>
        <Icon name="link" size={14} /> <span><b>{i.table_name}</b><span className="muted small"> through {i.field_name}{i.to ? ` (for ${ds.sources?.find((s) => s.table_id === i.to)?.name ?? "its"} records)` : ""}</span></span></button>))
      : <div className="muted pad small">No table links to this one yet: add a link to “{ds.name}” in another table.</div>;
  } else if (pick?.what === "stats") {
    menu = numbers.length ? <button className="menu-item" onClick={() => { setPick(null); onAdd({ type: "stats", items: numbers.slice(0, 3).map((f) => ({ field: f.id, view: "stat" })) }); }}>
      <Icon name="chart" size={14} /> Numbers side by side (choose them with ⚙)</button> : <div className="muted pad small">No number, rollup or formula giving a number yet</div>;
  }
  return (
    <div className="designer-add">
      <button className="btn sm" disabled={disabled} onClick={() => onAdd({ type: "heading", text: "" })}>Heading</button>
      <button className="btn sm" disabled={disabled} onClick={() => onAdd({ type: "divider" })}>Divider</button>
      <button className="btn sm" disabled={disabled} onClick={() => onAdd({ type: "section", text: "Section", blocks: [] })}>Section</button>
      <button className="btn sm" disabled={disabled} onClick={() => onAdd({ type: "tabs", tabs: [{ id: newId(), text: "Overview", blocks: [] }, { id: newId(), text: "Details", blocks: [] }] })}>Tabs</button>
      <button className="btn sm" disabled={disabled} onClick={open("related")}>Related list</button>
      <button className="btn sm" disabled={disabled} onClick={open("files")}>Files</button>
      <button className="btn sm" disabled={disabled} onClick={open("stats")}>Numbers</button>
      {!allBlocks(blocks).some((b) => b.type === "properties") && <button className="btn sm" disabled={disabled} title="Every field not placed elsewhere, as a list"
        onClick={() => onAdd({ type: "properties" })}>Other fields</button>}
      {pick && <Popover anchor={pick.anchor} onClose={() => setPick(null)} width={280}><div className="menu-list">{menu}</div></Popover>}
    </div>
  );
}

/** What stays under the title, the outline, and the template new records' page bodies start as. */
function PageSettings({ fields, layout, onChange }: { fields: Field[]; layout: PageLayout; onChange: (p: PageLayout) => void }) {
  const header = new Set(layout.header ?? []);
  const p = primaryField(fields);
  const candidates = pageFields(fields).filter((f) => f.id !== p?.id && !["long_text", "attachment", "button"].includes(f.type));
  const longTexts = fields.filter((f) => f.type === "long_text" && !f.system_key);
  const [tpl, setTpl] = useState(layout.template ?? "");
  useEffect(() => setTpl(layout.template ?? ""), [layout.template]);
  const ta = useRef<HTMLTextAreaElement>(null);
  const place = (id: string) => {
    const t = ta.current, at = t ? t.selectionStart : tpl.length;
    const before = tpl.slice(0, at), after = tpl.slice(at);
    setTpl(`${before}${before && !before.endsWith("\n") ? "\n" : ""}{{field:${id}}}\n${after}`);
  };
  return (
    <div className="designer-settings">
      <details>
        <summary>Under the title{header.size ? ` (${header.size})` : ""}</summary>
        <p className="muted small">Shown under the title, and kept in view with it while scrolling.</p>
        {candidates.map((f) => (
          <label key={f.id} className="inline-check small"><input type="checkbox" checked={header.has(f.id)}
            onChange={(e) => onChange({ ...layout, header: e.target.checked ? [...(layout.header ?? []), f.id] : (layout.header ?? []).filter((x) => x !== f.id) })} />
            <FieldIcon type={f.type} /> {f.name}</label>
        ))}
      </details>
      <label className="inline-check small"><input type="checkbox" checked={!!layout.outline} onChange={(e) => onChange({ ...layout, outline: e.target.checked || undefined })} />
        An outline of its headings, sections and tabs</label>
      <details>
        <summary>Template for new records{layout.template ? " (on)" : ""}</summary>
        <p className="muted small">A new record's page body starts as this (Markdown: ## for headings, - for lists; fields placed on their own line).</p>
        {longTexts.length > 1 && (
          <label className="stack small">Into
            <select className="input sm" value={layout.templateField ?? ""} onChange={(e) => onChange({ ...layout, templateField: e.target.value || undefined })}>
              <option value="">The page body</option>
              {longTexts.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </label>
        )}
        <textarea ref={ta} className="input designer-template" rows={7} value={tpl} placeholder={"## Scope\n\n## Materials\n\n## Notes"} aria-label="Template"
          onChange={(e) => setTpl(e.target.value)} onBlur={() => tpl !== (layout.template ?? "") && onChange({ ...layout, template: tpl.trim() ? tpl : undefined })} />
        <select className="input sm" value="" aria-label="Place a field in the template" onChange={(e) => { if (e.target.value) { place(e.target.value); setTimeout(() => ta.current?.focus()); } }}>
          <option value="">Place a field…</option>
          {pageFields(fields).filter((f) => f.type !== "long_text" && f.id !== p?.id).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      </details>
    </div>
  );
}

/** A block's ⚙: how it's shown, its size, where it is, and when it shows. */
function BlockMenu({ b, fields, blocks, onPatch, onMove, onPlace, sized }: {
  b: PageBlock; fields: Field[]; blocks: PageBlock[]; sized: boolean;
  onPatch: (p: Partial<PageBlock>) => void; onMove: (d: -1 | 1) => void; onPlace: (place: Place) => void;
}) {
  const f = b.field ? fields.find((x) => x.id === b.field) : undefined;
  const ways = f && b.type === "field" ? displaysFor(f, "record") : [];
  const here = placeOf(blocks, b.id);
  const targets = places(blocks).filter((p) => p.place === null || !allBlocks([b]).some((x) => p.place!.split(":")[1] === x.id));
  const [cond, setCond] = useState(!!b.when?.items.length);
  return (
    <div className="pop-body block-menu">
      {ways.length > 1 && (
        <label className="stack small">Show as
          <select className="input sm" aria-label={`Show ${f!.name} as`} value={b.view && ways.includes(b.view) ? b.view : ""}
            onChange={(e) => onPatch({ view: (e.target.value || undefined) as FieldView | undefined })}>
            <option value="">{VIEW_LABEL[f!.options.views?.record && ways.includes(f!.options.views.record) ? f!.options.views.record : ways[0]]} (the field's own)</option>
            {ways.map((v) => <option key={v} value={v}>{VIEW_LABEL[v]}</option>)}
          </select>
        </label>
      )}
      {f?.type === "long_text" && (b.type === "field" || b.type === "doc") && (
        <label className="inline-check small"><input type="checkbox" checked={b.type === "doc"} onChange={(e) => onPatch({ type: e.target.checked ? "doc" : "field", view: undefined })} />
          The page's body (formatted, with fields placed in its text)</label>
      )}
      {b.type === "section" && (<>
        <label className="stack small">Title<input className="input sm" defaultValue={b.text ?? ""} aria-label="Section title" onBlur={(e) => onPatch({ text: e.target.value })} /></label>
        <label className="inline-check small"><input type="checkbox" checked={!!b.closed} onChange={(e) => onPatch({ closed: e.target.checked || undefined })} /> Starts folded</label>
      </>)}
      {b.type === "tabs" && <TabsEditor b={b} onPatch={onPatch} />}
      {b.type === "related" && <RelatedEditor b={b} onPatch={onPatch} />}
      {b.type === "stats" && (
        <div className="stack small">Numbers
          {fields.filter((x) => valueType(x) === "number" && x.type !== "rating").map((x) => {
            const it = (b.items ?? []).find((i) => i.field === x.id);
            return (
              <div key={x.id} className="row block-stat-row">
                <label className="inline-check small"><input type="checkbox" checked={!!it}
                  onChange={(e) => onPatch({ items: e.target.checked ? [...(b.items ?? []), { field: x.id, view: "stat" }] : (b.items ?? []).filter((i) => i.field !== x.id) })} /> {x.name}</label>
                {it && <select className="input sm" value={it.view} aria-label={`${x.name} as`}
                  onChange={(e) => onPatch({ items: (b.items ?? []).map((i) => (i.field === x.id ? { ...i, view: e.target.value as "stat" | "bar" } : i)) })}>
                  <option value="stat">Big number</option><option value="bar">Progress bar</option></select>}
              </div>
            );
          })}
        </div>
      )}
      {b.type === "files" && (
        <label className="stack small">Files of
          <select className="input sm" value={b.field} onChange={(e) => onPatch({ field: e.target.value })}>
            {fields.filter((x) => x.type === "attachment" || (x.type === "link" && x.options._filesTable)).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        </label>
      )}
      {sized && (<>
        <span className="small muted">Width</span>
        <span className="segmented sm block-span" role="group" aria-label="Width">
          {SPANS.map((x) => <button key={x.n} type="button" className={(b.span ?? 4) === x.n ? "active" : ""} title={`${x.label === "Full" ? "The whole" : x.label} width`}
            onClick={() => onPatch({ span: x.n === 4 ? undefined : x.n })}>{x.label}</button>)}
        </span>
        <span className="small muted">Height: {b.height ? `${b.height} px` : "as tall as its content"} (drag its bottom edge)</span>
        {b.height && <button type="button" className="btn sm" onClick={() => onPatch({ height: undefined })}>As tall as its content</button>}
      </>)}
      <label className="stack small">Put in
        <select className="input sm" value={here?.place ?? ""} aria-label="Put in" onChange={(e) => onPlace(e.target.value === "" ? null : e.target.value)}>
          {targets.map((p) => <option key={p.place ?? ""} value={p.place ?? ""}>{p.label}</option>)}
        </select>
      </label>
      <div className="row">
        <button type="button" className="btn sm" onClick={() => onMove(-1)}><Icon name="up" size={13} /> Up</button>
        <button type="button" className="btn sm" onClick={() => onMove(1)}><Icon name="down" size={13} /> Down</button>
      </div>
      <label className="inline-check small"><input type="checkbox" checked={cond} onChange={(e) => { setCond(e.target.checked); if (!e.target.checked) onPatch({ when: undefined }); }} />
        Show only when…{b.when?.items.length ? ` (${countConditions(b.when)})` : ""}</label>
      {cond && <div className="block-when-editor"><FilterEditor fields={fields} filter={b.when ?? { conjunction: "and", items: [] }}
        onChange={(g: FilterGroup) => onPatch({ when: g.items.length ? g : undefined })} /></div>}
    </div>
  );
}

function TabsEditor({ b, onPatch }: { b: PageBlock; onPatch: (p: Partial<PageBlock>) => void }) {
  const tabs = b.tabs ?? [];
  return (
    <div className="stack small">Tabs
      {tabs.map((t, i) => (
        <div key={t.id} className="row">
          <input className="input sm" defaultValue={t.text} aria-label={`Tab ${i + 1} name`}
            onBlur={(e) => e.target.value !== t.text && onPatch({ tabs: tabs.map((x) => (x.id === t.id ? { ...x, text: e.target.value || "Tab" } : x)) })} />
          <button type="button" className="icon-btn sm" disabled={tabs.length < 2} aria-label={`Remove tab ${t.text}`} title="Remove the tab (its blocks go with it)"
            onClick={() => onPatch({ tabs: tabs.filter((x) => x.id !== t.id) })}><Icon name="trash" size={13} /></button>
        </div>
      ))}
      {tabs.length < 12 && <button type="button" className="btn sm" onClick={() => onPatch({ tabs: [...tabs, { id: newId(), text: `Tab ${tabs.length + 1}`, blocks: [] }] })}>
        <Icon name="plus" size={13} /> Add a tab</button>}
    </div>
  );
}

function RelatedEditor({ b, onPatch }: { b: PageBlock; onPatch: (p: Partial<PageBlock>) => void }) {
  const info = useTableInfo(b.table);
  const chosen = new Set(b.fields ?? []);
  return (<>
    <label className="stack small">Title<input className="input sm" defaultValue={b.text ?? ""} aria-label="Related list title" onBlur={(e) => onPatch({ text: e.target.value || undefined })} /></label>
    <label className="stack small">Show as
      <select className="input sm" value={b.display ?? "list"} aria-label="Show the related records as" onChange={(e) => onPatch({ display: e.target.value as "list" | "table" | "cards" })}>
        <option value="list">List</option><option value="table">Table</option><option value="cards">Cards</option>
      </select>
    </label>
    <label className="stack small">Which records, in what order
      <select className="input sm" value={b.filterView ?? ""} onChange={(e) => onPatch({ filterView: e.target.value || undefined })}>
        <option value="">All of them</option>
        {(info?.views ?? []).filter((v) => v.type !== "chart").map((v) => <option key={v.id} value={v.id}>As in the view “{v.name}”</option>)}
      </select>
    </label>
    <label className="stack small">At most<input className="input sm" type="number" min={1} max={500} defaultValue={b.limit ?? 100}
      onBlur={(e) => onPatch({ limit: Math.max(1, Math.min(500, Number(e.target.value) || 100)) })} /></label>
    <div className="stack small">Fields to show
      <div className="view-fields-pick">
        {(info?.fields ?? []).filter((x) => x.type !== "button" && x.type !== "record_id").map((x) => (
          <label key={x.id} className="inline-check small"><input type="checkbox" checked={chosen.has(x.id)}
            onChange={(e) => { const n = (b.fields ?? []).filter((y) => y !== x.id); if (e.target.checked) n.push(x.id); onPatch({ fields: n.length ? n : undefined }); }} /> {x.name}</label>
        ))}
      </div>
    </div>
  </>);
}

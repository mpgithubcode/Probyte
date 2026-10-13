import { useEffect, useRef, useState } from "react";
import type { Field, View, ViewConfig, ViewType } from "../types";
import { countConditions, hiddenIds, isDateField, setFieldShown, viewFilter, viewGroups, viewSorts } from "../util";
import { BoardOptions, FilterEditor, GroupEditorList, ListOptions, SortEditor } from "./ViewControls";
import { listLayout } from "./ListView";
import { FieldIcon, Icon } from "./Icon";
import { Popover, usePopover } from "./Overlay";
import { PAGE_SIZES, PAGED_VIEWS } from "./Pager";
import { listTemplates, readTemplateFile, type TemplateInfo } from "./Templates";

export const VIEW_TYPES: { type: ViewType; label: string; icon: string; desc: string }[] = [
  { type: "grid", label: "Table", icon: "grid", desc: "Rows and columns" },
  { type: "kanban", label: "Board", icon: "kanban", desc: "Cards grouped by a property" },
  { type: "timeline", label: "Timeline", icon: "timeline", desc: "Date ranges as bars" },
  { type: "calendar", label: "Calendar", icon: "calendar", desc: "Items on a month grid" },
  { type: "list", label: "List", icon: "list", desc: "Clean, minimal rows" },
  { type: "gallery", label: "Gallery", icon: "gallery", desc: "Cards, with optional cover image" },
  { type: "chart", label: "Chart", icon: "chart", desc: "Bar, line or donut chart" },
];

/** The view tabs: click to switch, drag to reorder, ⋯ (or double-click) on the open one for its settings. */
export function ViewTabs({ views, active, count, onSelect, onCreate, onReorder, onSettings, onFromTemplate }: {
  count?: number;   // how many records the open view shows
  views: View[]; active?: View; onSelect: (v: View) => void; onCreate: (t: ViewType) => void;
  onReorder: (ids: string[]) => void; onSettings: (v: View) => void;
  onFromTemplate?: (t: { template_id?: string; body?: Record<string, unknown> }) => Promise<void>;
}) {
  const add = usePopover();
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(null);
  const [tplErr, setTplErr] = useState("");
  const tplFile = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!add.anchor || !onFromTemplate) return;
    setTplErr("");
    listTemplates("view").then(setTemplates).catch(() => setTemplates([]));
  }, [add.anchor]);
  const useTemplate = async (t: { template_id?: string; body?: Record<string, unknown> }) => {
    try { await onFromTemplate!(t); add.close(); } catch (e) { setTplErr((e as Error).message); }
  };
  const fromFile = async (f: File | undefined) => {
    if (!f) return;
    try {
      const t = await readTemplateFile(f);
      if (t.kind !== "view") throw new Error("That's a table template: use it from New table");
      await useTemplate({ body: t.body });
    } catch (e) { setTplErr((e as Error).message); }
    if (tplFile.current) tplFile.current.value = "";
  };
  const [drag, setDrag] = useState<string | null>(null);
  const [order, setOrder] = useState<string[] | null>(null);  // while dragging
  const shown = order ? order.map((id) => views.find((v) => v.id === id)!).filter(Boolean) : views;

  const over = (id: string) => {
    if (!drag || drag === id) return;
    const ids = (order ?? views.map((v) => v.id)).filter((x) => x !== drag);
    ids.splice(ids.indexOf(id) + (shown.findIndex((v) => v.id === drag) < shown.findIndex((v) => v.id === id) ? 1 : 0), 0, drag);
    setOrder(ids);
  };
  const drop = () => {
    if (order && order.join() !== views.map((v) => v.id).join()) onReorder(order);
    setDrag(null); setOrder(null);
  };

  return (
    <div className="view-tabs">
      {shown.map((v) => {
        const icon = VIEW_TYPES.find((t) => t.type === v.type)?.icon ?? "grid";
        const on = active?.id === v.id;
        return (
          <div key={v.id} className={`view-tab ${on ? "active" : ""} ${drag === v.id ? "dragging" : ""}`} role="tab" aria-selected={on}
            draggable onDragStart={(e) => { setDrag(v.id); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", v.id); }}
            onDragOver={(e) => { if (drag) { e.preventDefault(); over(v.id); } }} onDrop={(e) => { e.preventDefault(); drop(); }} onDragEnd={drop}
            onClick={() => !on && onSelect(v)} onDoubleClick={() => onSettings(v)} title={on ? "Double-click or ⋯ for view settings; drag to reorder" : "Drag to reorder"}>
            <Icon name={icon} size={14} /> <span className="view-tab-name">{v.name}</span>
            {countConditions(viewFilter(v.config)) > 0 && <span className="view-tab-filtered" title="This view is filtered" aria-label="filtered" />}
            {on && count !== undefined && <span className="view-tab-count" aria-label={`${count} records`}>{count.toLocaleString()}</span>}
            {on && <button className="view-tab-more" aria-label="View settings" title="View settings"
              onClick={(e) => { e.stopPropagation(); onSettings(v); }}><Icon name="more" size={14} /></button>}
          </div>
        );
      })}
      <button className="view-tab add" onClick={add.open} title="Add view"><Icon name="plus" size={14} /></button>
      {add.anchor && (
        <Popover anchor={add.anchor} onClose={add.close} width={260}>
          <div className="menu-list">
            <div className="menu-heading">Add a view</div>
            {VIEW_TYPES.map((t) => (
              <button key={t.type} className="menu-item view-type-item" onClick={() => { onCreate(t.type); add.close(); }}>
                <Icon name={t.icon} size={16} />
                <span><b>{t.label}</b><span className="muted small">{t.desc}</span></span>
              </button>
            ))}
            {onFromTemplate && (<>
              <div className="menu-heading">From a template</div>
              {templates?.map((t) => (
                <button key={t.id} className="menu-item" onClick={() => useTemplate({ template_id: t.id })}>
                  <Icon name={VIEW_TYPES.find((x) => x.type === t.summary.type)?.icon ?? "grid"} size={16} /> {t.name}
                </button>
              ))}
              {templates && !templates.length && <div className="muted small pad">No view templates yet: save one from a view's settings.</div>}
              <input ref={tplFile} type="file" accept=".json,application/json" hidden onChange={(e) => fromFile(e.target.files?.[0])} />
              <button className="menu-item" onClick={() => tplFile.current?.click()}><Icon name="upload" size={16} /> Use a template file…</button>
              {tplErr && <div className="error small pad">{tplErr}</div>}
            </>)}
          </div>
        </Popover>
      )}
    </div>
  );
}

/** Views whose rows or cards can be made smaller or larger. */
export const DENSITY_VIEWS = new Set<ViewType>(["grid", "list", "kanban", "gallery"]);

/** A toolbar choice shown as "Label: current value" that opens a short menu (instead of a bare dropdown, which doesn't say
 * what it's for). */
export function ChoiceMenu<T extends string | number | null>({ icon, label, title, value, options, onChange }: {
  icon: string; label: string; title?: string; value: T;
  options: { value: T; label: string; hidden?: boolean; plain?: boolean }[];   // hidden: shown as the current value only, not offered;
                                                                               // plain: the default (the button shows just its name)
  onChange: (v: T) => void;
}) {
  const pop = usePopover();
  const current = options.find((o) => o.value === value);
  return (
    <>
      <button className="tool-btn choice-btn" onClick={pop.open} title={title} aria-label={`${title ?? label}: ${current?.label ?? "none"}`} aria-haspopup="menu">
        <Icon name={icon} size={14} />
        {current?.plain ? <span>{label}</span> : <><span className="choice-label">{label}:</span> <b>{current?.label ?? "—"}</b></>}
        <Icon name="down" size={12} className="choice-chev" />
      </button>
      {pop.anchor && (
        <Popover anchor={pop.anchor} onClose={pop.close} width={220}>
          <div className="menu-list" role="menu" aria-label={title ?? label}>
            {title && <div className="menu-heading">{title}</div>}
            {options.filter((o) => !o.hidden).map((o) => (
              <button key={String(o.value)} role="menuitemradio" aria-checked={o.value === value}
                className={`menu-item ${o.value === value ? "active" : ""}`} onClick={() => { onChange(o.value); pop.close(); }}>
                {o.label}{o.value === value && <span className="tick">✓</span>}
              </button>
            ))}
            {!options.some((o) => !o.hidden) && <div className="muted small pad">Nothing to choose from yet.</div>}
          </div>
        </Popover>
      )}
    </>
  );
}

const DENSITIES = [{ value: "compact", label: "Compact" }, { value: "comfortable", label: "Comfortable" }, { value: "roomy", label: "Roomy" }] as const;

/** How rows and cards are laid out: their density, and how many rows a page has (one menu, to keep the toolbar short). */
function LayoutMenu({ config, setConfig, density, paged }: {
  config: ViewConfig; setConfig: (p: Partial<ViewConfig>) => void; density: boolean; paged: boolean;
}) {
  const pop = usePopover();
  const d = config.density ?? "comfortable";
  const size = config.pageSize ?? 0;
  // only what differs from the usual (comfortable, all rows)
  const summary = [density && d !== "comfortable" && DENSITIES.find((x) => x.value === d)!.label, paged && size ? `${size} per page` : null]
    .filter(Boolean).join(" · ");
  const item = (on: boolean, label: string, run: () => void) => (
    <button key={label} role="menuitemradio" aria-checked={on} className={`menu-item ${on ? "active" : ""}`} onClick={run}>
      {label}{on && <span className="tick">✓</span>}
    </button>
  );
  return (
    <>
      <button className={`tool-btn choice-btn ${d !== "comfortable" || size ? "on" : ""}`} onClick={pop.open} aria-label={`Layout: ${summary || "comfortable, all rows"}`}
        aria-haspopup="menu" title="Row and card size, rows per page">
        <Icon name="density" size={14} /> {summary ? <><span className="choice-label">Layout:</span> <b className="layout-summary">{summary}</b></> : <span>Layout</span>}<Icon name="down" size={12} className="choice-chev" />
      </button>
      {pop.anchor && (
        <Popover anchor={pop.anchor} onClose={pop.close} width={220}>
          <div className="menu-list" role="menu" aria-label="Layout">
            {density && (<>
              <div className="menu-heading">Row and card size</div>
              {DENSITIES.map((x) => item(d === x.value, x.label, () => setConfig({ density: x.value === "comfortable" ? undefined : x.value })))}
            </>)}
            {paged && (<>
              <div className="menu-heading">Rows per page</div>
              {item(!size, "All rows", () => setConfig({ pageSize: undefined }))}
              {PAGE_SIZES.map((n) => item(size === n, `${n} per page`, () => setConfig({ pageSize: n })))}
            </>)}
          </div>
        </Popover>
      )}
    </>
  );
}

export function FieldSelect({ fields, value, onChange, placeholder, allowNone }: {
  fields: Field[]; value?: string | null; onChange: (id: string | null) => void; placeholder: string; allowNone?: boolean;
}) {
  return (
    <select className="input sm" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="" disabled={!allowNone}>{placeholder}</option>
      {fields.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
    </select>
  );
}

export function ViewOptions({ view, fields, config, setConfig, search, setSearch, onAdd, addLabel }: {
  view: View; fields: Field[]; config: ViewConfig; setConfig: (p: Partial<ViewConfig>) => void;
  search: string; setSearch: (s: string) => void;
  onAdd?: (e: { currentTarget: HTMLElement }) => void;   // "New record" (left of the search box); left out when the viewer can't add
  addLabel?: string;    // files tables: "Upload"
}) {
  const filterPop = usePopover();
  const sortPop = usePopover();
  const hidePop = usePopover();
  const groupPop = usePopover();
  const boardPop = usePopover();
  const listPop = usePopover();
  const filter = viewFilter(config);
  const nFilters = countConditions(filter);
  const sorts = viewSorts(config);
  const groups = viewGroups(config);
  const hiddenSet = hiddenIds(fields, config);
  const hidden = (config.hidden ?? []).filter((id) => fields.some((f) => f.id === id));  // hidden on purpose (not Record ID)
  const dateFields = fields.filter(isDateField);
  const selectFields = fields.filter((f) => f.type === "single_select");


  return (
    <div className="view-options">
      {onAdd && <button className="btn sm primary new-record-btn" onClick={onAdd} title={addLabel ? "Upload files" : "New record"}>
        <Icon name={addLabel ? "upload" : "plus"} size={14} /> <span>{addLabel ?? "New"}</span></button>}
      <div className="search">
        <Icon name="search" size={14} />
        <input placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      <button className={`tool-btn ${nFilters ? "on" : ""}`} onClick={filterPop.open}>
        <Icon name="filter" size={14} /> Filter{nFilters ? ` (${nFilters})` : ""}
      </button>
      {filterPop.anchor && (
        <Popover anchor={filterPop.anchor} onClose={filterPop.close} width={620}>
          <FilterEditor fields={fields} filter={filter} onChange={(g) => setConfig({ filter: g })} />
        </Popover>
      )}

      <button className={`tool-btn ${sorts.length ? "on" : ""}`} onClick={sortPop.open}>
        <Icon name="sort" size={14} /> Sort{sorts.length > 1 ? ` (${sorts.length})` : ""}
      </button>
      {sortPop.anchor && (
        <Popover anchor={sortPop.anchor} onClose={sortPop.close} width={480}>
          <SortEditor fields={fields} sorts={sorts} onChange={(x) => setConfig({ sorts: x })} />
        </Popover>
      )}

      {(view.type === "grid" || view.type === "list" || view.type === "gallery") && (
        <>
          <button className={`tool-btn ${groups.length ? "on" : ""}`} onClick={groupPop.open}>
            <Icon name="group" size={14} /> Group{groups.length ? ` (${groups.length})` : ""}
          </button>
          {groupPop.anchor && (
            <Popover anchor={groupPop.anchor} onClose={groupPop.close} width={480}>
              <GroupEditorList fields={fields} groups={groups} onChange={(x) => setConfig({ groups: x })} />
            </Popover>
          )}
        </>
      )}

      <button className={`tool-btn ${hidden.length ? "on" : ""}`} onClick={hidePop.open}>
        <Icon name="eye" size={14} /> {hidden.length ? `${hidden.length} hidden` : "Fields"}
      </button>
      {hidePop.anchor && (
        <Popover anchor={hidePop.anchor} onClose={hidePop.close} width={300}>
          <div className="pop-body fields-pop">
            <p className="muted small">Show or hide fields, and rename them in this view only.</p>
            {fields.map((f) => (
              <div key={f.id} className="field-pop-row">
                <input type="checkbox" checked={!hiddenSet.has(f.id)} disabled={f.is_primary} aria-label={`Show ${f.realName ?? f.name}`}
                  onChange={(e) => setConfig(setFieldShown(f, e.target.checked, config))} />
                <FieldIcon type={f.type} />
                <input className="input sm" value={config.fieldNames?.[f.id] ?? ""} placeholder={f.realName ?? f.name}
                  aria-label={`Name of ${f.realName ?? f.name} in this view`}
                  onChange={(e) => {
                    const names = { ...(config.fieldNames ?? {}) };
                    if (e.target.value) names[f.id] = e.target.value; else delete names[f.id];
                    setConfig({ fieldNames: names });
                  }} />
              </div>
            ))}
          </div>
        </Popover>
      )}

      {(view.type === "kanban" || view.type === "list" || view.type === "calendar" || view.type === "timeline" || view.type === "gallery"
        || PAGED_VIEWS.has(view.type) || DENSITY_VIEWS.has(view.type)) && <span className="tool-sep" />}
      {view.type === "kanban" && (
        <ChoiceMenu icon="group" label="Columns" title="Group cards by" value={config.groupBy ?? null}
          options={[{ value: null, label: "Choose…", hidden: true }, ...selectFields.map((f) => ({ value: f.id, label: f.name }))]}
          onChange={(id) => setConfig({ groupBy: id ?? undefined })} />
      )}
      {view.type === "list" && (
        <>
          <button className={`tool-btn ${config.listProps?.length ? "on" : ""}`} onClick={listPop.open}>
            <Icon name="list" size={14} /> Properties
          </button>
          {listPop.anchor && (
            <Popover anchor={listPop.anchor} onClose={listPop.close} width={400}>
              <ListOptions layout={listLayout(fields.filter((f) => !hiddenSet.has(f.id)), config)} config={config} setConfig={setConfig}
                extra={fields.filter((f) => !hiddenSet.has(f.id) && f.type === "long_text" && !config.listProps?.some((p) => p.field === f.id))} />
            </Popover>
          )}
        </>
      )}
      {view.type === "kanban" && (
        <>
          <button className={`tool-btn ${config.hideEmptyColumns || config.hideUnassigned || config.hideBlankCards ? "on" : ""}`} onClick={boardPop.open}>
            <Icon name="settings" size={14} /> Board
          </button>
          {boardPop.anchor && (
            <Popover anchor={boardPop.anchor} onClose={boardPop.close} width={300}>
              <BoardOptions config={config} groupField={fields.find((f) => f.id === config.groupBy)} setConfig={setConfig} />
            </Popover>
          )}
        </>
      )}
      {(view.type === "calendar" || view.type === "timeline") && (
        <>
          <ChoiceMenu icon="calendar" label="Starts" title="The date each item is placed on" value={config.dateField ?? null}
            options={[{ value: null, label: "Choose…", hidden: true }, ...dateFields.map((f) => ({ value: f.id, label: f.name }))]}
            onChange={(id) => setConfig({ dateField: id ?? undefined })} />
          <ChoiceMenu icon="right" label="Ends" title="The date each item ends on" value={config.endField ?? null}
            options={[{ value: null, label: "No end date" }, ...dateFields.map((f) => ({ value: f.id, label: f.name }))]}
            onChange={(id) => setConfig({ endField: id })} />
        </>
      )}
      {view.type === "gallery" && (
        <ChoiceMenu icon="gallery" label="Cover" title="Cover image" value={config.coverField ?? null}
          options={[{ value: null, label: "No cover", plain: true },
            ...fields.filter((f) => f.type === "attachment" || f.type === "url" || f.type === "text").map((f) => ({ value: f.id, label: f.name }))]}
          onChange={(id) => setConfig({ coverField: id })} />
      )}
      {(view.type === "calendar" || view.type === "timeline" || view.type === "kanban" || view.type === "gallery" || view.type === "list") && (
        <ChoiceMenu icon="palette" label="Color" title="Color by" value={config.colorField ?? null}
          options={[{ value: null, label: "None", plain: true }, ...selectFields.map((f) => ({ value: f.id, label: f.name }))]}
          onChange={(id) => setConfig({ colorField: id })} />
      )}
      {(DENSITY_VIEWS.has(view.type) || PAGED_VIEWS.has(view.type)) && (
        <LayoutMenu config={config} setConfig={setConfig} density={DENSITY_VIEWS.has(view.type)} paged={PAGED_VIEWS.has(view.type)} />
      )}
    </div>
  );
}

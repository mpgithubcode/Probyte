import { useState } from "react";
import type { Field, View, ViewConfig, ViewType } from "../types";
import { countConditions, hiddenIds, isDateField, setFieldShown, viewFilter, viewGroups, viewSorts } from "../util";
import { BoardOptions, FilterEditor, GroupEditorList, ListOptions, SortEditor } from "./ViewControls";
import { listLayout } from "./ListView";
import { FieldIcon, Icon } from "./Icon";
import { Popover, usePopover } from "./Overlay";
import { PAGE_SIZES, PAGED_VIEWS } from "./Pager";

export const VIEW_TYPES: { type: ViewType; label: string; icon: string; desc: string }[] = [
  { type: "grid", label: "Table", icon: "grid", desc: "Rows and columns" },
  { type: "kanban", label: "Board", icon: "kanban", desc: "Cards grouped by a property" },
  { type: "timeline", label: "Timeline", icon: "timeline", desc: "Date ranges as bars" },
  { type: "calendar", label: "Calendar", icon: "calendar", desc: "Items on a month grid" },
  { type: "list", label: "List", icon: "list", desc: "Clean, minimal rows" },
  { type: "gallery", label: "Gallery", icon: "gallery", desc: "Cards, with optional cover image" },
  { type: "chart", label: "Chart", icon: "chart", desc: "Bar, line or donut chart" },
];

export function ViewTabs({ views, active, onSelect, onCreate, onRename, onDelete }: {
  views: View[]; active?: View; onSelect: (v: View) => void; onCreate: (t: ViewType) => void;
  onRename: (v: View, name: string) => void; onDelete: (v: View) => void;
}) {
  const add = usePopover();
  const [menu, setMenu] = useState<{ view: View; anchor: HTMLElement } | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);

  return (
    <div className="view-tabs">
      {views.map((v) => {
        const icon = VIEW_TYPES.find((t) => t.type === v.type)?.icon ?? "grid";
        return renaming === v.id ? (
          <input key={v.id} className="input tab-rename" autoFocus defaultValue={v.name}
            onBlur={(e) => { if (e.target.value.trim()) onRename(v, e.target.value.trim()); setRenaming(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") setRenaming(null); }} />
        ) : (
          <button key={v.id} className={`view-tab ${active?.id === v.id ? "active" : ""}`}
            onClick={(e) => active?.id === v.id ? setMenu({ view: v, anchor: e.currentTarget }) : onSelect(v)}
            onDoubleClick={() => setRenaming(v.id)}>
            <Icon name={icon} size={14} /> {v.name}
          </button>
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
          </div>
        </Popover>
      )}
      {menu && (
        <Popover anchor={menu.anchor} onClose={() => setMenu(null)} width={180}>
          <div className="menu-list">
            <button className="menu-item" onClick={() => { setRenaming(menu.view.id); setMenu(null); }}><Icon name="edit" size={14} /> Rename</button>
            {views.length > 1 && (
              <button className="menu-item danger" onClick={() => { onDelete(menu.view); setMenu(null); }}><Icon name="trash" size={14} /> Delete view</button>
            )}
          </div>
        </Popover>
      )}
    </div>
  );
}

function FieldSelect({ fields, value, onChange, placeholder, allowNone }: {
  fields: Field[]; value?: string | null; onChange: (id: string | null) => void; placeholder: string; allowNone?: boolean;
}) {
  return (
    <select className="input sm" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="" disabled={!allowNone}>{placeholder}</option>
      {fields.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
    </select>
  );
}

export function ViewOptions({ view, fields, config, setConfig, search, setSearch, onAdd }: {
  view: View; fields: Field[]; config: ViewConfig; setConfig: (p: Partial<ViewConfig>) => void;
  search: string; setSearch: (s: string) => void;
  onAdd?: () => void;   // "New record" (left of the search box); left out when the viewer can't add
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
      {onAdd && <button className="btn sm primary new-record-btn" onClick={onAdd} title="New record"><Icon name="plus" size={14} /> <span>New</span></button>}
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

      {view.type === "kanban" && (
        <label className="tool-label"><Icon name="group" size={14} />
          <FieldSelect fields={selectFields} value={config.groupBy} onChange={(id) => setConfig({ groupBy: id ?? undefined })} placeholder="Group by…" />
        </label>
      )}
      {view.type === "list" && (
        <>
          <button className={`tool-btn ${config.listProps?.length ? "on" : ""}`} onClick={listPop.open}>
            <Icon name="list" size={14} /> Properties
          </button>
          {listPop.anchor && (
            <Popover anchor={listPop.anchor} onClose={listPop.close} width={400}>
              <ListOptions layout={listLayout(fields.filter((f) => !hiddenSet.has(f.id)), config)} config={config} setConfig={setConfig} />
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
          <label className="tool-label"><Icon name="calendar" size={14} />
            <FieldSelect fields={dateFields} value={config.dateField} onChange={(id) => setConfig({ dateField: id ?? undefined })} placeholder="Date field…" />
          </label>
          <label className="tool-label">→
            <FieldSelect fields={dateFields} value={config.endField} onChange={(id) => setConfig({ endField: id })} placeholder="No end date" allowNone />
          </label>
        </>
      )}
      {view.type === "gallery" && (
        <label className="tool-label" title="Cover image"><Icon name="gallery" size={14} />
          <FieldSelect fields={fields.filter((f) => f.type === "attachment" || f.type === "url" || f.type === "text")} value={config.coverField}
            onChange={(id) => setConfig({ coverField: id })} placeholder="No cover" allowNone />
        </label>
      )}
      {PAGED_VIEWS.has(view.type) && (
        <label className="tool-label" title="Rows per page"><Icon name="list" size={14} />
          <select className="input sm" value={config.pageSize ?? 0} onChange={(e) => setConfig({ pageSize: Number(e.target.value) || undefined })}>
            <option value={0}>All rows</option>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n} per page</option>)}
          </select>
        </label>
      )}
      {(view.type === "calendar" || view.type === "timeline" || view.type === "kanban" || view.type === "gallery" || view.type === "list") && (
        <label className="tool-label" title="Color by"><Icon name="palette" size={14} />
          <FieldSelect fields={selectFields} value={config.colorField} onChange={(id) => setConfig({ colorField: id })} placeholder="No color" allowNone />
        </label>
      )}
    </div>
  );
}

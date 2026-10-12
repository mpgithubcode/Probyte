import { useState } from "react";
import { api } from "../api";
import type { Field, View, ViewConfig, ViewType } from "../types";
import { countConditions, hiddenIds, isDateField, orderFields, setFieldShown, viewFilter, viewGroups, viewSorts } from "../util";
import { FieldIcon, Icon } from "./Icon";
import { listLayout } from "./ListView";
import { Modal } from "./Overlay";
import { PAGE_SIZES, PAGED_VIEWS } from "./Pager";
import { FieldSelect, VIEW_TYPES } from "./Toolbar";
import { BoardOptions, FilterEditor, GroupEditorList, ListOptions, SortEditor } from "./ViewControls";

type Tab = "general" | "layout" | "fields" | "filter" | "sort" | "group";

/** Everything about one view, in one window: its name and kind, layout, fields, filters, sorting and grouping, and
 * what can be done with it (duplicate, move, calendar link, delete). Changes apply as they're made. */
export function ViewSettings({ view, views, fields, setConfig, onClose, onRenamed, onTypeChanged, onDuplicate, onMove, onDelete, onCalendarLink }: {
  view: View; views: View[]; fields: Field[];
  setConfig: (p: Partial<ViewConfig>) => void;
  onClose: () => void;
  onRenamed: (name: string) => void;
  onTypeChanged: (v: View) => void;
  onDuplicate: () => void;
  onMove: (dir: -1 | 1) => void;
  onDelete?: () => void;            // not offered for the last view
  onCalendarLink?: () => void;      // tables / combined databases with dates
}) {
  const [tab, setTab] = useState<Tab>("general");
  const [name, setName] = useState(view.name);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [err, setErr] = useState("");
  const config = view.config;
  const filter = viewFilter(config), sorts = viewSorts(config), groups = viewGroups(config);
  const canGroup = view.type === "grid" || view.type === "list" || view.type === "gallery";
  const idx = views.findIndex((v) => v.id === view.id);

  const rename = () => {
    const n = name.trim();
    if (n && n !== view.name) onRenamed(n);
  };
  const changeType = async (type: ViewType) => {
    if (type === view.type) return;
    // with the current settings: changes made a moment ago may not be saved yet
    try { onTypeChanged(await api.patch<View>(`/views/${view.id}`, { type, config: view.config })); } catch (e) { setErr((e as Error).message); }
  };

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: "general", label: "General" }, { id: "layout", label: "Layout" }, { id: "fields", label: "Fields" },
    { id: "filter", label: "Filter", badge: countConditions(filter) }, { id: "sort", label: "Sort", badge: sorts.length },
    ...(canGroup ? [{ id: "group" as Tab, label: "Group", badge: groups.length }] : []),
  ];

  return (
    <Modal title={<span className="row"><Icon name={VIEW_TYPES.find((t) => t.type === view.type)?.icon ?? "grid"} size={16} /> View settings</span>}
      width={720} onClose={() => { rename(); onClose(); }}
      footer={<><span className="muted small">Changes are saved as you make them.</span><span className="spacer" />
        <button className="btn primary" onClick={() => { rename(); onClose(); }}>Done</button></>}>
      <div className="settings-split">
        <nav className="settings-nav">
          {tabs.map((t) => (
            <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => { rename(); setTab(t.id); }}>
              {t.label}{t.badge ? <span className="badge">{t.badge}</span> : null}
            </button>
          ))}
        </nav>
        <div className="settings-pane">
          {tab === "general" && (
            <div className="form">
              <label className="stack">Name
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} onBlur={rename}
                  onKeyDown={(e) => e.key === "Enter" && rename()} />
              </label>
              <div className="stack">Show records as
                <div className="type-tiles">
                  {VIEW_TYPES.map((t) => (
                    <button key={t.type} className={`type-tile ${view.type === t.type ? "active" : ""}`} onClick={() => changeType(t.type)} title={t.desc}>
                      <Icon name={t.icon} size={18} /><span>{t.label}</span>
                    </button>
                  ))}
                </div>
                <span className="hint">Switching keeps the view's filters, sorting and hidden fields.</span>
              </div>
              <div className="settings-actions">
                <button className="btn sm" onClick={onDuplicate}><Icon name="copy" size={14} /> Duplicate view</button>
                <button className="btn sm" disabled={idx <= 0} onClick={() => onMove(-1)}><Icon name="left" size={14} /> Move left</button>
                <button className="btn sm" disabled={idx >= views.length - 1} onClick={() => onMove(1)}>Move right <Icon name="right" size={14} /></button>
                {onCalendarLink && <button className="btn sm" onClick={onCalendarLink}><Icon name="calendar" size={14} /> Calendar subscription link…</button>}
                <button className="btn sm" title={view.id} onClick={() => navigator.clipboard?.writeText(view.id)}><Icon name="copy" size={14} /> Copy view ID</button>
              </div>
              {onDelete && (
                <div className="settings-danger">
                  {confirmDelete
                    ? <><span>Delete “{view.name}”? Records aren't affected.</span>
                      <button className="btn sm danger" onClick={onDelete}>Delete view</button>
                      <button className="btn sm" onClick={() => setConfirmDelete(false)}>Cancel</button></>
                    : <button className="btn sm danger-ghost" onClick={() => setConfirmDelete(true)}><Icon name="trash" size={14} /> Delete view</button>}
                </div>
              )}
              {err && <div className="error">{err}</div>}
            </div>
          )}
          {tab === "layout" && <Layout view={view} fields={fields} config={config} setConfig={setConfig} />}
          {tab === "fields" && <FieldsPane fields={fields} config={config} setConfig={setConfig} />}
          {tab === "filter" && <FilterEditor fields={fields} filter={filter} onChange={(g) => setConfig({ filter: g })} />}
          {tab === "sort" && <SortEditor fields={fields} sorts={sorts} onChange={(x) => setConfig({ sorts: x })} />}
          {tab === "group" && canGroup && <GroupEditorList fields={fields} groups={groups} onChange={(x) => setConfig({ groups: x })} />}
        </div>
      </div>
    </Modal>
  );
}

/** What this kind of view needs: date fields, the board's column field, the cover, colors, rows per page… */
function Layout({ view, fields, config, setConfig }: { view: View; fields: Field[]; config: ViewConfig; setConfig: (p: Partial<ViewConfig>) => void }) {
  const dates = fields.filter(isDateField);
  const selects = fields.filter((f) => f.type === "single_select");
  const hidden = hiddenIds(fields, config);
  const t = view.type;
  return (
    <div className="form">
      {(t === "calendar" || t === "timeline") && (<>
        <label className="stack">Start date<FieldSelect fields={dates} value={config.dateField} onChange={(id) => setConfig({ dateField: id ?? undefined })} placeholder="Choose a date field…" /></label>
        <label className="stack">End date<FieldSelect fields={dates} value={config.endField} onChange={(id) => setConfig({ endField: id })} placeholder="No end date" allowNone /></label>
        {!dates.length && <p className="hint">Add a Date field to the table to place records on the {t}.</p>}
      </>)}
      {t === "calendar" && (
        <label className="stack">Opens on
          <select className="input sm" value={config.calMode ?? "month"} onChange={(e) => setConfig({ calMode: e.target.value as ViewConfig["calMode"] })}>
            <option value="month">Month</option><option value="week">Week</option><option value="day">Day</option>
          </select>
        </label>
      )}
      {t === "kanban" && (<>
        <label className="stack">Columns from<FieldSelect fields={selects} value={config.groupBy} onChange={(id) => setConfig({ groupBy: id ?? undefined })} placeholder="Choose a single select field…" /></label>
        <BoardOptions config={config} groupField={fields.find((f) => f.id === config.groupBy)} setConfig={setConfig} />
      </>)}
      {t === "gallery" && (
        <label className="stack">Cover image
          <FieldSelect fields={fields.filter((f) => f.type === "attachment" || f.type === "url" || f.type === "text" || f.type === "lookup")} value={config.coverField}
            onChange={(id) => setConfig({ coverField: id })} placeholder="No cover" allowNone />
        </label>
      )}
      {t === "list" && <ListOptions layout={listLayout(fields.filter((f) => !hidden.has(f.id)), config)} config={config} setConfig={setConfig} />}
      {(t === "calendar" || t === "timeline" || t === "kanban" || t === "gallery" || t === "list") && (
        <label className="stack">Color records by<FieldSelect fields={selects} value={config.colorField} onChange={(id) => setConfig({ colorField: id })} placeholder="No color" allowNone /></label>
      )}
      {PAGED_VIEWS.has(t) && (
        <label className="stack">Rows per page
          <select className="input sm" value={config.pageSize ?? 0} onChange={(e) => setConfig({ pageSize: Number(e.target.value) || undefined })}>
            <option value={0}>All rows</option>
            {PAGE_SIZES.map((n) => <option key={n} value={n}>{n} per page</option>)}
          </select>
        </label>
      )}
      {t === "grid" && (
        <label className="stack">Frozen columns
          <select className="input sm" value={config.frozen ?? 0} onChange={(e) => setConfig({ frozen: Number(e.target.value) || undefined })}>
            <option value={0}>None</option>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>The first {n === 1 ? "column" : `${n} columns`}</option>)}
          </select>
          <span className="hint">They stay in place while the rest scrolls sideways. Column totals are chosen in the table's footer.</span>
        </label>
      )}
      {t === "chart" && <p className="hint">The chart's settings are above the chart itself.</p>}
    </div>
  );
}

/** Show / hide, rename for this view, and order the fields (drag). */
function FieldsPane({ fields, config, setConfig }: { fields: Field[]; config: ViewConfig; setConfig: (p: Partial<ViewConfig>) => void }) {
  const ordered = orderFields(fields, config.fieldOrder);
  const hidden = hiddenIds(fields, config);
  const [drag, setDrag] = useState<string | null>(null);
  const move = (from: string, to: string) => {
    if (from === to) return;
    const ids = ordered.map((f) => f.id).filter((id) => id !== from);
    ids.splice(ids.indexOf(to) + (ordered.findIndex((f) => f.id === from) < ordered.findIndex((f) => f.id === to) ? 1 : 0), 0, from);
    setConfig({ fieldOrder: ids });
  };
  return (
    <div className="form">
      <p className="muted small">Drag to change the order in this view; untick to hide; type a name to call a field something else here only.</p>
      <div className="settings-list">
        {ordered.map((f) => (
          <div key={f.id} className={`settings-row ${drag === f.id ? "dragging" : ""}`} draggable onDragStart={() => setDrag(f.id)}
            onDragOver={(e) => { if (drag) { e.preventDefault(); move(drag, f.id); } }} onDragEnd={() => setDrag(null)}>
            <span className="drag-handle" title="Drag to reorder">⋮⋮</span>
            <input type="checkbox" checked={!hidden.has(f.id)} disabled={f.is_primary} aria-label={`Show ${f.realName ?? f.name}`}
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
      {config.fieldOrder?.length ? <button className="btn sm" onClick={() => setConfig({ fieldOrder: undefined })}>Use the table's field order</button> : null}
    </div>
  );
}

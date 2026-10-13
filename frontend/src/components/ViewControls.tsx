import type { Field, Filter, FilterGroup, FilterOp, SortSpec, ViewConfig } from "../types";
import type { ListProp } from "./ListView";
import { canGroupBy, valueType } from "../util";
import { Icon } from "./Icon";

// ------------------------------------------------------------------ filter

const ALL_OPS: { op: FilterOp; label: string; needsValue: boolean }[] = [
  { op: "is", label: "is", needsValue: true },
  { op: "is_not", label: "is not", needsValue: true },
  { op: "contains", label: "contains", needsValue: true },
  { op: "not_contains", label: "does not contain", needsValue: true },
  { op: "gt", label: "is greater than", needsValue: true },
  { op: "gte", label: "is at least", needsValue: true },
  { op: "lt", label: "is less than", needsValue: true },
  { op: "lte", label: "is at most", needsValue: true },
  { op: "empty", label: "is empty", needsValue: false },
  { op: "not_empty", label: "is not empty", needsValue: false },
];
const DATE_LABELS: Partial<Record<FilterOp, string>> = { gt: "is after", gte: "is on or after", lt: "is before", lte: "is on or before" };

function opsFor(f: Field | undefined) {
  const vt = valueType(f);
  if (f?.type === "checkbox" || vt === "boolean") return ALL_OPS.filter((o) => o.op === "is");
  if (vt === "number" || vt === "date") {
    return ALL_OPS.filter((o) => !["contains", "not_contains"].includes(o.op))
      .map((o) => (vt === "date" && DATE_LABELS[o.op] ? { ...o, label: DATE_LABELS[o.op]! } : o));
  }
  return ALL_OPS.filter((o) => !["gt", "gte", "lt", "lte"].includes(o.op));
}

const isGroup = (n: Filter | FilterGroup): n is FilterGroup => Array.isArray((n as FilterGroup).items);
const MAX_DEPTH = 3;

function ValueInput({ field, value, onChange }: { field?: Field; value?: string; onChange: (v: string) => void }) {
  const vt = valueType(field);
  if (field?.type === "checkbox" || vt === "boolean") {
    return (
      <select className="input sm" value={value || "true"} onChange={(e) => onChange(e.target.value)}>
        <option value="true">checked</option><option value="false">unchecked</option>
      </select>
    );
  }
  const choices = field?.options.choices ?? (field?.id === "__source" ? field.options.choices : undefined);
  if (choices && (field?.type === "single_select" || field?.type === "multi_select" || field?.id === "__source")) {
    return (
      <select className="input sm" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose…</option>
        {choices.map((c) => <option key={c.name} value={c.name.toLowerCase()}>{c.name}</option>)}
      </select>
    );
  }
  if (vt === "date") return <DateValue value={value ?? ""} onChange={onChange} />;
  return <input className="input sm" type={vt === "number" ? "number" : "text"} value={value ?? ""} placeholder="Value" onChange={(e) => onChange(e.target.value)} />;
}

// a date filter's value: a period worked out when the view is shown (as in Teable), a number of days, a date or a range
const DATE_MODES = ["today", "tomorrow", "yesterday", "current week", "last week", "next week", "current month", "last month", "next month",
  "current year", "days ago", "days from now", "exact date", "custom range"] as const;
type DateMode = typeof DATE_MODES[number];

function dateMode(v: string): { mode: DateMode; n?: string; a?: string; b?: string } {
  const s = v.trim().toLowerCase().replace(/\(\)/g, "");
  let m: RegExpMatchArray | null;
  if ((DATE_MODES as readonly string[]).includes(s)) return { mode: s as DateMode };
  if (s === "this week" || s === "this month" || s === "this year") return { mode: s.replace("this", "current") as DateMode };
  if ((m = s.match(/^(\d+) days? ago$/))) return { mode: "days ago", n: m[1] };
  if ((m = s.match(/^(?:(\d+) days? from now|in (\d+) days?)$/))) return { mode: "days from now", n: m[1] ?? m[2] };
  if ((m = s.match(/^(\d{4}-\d{2}-\d{2})\s*(?:\.\.|to)\s*(\d{4}-\d{2}-\d{2})$/))) return { mode: "custom range", a: m[1], b: m[2] };
  return { mode: "exact date", a: /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : "" };
}

export function DateValue({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const cur = value ? dateMode(value) : { mode: "exact date" as DateMode, a: "" };
  const today = new Date().toISOString().slice(0, 10);
  const pick = (mode: DateMode) => onChange(mode === "days ago" ? "1 days ago" : mode === "days from now" ? "1 days from now"
    : mode === "exact date" ? today : mode === "custom range" ? `${today}..${today}` : mode);
  return (
    <span className="date-filter">
      <select className="input sm" value={cur.mode} onChange={(e) => pick(e.target.value as DateMode)} aria-label="Date">
        {DATE_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
      </select>
      {(cur.mode === "days ago" || cur.mode === "days from now") && (
        <input className="input sm date-filter-n" type="number" min={0} value={cur.n ?? "1"} aria-label="Days"
          onChange={(e) => onChange(`${Math.max(0, Number(e.target.value) || 0)} days ${cur.mode === "days ago" ? "ago" : "from now"}`)} />
      )}
      {cur.mode === "exact date" && <input className="input sm" type="date" value={cur.a ?? ""} onChange={(e) => onChange(e.target.value)} aria-label="Date" />}
      {cur.mode === "custom range" && (<>
        <input className="input sm" type="date" value={cur.a ?? ""} aria-label="From" onChange={(e) => onChange(`${e.target.value}..${cur.b ?? e.target.value}`)} />
        <span className="muted">to</span>
        <input className="input sm" type="date" value={cur.b ?? ""} aria-label="To" onChange={(e) => onChange(`${cur.a ?? e.target.value}..${e.target.value}`)} />
      </>)}
    </span>
  );
}

function GroupEditor({ group, fields, depth, onChange, onRemove }: {
  group: FilterGroup; fields: Field[]; depth: number; onChange: (g: FilterGroup) => void; onRemove?: () => void;
}) {
  const set = (i: number, item: Filter | FilterGroup) => onChange({ ...group, items: group.items.map((x, j) => (j === i ? item : x)) });
  const remove = (i: number) => onChange({ ...group, items: group.items.filter((_, j) => j !== i) });
  const newCond = (): Filter => { const f = fields[0]; return { field: f?.id ?? "", op: opsFor(f)[0].op, value: "" }; };
  return (
    <div className={`filter-group ${depth ? "nested" : ""}`}>
      {group.items.map((item, i) => (
        <div key={i} className="filter-line">
          <span className="filter-conj">
            {i === 0 ? <span className="muted small">Where</span> : i === 1 ? (
              <select className="input sm" value={group.conjunction} onChange={(e) => onChange({ ...group, conjunction: e.target.value as "and" | "or" })}>
                <option value="and">and</option><option value="or">or</option>
              </select>
            ) : <span className="muted small">{group.conjunction}</span>}
          </span>
          {isGroup(item) ? (
            <GroupEditor group={item} fields={fields} depth={depth + 1} onChange={(g) => set(i, g)} onRemove={() => remove(i)} />
          ) : (() => {
            const f = fields.find((x) => x.id === item.field);
            const ops = opsFor(f);
            const needsValue = ops.find((o) => o.op === item.op)?.needsValue ?? true;
            return (
              <div className="filter-cond">
                <select className="input sm" value={item.field} onChange={(e) => {
                  const nf = fields.find((x) => x.id === e.target.value);
                  const nops = opsFor(nf);
                  set(i, { field: e.target.value, op: nops.some((o) => o.op === item.op) ? item.op : nops[0].op, value: "" });
                }}>
                  {fields.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                </select>
                <select className="input sm" value={item.op} onChange={(e) => set(i, { ...item, op: e.target.value as FilterOp })}>
                  {ops.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}
                </select>
                {needsValue ? <ValueInput field={f} value={item.value} onChange={(v) => set(i, { ...item, value: v })} /> : <span />}
                <button className="icon-btn" aria-label="Remove condition" onClick={() => remove(i)}><Icon name="x" size={14} /></button>
              </div>
            );
          })()}
        </div>
      ))}
      {!group.items.length && <div className="muted small pad-y">{depth ? "Empty group" : "No filters applied to this view."}</div>}
      <div className="filter-actions">
        <button className="ghost-btn" onClick={() => onChange({ ...group, items: [...group.items, newCond()] })}><Icon name="plus" size={14} /> Add condition</button>
        {depth < MAX_DEPTH - 1 && (
          <button className="ghost-btn" onClick={() => onChange({ ...group, items: [...group.items, { conjunction: "and", items: [newCond()] }] })}>
            <Icon name="plus" size={14} /> Add condition group
          </button>
        )}
        {onRemove && <button className="ghost-btn danger-text" onClick={onRemove}><Icon name="trash" size={13} /> Remove group</button>}
      </div>
    </div>
  );
}

export function FilterEditor({ fields, filter, onChange }: { fields: Field[]; filter: FilterGroup; onChange: (g: FilterGroup) => void }) {
  return (
    <div className="pop-body filter-editor">
      <GroupEditor group={filter} fields={fields} depth={0} onChange={onChange} />
    </div>
  );
}

// ------------------------------------------------------------------ sort & group lists

function SpecList({ fields, specs, onChange, max, addLabel, empty, ascLabel = "Ascending", descLabel = "Descending", groupToggles }: {
  fields: Field[]; specs: SortSpec[]; onChange: (s: SortSpec[]) => void; max: number; addLabel: string; empty: string;
  ascLabel?: string; descLabel?: string; groupToggles?: boolean;
}) {
  const used = new Set(specs.map((s) => s.field));
  const free = fields.filter((f) => !used.has(f.id));
  const set = (i: number, p: Partial<SortSpec>) => onChange(specs.map((s, j) => (j === i ? { ...s, ...p } : s)));
  const move = (i: number, d: number) => { const n = [...specs]; [n[i], n[i + d]] = [n[i + d], n[i]]; onChange(n); };
  return (
    <div className="pop-body spec-list">
      {specs.map((s, i) => (
        <div key={i} className="spec-row">
          <span className="muted small spec-label">{i === 0 ? "By" : "Then by"}</span>
          <select className="input sm" value={s.field} onChange={(e) => set(i, { field: e.target.value })}>
            {fields.filter((f) => f.id === s.field || !used.has(f.id)).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <select className="input sm" value={s.dir} onChange={(e) => set(i, { dir: e.target.value as "asc" | "desc" })}>
            <option value="asc">{ascLabel}</option><option value="desc">{descLabel}</option>
          </select>
          <span className="spec-btns">
            <button className="icon-btn" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="up" size={13} /></button>
            <button className="icon-btn" aria-label="Remove" onClick={() => onChange(specs.filter((_, j) => j !== i))}><Icon name="x" size={14} /></button>
          </span>
          {groupToggles && valueType(fields.find((f) => f.id === s.field)) === "date" && (
            <select className="input sm" value={s.dateBy ?? "day"} aria-label="Group dates by" onChange={(e) => set(i, { dateBy: e.target.value === "day" ? undefined : e.target.value as SortSpec["dateBy"] })}>
              <option value="day">by day</option><option value="week">by week</option><option value="month">by month</option>
              <option value="year">by year</option><option value="relative">relative to today</option>
            </select>
          )}
          {groupToggles && (
            <span className="spec-toggles">
              <label className="chip-check" title="Show this level's header row (e.g. “Event”, “Task”)">
                <input type="checkbox" checked={!s.hideTitle} onChange={(e) => set(i, { hideTitle: !e.target.checked })} /> Title
              </label>
              <label className={`chip-check ${s.hideTitle ? "disabled" : ""}`} title="Show the arrow that collapses this group">
                <input type="checkbox" checked={!s.noArrow} disabled={s.hideTitle} onChange={(e) => set(i, { noArrow: !e.target.checked })} /> Collapse arrow
              </label>
            </span>
          )}
        </div>
      ))}
      {!specs.length && <div className="muted small pad-y">{empty}</div>}
      {specs.length < max && free.length > 0 && (
        <button className="ghost-btn" onClick={() => onChange([...specs, { field: free[0].id, dir: "asc" }])}><Icon name="plus" size={14} /> {addLabel}</button>
      )}
    </div>
  );
}

export function SortEditor({ fields, sorts, onChange }: { fields: Field[]; sorts: SortSpec[]; onChange: (s: SortSpec[]) => void }) {
  return <SpecList fields={fields} specs={sorts} onChange={onChange} max={5} addLabel={sorts.length ? "Add another sort" : "Add sort"}
    empty="No sorting: records keep the order they were added in." ascLabel="A → Z / 1 → 9" descLabel="Z → A / 9 → 1" />;
}

export function GroupEditorList({ fields, groups, onChange }: { fields: Field[]; groups: SortSpec[]; onChange: (s: SortSpec[]) => void }) {
  return <SpecList fields={fields.filter(canGroupBy)} specs={groups} onChange={onChange} max={3} groupToggles
    addLabel={groups.length ? "Add subgroup" : "Add group"} empty="Records aren't grouped." />;
}

// ------------------------------------------------------------------ board options

export function BoardOptions({ config, groupField, setConfig }: { config: ViewConfig; groupField?: Field; setConfig: (p: Partial<ViewConfig>) => void }) {
  const name = groupField?.name ?? "group";
  return (
    <div className="pop-body board-options">
      <label className="inline-check">
        <input type="checkbox" checked={!config.hideUnassigned} onChange={(e) => setConfig({ hideUnassigned: !e.target.checked })} />
        <span>Show cards with no {name}<small className="muted block">The “No {name}” column</small></span>
      </label>
      <label className="inline-check">
        <input type="checkbox" checked={!!config.hideEmptyColumns} onChange={(e) => setConfig({ hideEmptyColumns: e.target.checked })} />
        <span>Hide empty columns<small className="muted block">Columns with no cards in this view</small></span>
      </label>
      <label className="inline-check">
        <input type="checkbox" checked={!!config.hideBlankCards} onChange={(e) => setConfig({ hideBlankCards: e.target.checked })} />
        <span>Hide empty cards<small className="muted block">Cards with no title and no other values</small></span>
      </label>
    </div>
  );
}




// ------------------------------------------------------------------ list properties

export function ListOptions({ layout, config, setConfig, extra = [] }: {
  layout: ListProp[]; config: ViewConfig; setConfig: (p: Partial<ViewConfig>) => void;
  extra?: Field[];   // fields a list leaves out until they're added here (long text)
}) {
  const save = (items: ListProp[]) =>
    setConfig({ listProps: items.map((x) => ({ field: x.field.id, align: x.align, label: x.label })) });
  const move = (i: number, d: number) => { const n = [...layout]; [n[i], n[i + d]] = [n[i + d], n[i]]; save(n); };
  const allNames = layout.length > 0 && layout.every((x) => x.label);
  return (
    <div className="pop-body list-options">
      <label className="inline-check">
        <input type="checkbox" checked={allNames} ref={(el) => { if (el) el.indeterminate = !allNames && layout.some((x) => x.label); }}
          onChange={(e) => { setConfig({ listLabels: e.target.checked }); save(layout.map((x) => ({ ...x, label: e.target.checked }))); }} />
        Show property names
      </label>
      <p className="muted small">
        <b>Start</b> puts a property right after the color dot, before the title. Order is left to right within each place;
        the eye shows or hides a property's name. Hide a property completely under Fields.
      </p>
      {layout.map((x, i) => (
        <div key={x.field.id} className="list-opt-row">
          <span className="list-opt-name">{x.field.name}</span>
          <button className={`icon-btn ${x.label ? "on" : "off"}`} aria-pressed={x.label} title={x.label ? "Name shown" : "Name hidden"}
            aria-label={`Show the name of ${x.field.name}`} onClick={() => save(layout.map((y, j) => (j === i ? { ...y, label: !y.label } : y)))}>
            <Icon name="eye" size={14} />
          </button>
          <div className="segmented sm" role="group" aria-label={`${x.field.name} position`}>
            {(["start", "left", "center", "right"] as const).map((a) => (
              <button key={a} className={x.align === a ? "active" : ""} onClick={() => save(layout.map((y, j) => (j === i ? { ...y, align: a } : y)))}>
                {a[0].toUpperCase() + a.slice(1)}
              </button>
            ))}
          </div>
          <span className="spec-btns">
            <button className="icon-btn" aria-label="Move left" disabled={i === 0} onClick={() => move(i, -1)}><Icon name="up" size={13} /></button>
            <button className="icon-btn" aria-label="Move right" disabled={i === layout.length - 1} onClick={() => move(i, 1)}>
              <span style={{ display: "inline-flex", transform: "rotate(180deg)" }}><Icon name="up" size={13} /></span>
            </button>
          </span>
        </div>
      ))}
      {!layout.length && <div className="muted small">No visible properties besides the title.</div>}
      {extra.length > 0 && (
        <div className="list-opt-extra">
          <span className="muted small">Long text (one line, cut off where the row ends):</span>
          {extra.map((f) => (
            <button key={f.id} className="btn sm" onClick={() => save([...layout, { field: f, align: "left", label: config.listLabels !== false }])}>
              <Icon name="plus" size={13} /> {f.name}</button>
          ))}
        </div>
      )}
      {layout.some((x) => x.field.type === "long_text") && (
        <div className="list-opt-extra">
          {layout.filter((x) => x.field.type === "long_text").map((x) => (
            <button key={x.field.id} className="btn sm ghost" onClick={() => save(layout.filter((y) => y.field.id !== x.field.id))}>
              <Icon name="x" size={13} /> Remove {x.field.name}</button>
          ))}
        </div>
      )}
    </div>
  );
}

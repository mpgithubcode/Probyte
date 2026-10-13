import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { api } from "../api";
import type { Choice, Field, FieldOptions, FieldType, FilterCond, TableInfo } from "../types";
import { COMPUTED_TYPES, DATE_FORMATS, FIELD_TYPES, PALETTE, ROLLUP_FUNCS, formatDate, formatNumber, isEditable } from "../util";
import { FormulaEditor } from "./FormulaEditor";
import { FieldIcon, Icon } from "./Icon";
import { Modal, Popover, usePopover } from "./Overlay";

const fieldCache = new Map<string, Promise<Field[]>>();
function useTableFields(tableId?: string) {
  const [fields, setFields] = useState<Field[] | null>(null);
  useEffect(() => {
    setFields(null);
    if (!tableId) return;
    if (!fieldCache.has(tableId)) fieldCache.set(tableId, api.get(`/tables/${tableId}`).then((r) => r.fields as Field[]));
    fieldCache.get(tableId)!.then(setFields).catch(() => { fieldCache.delete(tableId); setFields([]); });
  }, [tableId]);
  return fields;
}

const OPS = [
  { op: "is", label: "is" }, { op: "is_not", label: "is not" }, { op: "contains", label: "contains" },
  { op: "not_contains", label: "does not contain" }, { op: "gt", label: ">" }, { op: "gte", label: "≥" },
  { op: "lt", label: "<" }, { op: "lte", label: "≤" }, { op: "empty", label: "is empty" }, { op: "not_empty", label: "is not empty" },
];

function Select({ value, onChange, children, placeholder }: {
  value?: string | null; onChange: (v: string) => void; children: ReactNode; placeholder: string;
}) {
  return (
    <select className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
      <option value="" disabled>{placeholder}</option>
      {children}
    </select>
  );
}

function TypePicker({ value, onChange, disabled }: { value: FieldType; onChange: (t: FieldType) => void; disabled?: boolean }) {
  const pop = usePopover();
  const cur = FIELD_TYPES.find((t) => t.type === value)!;
  return (
    <>
      <button type="button" className="input type-button" disabled={disabled} onClick={pop.open}>
        <FieldIcon type={value} /> {cur.label} <span className="spacer" /> ▾
      </button>
      {pop.anchor && (
        <Popover anchor={pop.anchor} onClose={pop.close} width={420} className="type-popover">
          {(["Basic", "Advanced", "System"] as const).map((g) => (
            <div key={g}>
              <div className="menu-heading">{g} fields</div>
              <div className="type-grid">
                {FIELD_TYPES.filter((t) => t.group === g).map((t) => (
                  <button key={t.type} className={`menu-item type-item ${t.type === value ? "active" : ""}`}
                    onClick={() => { onChange(t.type); pop.close(); }} title={t.desc}>
                    <FieldIcon type={t.type} />
                    <span><b>{t.label}</b><span className="muted small">{t.desc}</span></span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </Popover>
      )}
    </>
  );
}

/** Which settings of the field editor a type has. */
const RULE_TYPES = new Set<FieldType>(["text", "long_text", "number", "rating", "email", "url", "phone", "date", "single_select", "multi_select",
  "checkbox", "user", "link", "attachment", "recurrence"]);
const UNIQUE_TYPES = new Set<FieldType>(["text", "number", "email", "url", "phone", "date"]);
const DEFAULT_TYPES = new Set<FieldType>(["text", "long_text", "number", "rating", "email", "url", "phone", "date", "single_select", "multi_select",
  "checkbox", "user"]);

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; hint?: ReactNode }) {
  return (
    <label className="toggle-row">
      <span className="stack-tight"><span>{label}</span>{hint && <span className="hint">{hint}</span>}</span>
      <span className="switch"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><span /></span>
    </label>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="field-section"><div className="field-section-title">{title}</div>{children}</div>;
}

function ColorDot({ color, onChange }: { color: string; onChange: (c: string) => void }) {
  const pop = usePopover();
  return (
    <>
      <button type="button" className="color-dot" style={{ background: color }} onClick={pop.open} aria-label="Option color" />
      {pop.anchor && (
        <Popover anchor={pop.anchor} onClose={pop.close} width={196}>
          <div className="swatch-row">
            {PALETTE.map((c) => <button key={c} type="button" className={`swatch-btn ${c === color ? "on" : ""}`} style={{ background: c }}
              onClick={() => { onChange(c); pop.close(); }} aria-label={c} />)}
            <input type="color" className="swatch-custom" value={color} title="Custom color" onChange={(e) => onChange(e.target.value)} />
          </div>
        </Popover>
      )}
    </>
  );
}

/** Select options: drag to reorder, a color each, Enter adds the next one. */
function ChoiceList({ choices, onChange }: { choices: Choice[]; onChange: (c: Choice[]) => void }) {
  const [drag, setDrag] = useState<number | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  useEffect(() => { if (focus !== null) { refs.current[focus]?.focus(); setFocus(null); } }, [focus, choices.length]);
  const put = (i: number, p: Partial<Choice>) => onChange(choices.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const add = (at = choices.length) => {
    const next = [...choices];
    next.splice(at, 0, { name: "", color: PALETTE[choices.length % PALETTE.length] });
    onChange(next);
    setFocus(at);
  };
  const move = (from: number, to: number) => {
    if (from === to) return;
    const next = [...choices];
    const [c] = next.splice(from, 1);
    next.splice(to, 0, c);
    onChange(next);
  };
  return (
    <div className="choice-list">
      {choices.map((c, i) => (
        <div key={i} className={`choice-row ${drag === i ? "dragging" : ""}`}
          onDragOver={(e) => { if (drag !== null) { e.preventDefault(); if (drag !== i) { move(drag, i); setDrag(i); } } }}
          onDrop={(e) => { e.preventDefault(); setDrag(null); }}>
          <span className="drag-handle" draggable title="Drag to reorder"
            onDragStart={(e) => { setDrag(i); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(i)); }}
            onDragEnd={() => setDrag(null)}>⋮⋮</span>
          <ColorDot color={c.color} onChange={(color) => put(i, { color })} />
          <input className="input" ref={(el) => { refs.current[i] = el; }} value={c.name} placeholder="Option name"
            onChange={(e) => put(i, { name: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); add(i + 1); }
              if (e.key === "Backspace" && !c.name && choices.length > 1) { e.preventDefault(); onChange(choices.filter((_, j) => j !== i)); setFocus(Math.max(0, i - 1)); }
            }} />
          <button type="button" className="icon-btn" onClick={() => onChange(choices.filter((_, j) => j !== i))} aria-label="Remove option"><Icon name="x" size={14} /></button>
        </div>
      ))}
      <button type="button" className="ghost-btn" onClick={() => add()}><Icon name="plus" size={14} /> Add option</button>
    </div>
  );
}

/** The value a new record starts with. */
function DefaultInput({ type, o, value, onChange }: { type: FieldType; o: FieldOptions; value: unknown; onChange: (v: unknown) => void }) {
  const str = value === null || value === undefined ? "" : String(value);
  switch (type) {
    case "checkbox":
      return <Toggle checked={value === true} onChange={(v) => onChange(v ? true : null)} label="Checked for new records" />;
    case "long_text":
      return <textarea className="input" rows={3} value={str} placeholder="No default" onChange={(e) => onChange(e.target.value || null)} />;
    case "number":
      return <input className="input" type="number" step="any" value={str} placeholder="No default"
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
    case "rating":
      return (
        <select className="input" value={str} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}>
          <option value="">No default</option>
          {Array.from({ length: o.max ?? 5 }, (_, i) => <option key={i} value={i + 1}>{"★".repeat(i + 1)}</option>)}
        </select>
      );
    case "single_select":
      return (
        <select className="input" value={str} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">No default</option>
          {(o.choices ?? []).filter((c) => c.name.trim()).map((c) => <option key={c.name} value={c.name.trim()}>{c.name.trim()}</option>)}
        </select>
      );
    case "multi_select": {
      const cur = Array.isArray(value) ? (value as string[]) : [];
      const names = (o.choices ?? []).map((c) => c.name.trim()).filter(Boolean);
      if (!names.length) return <span className="hint">Add options first.</span>;
      return (
        <div className="default-chips">
          {(o.choices ?? []).filter((c) => c.name.trim()).map((c) => {
            const on = cur.includes(c.name.trim());
            return (
              <button type="button" key={c.name} className={`chip-toggle ${on ? "on" : ""}`} style={{ "--chip": c.color } as CSSProperties}
                onClick={() => { const next = on ? cur.filter((x) => x !== c.name.trim()) : [...cur, c.name.trim()]; onChange(next.length ? next : null); }}>
                {c.name.trim()}
              </button>
            );
          })}
        </div>
      );
    }
    case "date": {
      const mode = value === "today" || value === "now" ? value : value ? "date" : "";
      return (
        <div className="row">
          <select className="input" value={mode} onChange={(e) => {
            const m = e.target.value;
            onChange(m === "" ? null : m === "date" ? new Date().toISOString().slice(0, 10) : m);
          }}>
            <option value="">No default</option>
            <option value="today">Today (the day it's added)</option>
            {o.includeTime && <option value="now">Now (the moment it's added)</option>}
            <option value="date">A specific date</option>
          </select>
          {mode === "date" && <input className="input" type="date" value={str.slice(0, 10)} onChange={(e) => onChange(e.target.value || null)} />}
        </div>
      );
    }
    case "user":
      return (
        <select className="input" value={Array.isArray(value) && value.includes("me") ? "me" : ""} onChange={(e) => onChange(e.target.value ? ["me"] : null)}>
          <option value="">No default</option>
          <option value="me">Whoever adds the record</option>
        </select>
      );
    default:
      return <input className="input" type={type === "email" ? "email" : type === "url" ? "url" : type === "phone" ? "tel" : "text"} value={str} placeholder="No default"
        onChange={(e) => onChange(e.target.value || null)} />;
  }
}

export function FieldModal({ tableId, tableName, field, fields, tables, onClose, onSaved }: {
  tableId: string; tableName: string; field: Field | null; fields: Field[]; tables: TableInfo[];
  onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState(field?.name ?? "");
  const [type, setType] = useState<FieldType>(field?.type ?? "text");
  const [o, setO] = useState<FieldOptions>(() => ({
    ...(field?.options ?? {}),
    ...(field?.options.choices ? { choices: field.options.choices.map((c) => ({ ...c, from: c.name })) } : {}),
    ...(field?.type === "formula" ? { expression: field.options.displayExpression ?? field.options.expression ?? "" } : {}),
  }));
  const [description, setDescription] = useState(field?.description ?? "");
  const [showDesc, setShowDesc] = useState(!!field?.description);
  const [advanced, setAdvanced] = useState(false);
  const [required, setRequired] = useState(!!field?.required);
  const [unique, setUnique] = useState(!!field?.unique_values);
  const [dv, setDv] = useState<unknown>(field?.default_value ?? null);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const system = !!field?.system_key;
  const set = (p: Partial<FieldOptions>) => setO((cur) => ({ ...cur, ...p }));

  const linkFields = fields.filter((f) => f.type === "link" && f.id !== field?.id);
  const linkField = linkFields.find((f) => f.id === o.linkFieldId);
  const foreignId = type === "lookup" || type === "rollup" ? linkField?.options.foreignTableId : o.foreignTableId;
  const foreignFields = useTableFields(foreignId);
  const otherTables = tables;
  const typeChanged = field && field.type !== type;
  // Values are converted on type change; say what happens in the cases that aren't a plain conversion.
  const conversionNote = !typeChanged ? null
    : COMPUTED_TYPES.has(type) ? "This field will be calculated automatically, so its current values will be replaced."
    : type === "attachment" ? "Existing values can't be turned into files, so this field will start empty."
    : type === "link" ? "Existing values will be matched to records in the linked table by their name."
    : type === "user" ? "Existing values will be matched to users by username."
    : COMPUTED_TYPES.has(field!.type) ? "The current calculated values will be kept as regular values you can edit."
    : null;

  const pickType = (t: FieldType) => {
    setType(t);
    setError("");
    if (t !== type) setDv(null);
    if (!name.trim() || FIELD_TYPES.some((x) => x.label === name)) setName(FIELD_TYPES.find((x) => x.type === t)!.label);
    if (t === "rollup" || t === "conditional_rollup") set({ func: o.func ?? "sum" });
    if (t === "button") set({ label: o.label ?? "Click", action: o.action ?? "update", updates: o.updates ?? [] });
  };

  const save = async () => {
    if (!name.trim()) return setError("Name is required");
    if (type === "date" && typeof dv === "string" && dv !== "today" && dv !== "now" && !/^\d{4}-\d{2}-\d{2}/.test(dv)) return setError("Pick the default date");
    const options: FieldOptions = { ...o };
    let def = dv;
    delete options._inner; delete options._error; delete options._filesTable; delete options.displayExpression; delete options.resultType;
    if (type === "single_select" || type === "multi_select") {
      const kept = (o.choices ?? []).filter((c) => c.name.trim());
      // renamed options: records holding the old name get the new one
      const renames = Object.fromEntries(kept.filter((c) => c.from && c.from !== c.name.trim()).map((c) => [c.from, c.name.trim()]));
      options.choices = kept.map(({ from: _, ...c }) => ({ ...c, name: c.name.trim() }));
      if (Object.keys(renames).length && field && field.type === type) options.renames = renames;
      const rn = (v: unknown) => (typeof v === "string" && renames[v]) || v;
      def = Array.isArray(dv) ? dv.map(rn) : rn(dv);
    }
    const body = system ? { description: description.trim() || null } : {
      name: name.trim(), type, options, description: description.trim() || null,
      required: RULE_TYPES.has(type) && required, unique: UNIQUE_TYPES.has(type) && unique,
      default_value: DEFAULT_TYPES.has(type) && def !== "" ? def : null,
    };
    setBusy(true);
    try {
      if (field) await api.patch(`/fields/${field.id}`, body);
      else await api.post(`/tables/${tableId}/fields`, body);
      fieldCache.clear();
      onSaved();
      onClose();
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  const remove = async () => {
    try { await api.del(`/fields/${field!.id}`); fieldCache.clear(); onSaved(); onClose(); } catch (e) { setError((e as Error).message); }
  };

  const choices = o.choices ?? [];
  const setChoices = (c: Choice[]) => set({ choices: c });
  const filters = o.filters ?? [];
  const setFilter = (i: number, p: Partial<FilterCond>) => set({ filters: filters.map((f, j) => (j === i ? { ...f, ...p } : f)) });
  const fieldOpt = (f: Field) => <option key={f.id} value={f.id}>{f.name}</option>;
  const wide = type === "formula";

  return (
    <Modal title={system ? <span className="row"><Icon name="lock" size={15} /> {field!.name}</span> : field ? "Edit field" : "New field"}
      width={wide ? 980 : 480} drawer onClose={onClose}
      footer={<>
        {field && !system && !field.is_primary && (confirmDelete
          ? <><button className="btn danger" onClick={remove}>Delete field + data</button><button className="btn" onClick={() => setConfirmDelete(false)}>Cancel</button></>
          : <button className="btn danger-ghost" onClick={() => setConfirmDelete(true)}><Icon name="trash" size={14} /> Delete</button>)}
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
      </>}>
      {system && (
        <div className="notice lock-notice">
          <Icon name="lock" size={16} />
          <span>{field!.system_key === "record_id"
            ? "Built in: every table has its record IDs. It can be hidden in a view but not changed."
            : "Locked: this field is part of the synced calendar / contact item, so it can't be renamed, changed or deleted. Hide it in a view if you don't need it, or add your own field."}</span>
        </div>
      )}
      <div className="form field-editor">
        <label className="stack">Name
          <input className="input" autoFocus={!wide} value={name} disabled={system} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !wide && save()} />
        </label>
        <div className="row field-links">
          {!showDesc && <button type="button" className="link-btn" onClick={() => setShowDesc(true)}>Add description</button>}
          {field && <button type="button" className="link-btn" onClick={() => setAdvanced(!advanced)}>{advanced ? "Hide" : "Advanced"} properties</button>}
        </div>
        {showDesc && (
          <label className="stack">Description
            <textarea className="input" rows={2} autoFocus={!field?.description} value={description} placeholder="What this field is for (shown on its column)"
              onChange={(e) => setDescription(e.target.value)} />
          </label>
        )}
        {advanced && field && (
          <div className="advanced-props">
            <div className="row"><span className="muted small">Field ID</span><code className="mono">{field.id}</code>
              <button type="button" className="icon-btn" title="Copy" onClick={() => navigator.clipboard?.writeText(field.id)}><Icon name="copy" size={13} /></button></div>
            <span className="hint">Use the ID instead of the name in the API (<code>fieldKeyType=id</code>) so renaming the field doesn't break integrations.</span>
          </div>
        )}
      </div>
      <fieldset className="form plain-fieldset field-editor" disabled={system}>
        <label className="stack">Type
          <TypePicker value={type} onChange={pickType} disabled={system} />
          <span className="hint">{FIELD_TYPES.find((t) => t.type === type)?.desc}</span>
        </label>
        {conversionNote && <div className="notice">{conversionNote}</div>}
        {field?.options._error && !typeChanged && <div className="error">{field.options._error}</div>}

        {/* ---------------- basic */}
        {(type === "single_select" || type === "multi_select") && (
          <Section title="Options">
            <ChoiceList choices={choices} onChange={setChoices} />
            <Toggle checked={o.allowNew !== false} onChange={(v) => set({ allowNew: v ? undefined : false })}
              label="Allow new options when editing" hint="Off: only the options above can be picked; new ones are added here." />
          </Section>
        )}
        {type === "number" && (
          <Section title="Formatting">
            <div className="segmented">
              {(["decimal", "percent", "currency"] as const).map((f) => (
                <button type="button" key={f} className={(o.numberFormat ?? "decimal") === f ? "active" : ""}
                  onClick={() => set({ numberFormat: f === "decimal" ? undefined : f })}>{f[0].toUpperCase() + f.slice(1)}</button>
              ))}
            </div>
            <div className="row-2">
              <label className="stack">Decimal places
                <select className="input" value={o.precision ?? ""} onChange={(e) => set({ precision: e.target.value === "" ? undefined : Number(e.target.value) })}>
                  <option value="">Automatic</option>
                  {[0, 1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n === 0 ? "1" : `1.${"0".repeat(n)}`}</option>)}
                </select>
              </label>
              {o.numberFormat === "currency" && (
                <label className="stack">Symbol
                  <input className="input" value={o.currency ?? "$"} maxLength={4} onChange={(e) => set({ currency: e.target.value })} />
                </label>
              )}
            </div>
            <Toggle checked={o.separators !== false} onChange={(v) => set({ separators: v ? undefined : false })} label="Thousands separators" />
            <span className="hint">Example: {formatNumber(o.numberFormat === "percent" ? 0.4567 : 12345.678, o)}</span>
          </Section>
        )}
        {type === "date" && (
          <Section title="Formatting">
            <label className="stack">Date format
              <select className="input" value={o.dateFormat ?? "local"} onChange={(e) => set({ dateFormat: e.target.value === "local" ? undefined : e.target.value as FieldOptions["dateFormat"] })}>
                {DATE_FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
              </select>
            </label>
            <Toggle checked={!!o.includeTime} onChange={(v) => { set({ includeTime: v }); if (!v && dv === "now") setDv("today"); }} label="Include time" />
            {o.includeTime && (
              <label className="stack">Time format
                <select className="input" value={o.timeFormat ?? ""} onChange={(e) => set({ timeFormat: (e.target.value || undefined) as FieldOptions["timeFormat"] })}>
                  <option value="">As on this device</option><option value="12">12 hour (2:30 PM)</option><option value="24">24 hour (14:30)</option>
                </select>
              </label>
            )}
            <span className="hint">Example: {formatDate(o.includeTime ? "2026-10-11T14:30:00Z" : "2026-10-11", !!o.includeTime, o)}</span>
          </Section>
        )}
        {type === "rating" && (
          <label className="stack">Maximum<input className="input" type="number" min={1} max={10} value={o.max ?? 5} onChange={(e) => set({ max: Number(e.target.value) })} /></label>
        )}
        {type === "user" && (
          <label className="inline-check"><input type="checkbox" checked={!!o.multiple} onChange={(e) => set({ multiple: e.target.checked })} /> Allow several users</label>
        )}
        {type === "attachment" && <p className="hint">Upload files of up to 25 MB each. Images show as thumbnails and can be used as gallery covers.</p>}

        {/* ---------------- link */}
        {type === "link" && (
          <>
            <label className="stack">Link to table
              <Select value={o.foreignTableId} onChange={(v) => set({ foreignTableId: v })} placeholder="Choose a table…">
                {otherTables.map((t) => <option key={t.id} value={t.id}>{t.name}{t.id === tableId ? " (this table)" : ""}</option>)}
              </Select>
            </label>
            <label className="inline-check"><input type="checkbox" checked={o.relationship !== "one"}
              onChange={(e) => set({ relationship: e.target.checked ? "many" : "one" })} /> Allow linking to several records</label>
            {o.foreignTableId && foreignFields && (
              <label className="stack">Show in the cell
                <select className="input" value={o.displayFieldId ?? ""} onChange={(e) => set({ displayFieldId: e.target.value || null })}>
                  <option value="">Each record's title</option>
                  {foreignFields.filter((f) => f.type !== "button" && !f.is_primary).map(fieldOpt)}
                </select>
                <span className="hint">Any of the linked records' fields: a formula, a lookup, a date, their files…</span>
              </label>
            )}
            {o.displayFieldId && ["attachment", "lookup", "conditional_lookup"].includes(foreignFields?.find((f) => f.id === o.displayFieldId)?.type ?? "") && (
              <label className="inline-check"><input type="checkbox" checked={o.displayFiles !== false} onChange={(e) => set({ displayFiles: e.target.checked })} />
                Show files as preview thumbnails (a click opens the file)</label>
            )}
            {o.foreignTableId && tables.find((t) => t.id === o.foreignTableId)?.kind === "remote" && (
              <p className="hint">This is a linked table, so the link is one-way: its records come from its owner and can't get a field pointing back.</p>
            )}
            {!field && o.foreignTableId && o.foreignTableId !== tableId && tables.find((t) => t.id === o.foreignTableId)?.kind !== "remote" && (
              <label className="inline-check"><input type="checkbox" checked={o.symmetric !== false} onChange={(e) => set({ symmetric: e.target.checked })} />
                Also add a “{tableName}” field to {tables.find((t) => t.id === o.foreignTableId)?.name} (two-way link)</label>
            )}
          </>
        )}

        {/* ---------------- lookup / rollup */}
        {(type === "lookup" || type === "rollup") && (
          <>
            <label className="stack">Linked records from
              <Select value={o.linkFieldId} onChange={(v) => set({ linkFieldId: v, lookupFieldId: undefined })} placeholder="Choose a link field…">
                {linkFields.map(fieldOpt)}
              </Select>
              {!linkFields.length && <span className="hint">Add a “Link to record” field first.</span>}
            </label>
            {linkField && (
              <label className="stack">{type === "lookup" ? "Field to show" : "Field to summarise"}
                <Select value={o.lookupFieldId} onChange={(v) => set({ lookupFieldId: v })} placeholder={foreignFields ? "Choose a field…" : "Loading…"}>
                  {(foreignFields ?? []).filter((f) => f.type !== "button").map(fieldOpt)}
                </Select>
              </label>
            )}
            {type === "lookup" && foreignFields?.find((f) => f.id === o.lookupFieldId)?.type === "attachment" && (
              <label className="inline-check"><input type="checkbox" checked={o.displayFiles !== false} onChange={(e) => set({ displayFiles: e.target.checked })} />
                Show files as preview thumbnails (a click opens the file)</label>
            )}
          </>
        )}

        {/* ---------------- conditional lookup / rollup */}
        {(type === "conditional_lookup" || type === "conditional_rollup") && (
          <>
            <label className="stack">From table
              <Select value={o.foreignTableId} onChange={(v) => set({ foreignTableId: v, lookupFieldId: undefined, filters: [], sortField: null })} placeholder="Choose a table…">
                {otherTables.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </Select>
            </label>
            {o.foreignTableId && foreignFields && (
              <>
                <label className="stack">{type === "conditional_lookup" ? "Field to show" : "Field to summarise"}
                  <Select value={o.lookupFieldId} onChange={(v) => set({ lookupFieldId: v })} placeholder="Choose a field…">
                    {foreignFields.filter((f) => f.type !== "button").map(fieldOpt)}
                  </Select>
                </label>
                <div className="stack">Only records where…
                  {filters.map((c, i) => {
                    const needsValue = !["empty", "not_empty"].includes(c.op);
                    return (
                      <div key={i} className="cond-row">
                        <select className="input sm" value={c.field} onChange={(e) => setFilter(i, { field: e.target.value })}>
                          {foreignFields.map(fieldOpt)}
                        </select>
                        <select className="input sm" value={c.op} onChange={(e) => setFilter(i, { op: e.target.value })}>
                          {OPS.map((x) => <option key={x.op} value={x.op}>{x.label}</option>)}
                        </select>
                        {needsValue && (
                          <select className="input sm" value={c.valueField ? "field" : "value"}
                            onChange={(e) => setFilter(i, e.target.value === "field" ? { valueField: fields[0]?.id, value: undefined } : { valueField: undefined, value: "" })}>
                            <option value="value">value</option>
                            <option value="field">field of this record</option>
                          </select>
                        )}
                        {needsValue && (c.valueField
                          ? <select className="input sm" value={c.valueField} onChange={(e) => setFilter(i, { valueField: e.target.value })}>
                              {fields.filter((f) => f.id !== field?.id && f.type !== "button").map(fieldOpt)}
                            </select>
                          : <input className="input sm" value={String(c.value ?? "")} placeholder="Value" onChange={(e) => setFilter(i, { value: e.target.value })} />)}
                        <button className="icon-btn" onClick={() => set({ filters: filters.filter((_, j) => j !== i) })}><Icon name="x" size={14} /></button>
                      </div>
                    );
                  })}
                  <button className="ghost-btn" onClick={() => set({ filters: [...filters, { field: foreignFields[0]?.id, op: "is", value: "" }] })}>
                    <Icon name="plus" size={14} /> Add condition
                  </button>
                  {!filters.length && <span className="hint">With no conditions every record of the table is included.</span>}
                </div>
                <div className="row-3">
                  <label className="stack">Sort by
                    <select className="input" value={o.sortField ?? ""} onChange={(e) => set({ sortField: e.target.value || null })}>
                      <option value="">No sorting</option>
                      {foreignFields.map(fieldOpt)}
                    </select>
                  </label>
                  <label className="stack">Order
                    <select className="input" value={o.sortDir ?? "asc"} disabled={!o.sortField} onChange={(e) => set({ sortDir: e.target.value as "asc" | "desc" })}>
                      <option value="asc">Ascending</option><option value="desc">Descending</option>
                    </select>
                  </label>
                  <label className="stack">Limit
                    <input className="input" type="number" min={1} placeholder="All" value={o.limit ?? ""} onChange={(e) => set({ limit: e.target.value ? Number(e.target.value) : null })} />
                  </label>
                </div>
              </>
            )}
          </>
        )}

        {(type === "rollup" || type === "conditional_rollup") && (
          <label className="stack">Summarise with
            <Select value={o.func} onChange={(v) => set({ func: v })} placeholder="Choose a function…">
              {ROLLUP_FUNCS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </Select>
          </label>
        )}

        {/* ---------------- formula */}
        {type === "formula" && (
          <>
            <FormulaEditor tableId={tableId} fieldId={field?.id} fields={fields} value={o.expression ?? ""} onChange={(v) => set({ expression: v })} />
            <div className="row-2">
              <label className="stack">Decimal places (number results)
                <select className="input" value={o.precision ?? ""} onChange={(e) => set({ precision: e.target.value === "" ? undefined : Number(e.target.value) })}>
                  <option value="">Automatic</option>
                  {[0, 1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
            </div>
          </>
        )}

        {/* ---------------- button */}
        {type === "button" && (
          <>
            <div className="row-2">
              <label className="stack">Button label<input className="input" value={o.label ?? ""} onChange={(e) => set({ label: e.target.value })} /></label>
              <label className="stack">Color<input type="color" className="color-input" value={o.color ?? "#6366f1"} onChange={(e) => set({ color: e.target.value })} /></label>
            </div>
            <div className="segmented">
              <button className={o.action !== "url" ? "active" : ""} onClick={() => set({ action: "update" })}>Update fields</button>
              <button className={o.action === "url" ? "active" : ""} onClick={() => set({ action: "url" })}>Open URL</button>
            </div>
            {o.action === "url" ? (
              <label className="stack">URL
                <input className="input" value={o.url ?? ""} placeholder="https://example.com/search?q={Name}" onChange={(e) => set({ url: e.target.value })} />
                <span className="hint">Use {"{Field name}"} to insert a value from the record.</span>
              </label>
            ) : (
              <div className="stack">When clicked, set
                {(o.updates ?? []).map((u, i) => (
                  <div key={i} className="cond-row two">
                    <select className="input sm" value={u.fieldId} onChange={(e) => set({ updates: o.updates!.map((x, j) => j === i ? { ...x, fieldId: e.target.value } : x) })}>
                      {fields.filter(isEditable).map(fieldOpt)}
                    </select>
                    <input className="input sm" value={String(u.value ?? "")} placeholder='Value (or "now", "today", "true")'
                      onChange={(e) => set({ updates: o.updates!.map((x, j) => j === i ? { ...x, value: e.target.value } : x) })} />
                    <button className="icon-btn" onClick={() => set({ updates: o.updates!.filter((_, j) => j !== i) })}><Icon name="x" size={14} /></button>
                  </div>
                ))}
                <button className="ghost-btn" onClick={() => set({ updates: [...(o.updates ?? []), { fieldId: fields.filter(isEditable)[0]?.id, value: "" }] })}>
                  <Icon name="plus" size={14} /> Add field
                </button>
                <span className="hint">For dates, “now” and “today” are filled in when clicked. Leave the value empty to clear a field.</span>
              </div>
            )}
          </>
        )}

        {/* ---------------- automatic */}
        {type === "autonumber" && <p className="hint">Every record gets the next number automatically; numbers are never reused.</p>}
        {["created_time", "last_modified_time", "created_by", "last_modified_by"].includes(type) && (
          <p className="hint">Filled in automatically and can't be edited.</p>
        )}
        {RULE_TYPES.has(type) && (
          <Section title="Field value validation rules">
            <Toggle checked={required} onChange={setRequired} label="Required"
              hint={type === "checkbox" ? "Records can't be saved unless it's checked." : "Records can't be saved with this field empty."} />
            {UNIQUE_TYPES.has(type) && <Toggle checked={unique} onChange={setUnique} label="Unique" hint="No two records can have the same value." />}
          </Section>
        )}
        {DEFAULT_TYPES.has(type) && (
          <Section title="Default value">
            <DefaultInput type={type} o={o} value={dv} onChange={setDv} />
            <span className="hint">Filled in when a record is added without a value here.</span>
          </Section>
        )}
      </fieldset>
      {error && <div className="error">{error}</div>}
    </Modal>
  );
}

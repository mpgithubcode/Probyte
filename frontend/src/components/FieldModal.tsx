import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { api } from "../api";
import type { ChipStyle, Choice, Field, FieldOptions, FieldType, FieldView, FieldViews, FilterCond, FilterGroup, TableInfo, View } from "../types";
import { COMPUTED_TYPES, DATE_FORMATS, FIELD_TYPES, PALETTE, ROLLUP_FUNCS, formatDate, formatNumber, isEditable, BUILTIN_KEYS, valueType } from "../util";
import { FormulaEditor } from "./FormulaEditor";
import { VIEW_LABEL, displaysFor } from "../display";
import { DateValue, FilterEditor } from "./ViewControls";
import { FieldIcon, Icon } from "./Icon";
import { Modal, Popover, usePopover } from "./Overlay";

const fieldCache = new Map<string, Promise<{ fields: Field[]; views: View[] }>>();
function useTableInfo(tableId?: string) {
  const [info, setInfo] = useState<{ fields: Field[]; views: View[] } | null>(null);
  useEffect(() => {
    setInfo(null);
    if (!tableId) return;
    if (!fieldCache.has(tableId)) fieldCache.set(tableId, api.get<{ fields: Field[]; views: View[] }>(`/tables/${tableId}`)
      .then((r) => ({ fields: r.fields, views: r.views ?? [] })));
    fieldCache.get(tableId)!.then(setInfo).catch(() => { fieldCache.delete(tableId); setInfo({ fields: [], views: [] }); });
  }, [tableId]);
  return info;
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

const STAMPS = [
  { kind: "started" as const, label: "Started date", desc: "Fills itself in when work starts (a formula you choose)" },
  { kind: "completed" as const, label: "Completed date", desc: "Fills itself in when it's done, and clears if undone" },
];

function TypePicker({ value, onChange, disabled, stamp, onStamp }: {
  value: FieldType; onChange: (t: FieldType) => void; disabled?: boolean;
  stamp?: "started" | "completed" | null; onStamp?: (k: "started" | "completed") => void;
}) {
  const pop = usePopover();
  const cur = FIELD_TYPES.find((t) => t.type === value)!;
  const curLabel = value === "date" && stamp ? STAMPS.find((x) => x.kind === stamp)!.label : cur.label;
  return (
    <>
      <button type="button" className="input type-button" disabled={disabled} onClick={pop.open}>
        <FieldIcon type={value} /> {curLabel} <span className="spacer" /> ▾
      </button>
      {pop.anchor && (
        <Popover anchor={pop.anchor} onClose={pop.close} width={420} className="type-popover">
          {(["Basic", "Advanced", "System"] as const).map((g) => (
            <div key={g}>
              <div className="menu-heading">{g} fields</div>
              <div className="type-grid">
                {FIELD_TYPES.filter((t) => t.group === g).map((t) => (
                  <button key={t.type} className={`menu-item type-item ${t.type === value && !(t.type === "date" && stamp) ? "active" : ""}`}
                    onClick={() => { onChange(t.type); pop.close(); }} title={t.desc}>
                    <FieldIcon type={t.type} />
                    <span><b>{t.label}</b><span className="muted small">{t.desc}</span></span>
                  </button>
                ))}
              </div>
              {g === "Advanced" && onStamp && (<>
                <div className="menu-heading">Automatic dates</div>
                <div className="type-grid">
                  {STAMPS.map((x) => (
                    <button key={x.kind} className={`menu-item type-item ${value === "date" && stamp === x.kind ? "active" : ""}`}
                      onClick={() => { onStamp(x.kind); pop.close(); }} title={x.desc}>
                      <FieldIcon type="date" />
                      <span><b>{x.label}</b><span className="muted small">{x.desc}</span></span>
                    </button>
                  ))}
                </div>
              </>)}
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

const CHIP_STYLES: [ChipStyle, string][] = [
  ["chip", "Chips: the title (and the fields below)"],
  ["details", "Two lines: the title, the fields under it"],
  ["card", "Cards: a picture, the title and the fields"],
  ["compact", "Small chips: the title only"],
  ["plain", "Plain text: titles separated by commas"],
];

/** Pick a few fields, in order: each a toggle; the order they're turned on in is the order they show. */
function FieldToggles({ fields, value, max, onChange }: { fields: Field[]; value: string[]; max: number; onChange: (v: string[]) => void }) {
  return (
    <div className="field-toggles">
      {fields.map((f) => {
        const on = value.includes(f.id);
        return (
          <button key={f.id} type="button" className={`field-toggle ${on ? "on" : ""}`} disabled={!on && value.length >= max}
            onClick={() => onChange(on ? value.filter((x) => x !== f.id) : [...value, f.id])}>
            <FieldIcon type={f.type} /> {f.name}{on && value.length > 1 ? <span className="field-toggle-n">{value.indexOf(f.id) + 1}</span> : null}
          </button>
        );
      })}
    </div>
  );
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
    ...(field?.type === "date" && field.options.stampWhen ? { stampWhen: field.options.displayStampWhen ?? field.options.stampWhen } : {}),
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
  const foreignInfo = useTableInfo(foreignId);
  const foreignFields = foreignInfo?.fields ?? null;
  const otherTables = tables;
  const typeChanged = field && field.type !== type;
  // Values are converted on type change; say what happens in the cases that aren't a plain conversion.
  const conversionNote = !typeChanged ? null
    : COMPUTED_TYPES.has(type) ? "This field will be calculated automatically. Its current values are kept aside, and come back if you change the type back."
    : type === "attachment" ? "Existing values can't be turned into files, so this field will start empty. They're kept aside, and come back if you change the type back."
    : type === "link" ? "Existing values will be matched to records in the linked table by their name."
    : type === "user" ? "Existing values will be matched to users by username."
    : COMPUTED_TYPES.has(field!.type) ? "The current calculated values will be kept as regular values you can edit."
    : null;

  // what the change does to this field's values, asked before saving: how many fit the new type, how many are kept aside
  const [preview, setPreview] = useState<{ total: number; converted: number; kept: number; restored: number;
    examples: { from: string; to: unknown }[]; unfit: string[] } | null>(null);
  const previewKey = typeChanged ? JSON.stringify([type, o.foreignTableId, o.includeTime, o.max, o.relationship, o.multiple]) : "";
  useEffect(() => {
    setPreview(null);
    if (!previewKey || !field) return;
    let live = true;
    const t = setTimeout(() => {
      const { _inner: _a, _error: _b, _filesTable: _c, displayExpression: _d, resultType: _e, displayStampWhen: _f, ...options } = o;
      api.post<NonNullable<typeof preview>>(`/fields/${field.id}/conversion`, { type, options })
        .then((p) => { if (live) setPreview(p); }).catch(() => {});
    }, 250);
    return () => { live = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);
  const typeLabel = FIELD_TYPES.find((t) => t.type === type)?.label ?? type;
  const previewNote = !preview || !preview.total ? null : (
    <div className={preview.kept ? "notice warn conversion-preview" : "notice conversion-preview"}>
      {preview.converted === preview.total
        ? `All ${preview.total} value${preview.total === 1 ? "" : "s"} will be converted to ${typeLabel}.`
        : `${preview.converted} of ${preview.total} values will be converted to ${typeLabel}.`}
      {preview.examples.length > 0 && <> E.g. {preview.examples.map((e, i) => <span key={i} className="conversion-example">
        “{e.from}” → {Array.isArray(e.to) ? e.to.join(", ") : String(e.to)}</span>)}</>}
      {preview.kept > 0 && <div>
        {preview.kept} value{preview.kept === 1 ? "" : "s"} can't be shown as {typeLabel} (
        {preview.unfit.map((u) => `“${u}”`).join(", ")}{preview.unfit.length >= 5 ? ", …" : ""}).
        They aren't deleted: they're kept, and come back if you change the type back.</div>}
      {preview.restored > 0 && <div>{preview.restored} value{preview.restored === 1 ? "" : "s"} kept by an earlier change will come back.</div>}
    </div>
  );

  // Started / Completed date: a date that fills itself in; its formula guessed from the table (a status, a checkbox)
  const pickStamp = (kind: "started" | "completed") => {
    pickType("date");
    const label = kind === "started" ? "Started date" : "Completed date";
    if (!name.trim() || FIELD_TYPES.some((x) => x.label === name) || name === "Started date" || name === "Completed date") setName(label);
    const status = fields.find((f) => f.type === "single_select" && /status|stage|state/i.test(f.name)) ?? fields.find((f) => f.type === "single_select");
    const choice = (re: RegExp) => status?.options.choices?.find((c) => re.test(c.name))?.name;
    const box = fields.find((f) => f.type === "checkbox" && /done|complete|finished/i.test(f.name)) ?? fields.find((f) => f.type === "checkbox");
    const q = (v: string) => `"${v.replace(/"/g, '\\"')}"`;
    let when = "";
    if (kind === "started") {
      const c = choice(/progress|doing|started|active|working/i);
      when = status && c ? `{${status.name}} = ${q(c)}` : box ? `{${box.name}}` : "";
    } else {
      const c = choice(/done|complete|finished|closed/i);
      when = box ? `{${box.name}}` : status && c ? `{${status.name}} = ${q(c)}` : "";
    }
    set({ stampKind: kind, stampWhen: when, stampClear: kind === "completed", includeTime: true });
  };
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
    delete options._inner; delete options._error; delete options._filesTable; delete options.displayExpression; delete options.resultType; delete options.displayStampWhen;
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
            : BUILTIN_KEYS.has(field!.system_key ?? "")
            ? "Built in: every table keeps when and by whom each record was made and last changed. Show it in a view (Fields), hide it again; it can't be changed or deleted."
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
          <TypePicker value={type} onChange={(t) => { pickType(t); if (t === "date") set({ stampWhen: null, stampKind: null, stampClear: false }); }}
            disabled={system} stamp={o.stampKind} onStamp={pickStamp} />
          <span className="hint">{FIELD_TYPES.find((t) => t.type === type)?.desc}</span>
        </label>
        {conversionNote && <div className="notice">{conversionNote}</div>}
        {previewNote}
        {field?.options._error && !typeChanged && <div className="error">{field.options._error}</div>}

        {/* ---------------- basic */}
        {(type === "single_select" || type === "multi_select") && (
          <Section title="Options">
            <ChoiceList choices={choices} onChange={setChoices} />
            <Toggle checked={o.allowNew !== false} onChange={(v) => set({ allowNew: v ? undefined : false })}
              label="Allow new options when editing" hint="Off: only the options above can be picked; new ones are added here." />
          </Section>
        )}
        {type === "number" && <Section title="Formatting"><NumberFormat o={o} set={set} /></Section>}
        {/* fields that work out a number or a date: how it's shown */}
        {["formula", "rollup", "conditional_rollup", "lookup", "conditional_lookup"].includes(type) && (
          <Section title="Formatting">
            {(() => {
              const inner = type === "lookup" || type === "conditional_lookup" ? foreignFields?.find((f) => f.id === o.lookupFieldId) : undefined;
              const rollup = type === "rollup" || type === "conditional_rollup";
              // (a rollup's result: a number for counts, sums, averages; max / min of dates are dates)
              const rt = inner ? valueType(inner) : field?.type === type ? field.options.resultType : undefined;
              const numeric = rollup && ["countall", "counta", "count", "sum", "average", "max", "min"].includes(o.func ?? "");
              const showNumber = rt === "number" || (numeric && rt !== "date") || (!rt && type === "formula");
              const showDate = rt === "date" || (!rt && type === "formula");
              return (<>
                {!rt && type === "formula" && <span className="hint">Used when the formula works out a number (or a date, below).</span>}
                {showNumber && <NumberFormat o={o} set={set} />}
                {showDate && <DateFormat o={o} set={set} />}
                {!showNumber && !showDate && <span className="hint">{inner ? "Shown as the looked-up field shows it." : "Text and other values are shown as they are."}</span>}
              </>);
            })()}
          </Section>
        )}
        {(type === "created_time" || type === "last_modified_time") && <Section title="Formatting"><DateFormat o={o} set={set} always /></Section>}
        {type === "date" && (o.stampKind || o.stampWhen != null) && (
          <Section title="Fills itself in">
            <span className="hint">The date (and time) is filled in when this formula becomes true, for example
              <code>{"{Status} = \"Doing\""}</code>, <code>{"{Done}"}</code> or <code>{"AND({Done}, {Approved})"}</code>. It's checked whenever the record changes.</span>
            <FormulaEditor tableId={tableId} fieldId={field?.id} fields={fields} value={o.stampWhen ?? ""} onChange={(v) => set({ stampWhen: v })} />
            <Toggle checked={!!o.stampClear} onChange={(v) => set({ stampClear: v })} label="Clear it when the formula is no longer true" />
            <span className="hint">{o.stampClear ? "Undoing it (unticking, moving back) empties the date; doing it again fills in the new time."
              : "Once filled in, the date stays, even if the formula stops being true (a start date stays when the work moves on to Done)."}
              {" "}Records the formula is already true for get the time they were last changed.</span>
          </Section>
        )}
        {type === "date" && !o.stampKind && o.stampWhen == null && !system && (
          <button className="btn sm ghost" type="button" onClick={() => set({ stampWhen: "" })}>Fill it in automatically…</button>
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
        {type === "recurrence" && !system && (() => {
          const boxes = fields.filter((f) => f.type === "checkbox" && !f.system_key);
          const dates = fields.filter((f) => f.type === "date");
          const chosen = new Set(o.dateFields ?? dates.map((f) => f.id));
          return (
            <>
              <label className="stack">When this checkbox is ticked, move on to the next date
                <select className="input" value={o.doneField === false ? "__none" : o.doneField ?? ""}
                  onChange={(e) => set({ doneField: e.target.value === "__none" ? false : e.target.value || null })}>
                  <option value="">{boxes.length === 1 ? `Automatic (${boxes[0].name})` : "Automatic (when the table has one checkbox)"}</option>
                  {boxes.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                  <option value="__none">None: don't move on</option>
                </select>
              </label>
              {dates.length > 1 && (
                <div className="stack">Dates that move
                  {dates.map((f) => (
                    <label key={f.id} className="inline-check"><input type="checkbox" checked={chosen.has(f.id)}
                      onChange={(e) => { const n = new Set(chosen); if (e.target.checked) n.add(f.id); else n.delete(f.id);
                        set({ dateFields: n.size === dates.length ? null : [...n] }); }} /> {f.name}</label>
                  ))}
                </div>
              )}
              <p className="hint">Ticking a repeating record keeps a ticked copy, and moves the record on to its next date, unticked.
                Later dates can be done ahead of time from the record.</p>
            </>
          );
        })()}
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
            {o.foreignTableId && foreignFields && !o.displayFieldId && (() => {
              // how its linked records look, wherever the field shows: the table, the record, pages, boards, shared links
              const chip = o.chip ?? {};
              const setChip = (p: Partial<NonNullable<FieldOptions["chip"]>>) => set({ chip: { ...chip, ...p } });
              const usable = foreignFields.filter((f) => !f.is_primary && !["button", "long_text"].includes(f.type));
              return (
                <div className="chip-look">
                  <label className="stack">How linked records look
                    <select className="input" value={chip.style ?? "chip"} onChange={(e) => setChip({ style: e.target.value as ChipStyle })}>
                      {CHIP_STYLES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                    </select>
                  </label>
                  {chip.style !== "plain" && chip.style !== "compact" && (
                    <div className="stack">
                      <span>Also show in each</span>
                      <FieldToggles fields={usable} value={chip.fields ?? []} max={4} onChange={(v) => setChip({ fields: v })} />
                    </div>
                  )}
                  <div className="row-2">
                    <label className="stack">Colour from
                      <select className="input" value={chip.colorField ?? ""} onChange={(e) => setChip({ colorField: e.target.value || null })}>
                        <option value="">No colour</option>
                        {foreignFields.filter((f) => f.type === "single_select").map(fieldOpt)}
                      </select>
                    </label>
                    {chip.style !== "plain" && (
                      <label className="stack">Picture from
                        <select className="input" value={chip.imageField ?? ""} onChange={(e) => setChip({ imageField: e.target.value || null })}>
                          <option value="">No picture</option>
                          {foreignFields.filter((f) => f.type === "attachment" || ((f.type === "lookup") && f.options._inner?.type === "attachment")).map(fieldOpt)}
                        </select>
                      </label>
                    )}
                  </div>
                </div>
              );
            })()}
            {o.foreignTableId && foreignInfo && (
              <div className="link-pick">
                <div className="stack">
                  <span>Columns in the picker</span>
                  <FieldToggles fields={foreignInfo.fields.filter((f) => !f.is_primary && !["button", "long_text"].includes(f.type))}
                    value={o.pickFields ?? []} max={6} onChange={(v) => set({ pickFields: v })} />
                  <span className="hint">The picker lists the records as a table: their title and these fields (none chosen: the first few).</span>
                </div>
                <label className="stack">Records to choose from
                  <select className="input" value={o.pickView ?? ""} onChange={(e) => set({ pickView: e.target.value || null })}>
                    <option value="">All records</option>
                    {foreignInfo.views.filter((v) => v.type !== "chart").map((v) => <option key={v.id} value={v.id}>Those in the view “{v.name}”</option>)}
                  </select>
                </label>
                <details className="link-pick-filter" open={!!o.pickFilter?.items?.length}>
                  <summary>{o.pickFilter?.items?.length ? `Only records matching ${o.pickFilter.items.length} condition${o.pickFilter.items.length === 1 ? "" : "s"}` : "Filter the records…"}</summary>
                  <FilterEditor fields={foreignInfo.fields} filter={o.pickFilter ?? { conjunction: "and", items: [] }}
                    onChange={(g: FilterGroup) => set({ pickFilter: g.items.length ? g : null })} />
                </details>
                <span className="hint">The link's picker offers only these records (records already linked stay linked).</span>
              </div>
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
                          : valueType(foreignFields.find((x) => x.id === c.field)) === "date"
                            ? <DateValue value={String(c.value ?? "")} onChange={(v) => setFilter(i, { value: v })} />
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

        {/* ---------------- how a field holding several records / values is shown, in the cells and on records */}
        {(displaysFor({ type, options: o } as Field, "record").length > 1 || displaysFor({ type, options: o } as Field, "cell").length > 1) && (() => {
          const v = o.views ?? {};
          const setV = (p: Partial<FieldViews>) => {
            const n = { ...v, ...p };
            (Object.keys(n) as (keyof FieldViews)[]).forEach((k) => n[k] === undefined && delete n[k]);
            set({ views: Object.keys(n).length ? n : undefined });
          };
          const pick = (where: "cell" | "record", label: string) => {
            const opts = displaysFor({ type, options: o } as Field, where);
            if (opts.length < 2) return null;
            return (
              <label className="stack">{label}
                <select className="input" value={v[where] && opts.includes(v[where]!) ? v[where] : opts[0]}
                  onChange={(e) => setV({ [where]: e.target.value === opts[0] ? undefined : e.target.value as FieldView })}>
                  {opts.map((x) => <option key={x} value={x}>{VIEW_LABEL[x]}</option>)}
                </select>
              </label>
            );
          };
          const recordView = v.record ?? displaysFor({ type, options: o } as Field, "record")[0];
          const listed = type === "link" && ["list", "table", "cards", "hcards"].includes(recordView) && foreignFields;
          const chosen = new Set(v.fields ?? []);
          return (
            <Section title="How it's shown">
              <div className="row-2">
                {pick("cell", "In the table")}
                {pick("record", "On the record and its page")}
              </div>
              {listed && (
                <div className="stack">Fields of the linked records to show
                  <div className="view-fields-pick">
                    {foreignFields!.filter((f) => f.type !== "button" && f.type !== "record_id").map((f) => (
                      <label key={f.id} className="inline-check"><input type="checkbox" checked={chosen.has(f.id)}
                        onChange={(e) => { const n = (v.fields ?? []).filter((x) => x !== f.id); if (e.target.checked) n.push(f.id); setV({ fields: n.length ? n : undefined }); }} />
                        <FieldIcon type={f.type} /> {f.name}</label>
                    ))}
                  </div>
                  <span className="hint">{chosen.size ? "In this order: untick and tick again to move one to the end." : "None chosen: the title and the next few fields."}</span>
                </div>
              )}
              {(v.cell === "bar" || v.record === "bar") && (
                <label className="stack">A full bar is
                  <input className="input" type="number" min={0} step="any" placeholder="1 for fractions, else 100" value={v.max ?? ""}
                    onChange={(e) => setV({ max: e.target.value && Number(e.target.value) > 0 ? Number(e.target.value) : undefined })} />
                </label>
              )}
              <span className="hint">Each page can show it its own way too.</span>
            </Section>
          );
        })()}

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

const NUMBER_FORMATS: { value: NonNullable<FieldOptions["numberFormat"]> | "decimal"; label: string }[] = [
  { value: "decimal", label: "Decimal (1.23)" }, { value: "percent", label: "Percent (12%)" }, { value: "currency", label: "Currency ($100)" },
  { value: "bytes", label: "File size (1.2 MB)" },
];

/** How a number is shown: decimal, percent, currency (with its symbol), file size; decimal places and separators. */
function NumberFormat({ o, set }: { o: FieldOptions; set: (p: Partial<FieldOptions>) => void }) {
  const kind = o.numberFormat ?? "decimal";
  return (
    <div className="format-block">
      <label className="stack">Format type
        <select className="input" value={kind} onChange={(e) => set({ numberFormat: e.target.value === "decimal" ? undefined : e.target.value as FieldOptions["numberFormat"] })}>
          {NUMBER_FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label.replace("$", kind === "currency" ? o.currency ?? "$" : "$")}</option>)}
        </select>
      </label>
      {kind === "currency" && (
        <label className="stack">Currency symbol
          <input className="input" value={o.currency ?? "$"} maxLength={4} onChange={(e) => set({ currency: e.target.value })} />
        </label>
      )}
      {kind !== "bytes" && (
        <label className="stack">Precision
          <select className="input" value={o.precision ?? ""} onChange={(e) => set({ precision: e.target.value === "" ? undefined : Number(e.target.value) })}>
            <option value="">Automatic</option>
            {[0, 1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n === 0 ? "1" : `1.${"0".repeat(n)}`}</option>)}
          </select>
        </label>
      )}
      {kind !== "bytes" && <Toggle checked={o.separators !== false} onChange={(v) => set({ separators: v ? undefined : false })} label="Thousands separators" />}
      <span className="hint">Example: {formatNumber(kind === "percent" ? 0.4567 : kind === "bytes" ? 1234567 : 12345.678, o)}</span>
    </div>
  );
}

/** How a date is shown (fields that work one out: formulas, rollups, lookups, created / modified time). */
function DateFormat({ o, set, always }: { o: FieldOptions; set: (p: Partial<FieldOptions>) => void; always?: boolean }) {
  return (
    <div className="format-block">
      <label className="stack">Date format
        <select className="input" value={o.dateFormat ?? "local"} onChange={(e) => set({ dateFormat: e.target.value === "local" ? undefined : e.target.value as FieldOptions["dateFormat"] })}>
          {DATE_FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </label>
      <label className="stack">Time format
        <select className="input" value={o.timeFormat ?? ""} onChange={(e) => set({ timeFormat: (e.target.value || undefined) as FieldOptions["timeFormat"] })}>
          <option value="">As on this device</option><option value="12">12 hour (2:30 PM)</option><option value="24">24 hour (14:30)</option>
        </select>
      </label>
      <span className="hint">Example: {formatDate("2026-10-11T14:30:00Z", true, o)}{always ? "" : " (a date without a time shows just the date)"}</span>
    </div>
  );
}

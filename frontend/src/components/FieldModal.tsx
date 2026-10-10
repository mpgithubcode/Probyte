import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { Choice, Field, FieldOptions, FieldType, FilterCond, TableInfo } from "../types";
import { COMPUTED_TYPES, FIELD_TYPES, PALETTE, ROLLUP_FUNCS, isEditable } from "../util";
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

export function FieldModal({ tableId, tableName, field, fields, tables, onClose, onSaved }: {
  tableId: string; tableName: string; field: Field | null; fields: Field[]; tables: TableInfo[];
  onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState(field?.name ?? "");
  const [type, setType] = useState<FieldType>(field?.type ?? "text");
  const [o, setO] = useState<FieldOptions>(() => ({
    ...(field?.options ?? {}),
    ...(field?.type === "formula" ? { expression: field.options.displayExpression ?? field.options.expression ?? "" } : {}),
  }));
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
    if (!name.trim() || FIELD_TYPES.some((x) => x.label === name)) setName(FIELD_TYPES.find((x) => x.type === t)!.label);
    if (t === "rollup" || t === "conditional_rollup") set({ func: o.func ?? "sum" });
    if (t === "button") set({ label: o.label ?? "Click", action: o.action ?? "update", updates: o.updates ?? [] });
  };

  const save = async () => {
    if (!name.trim()) return setError("Name is required");
    const options: FieldOptions = { ...o };
    delete options._inner; delete options._error; delete options.displayExpression; delete options.resultType;
    if (type === "single_select" || type === "multi_select") options.choices = (o.choices ?? []).filter((c) => c.name.trim());
    setBusy(true);
    try {
      if (field) await api.patch(`/fields/${field.id}`, { name: name.trim(), type, options });
      else await api.post(`/tables/${tableId}/fields`, { name: name.trim(), type, options });
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
      width={wide ? 980 : 520} onClose={onClose}
      footer={<>
        {field && !system && !field.is_primary && (confirmDelete
          ? <><button className="btn danger" onClick={remove}>Delete field + data</button><button className="btn" onClick={() => setConfirmDelete(false)}>Cancel</button></>
          : <button className="btn danger-ghost" onClick={() => setConfirmDelete(true)}><Icon name="trash" size={14} /> Delete</button>)}
        <span className="spacer" />
        {system ? <button className="btn primary" onClick={onClose}>Close</button> : <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
        </>}
      </>}>
      {system && (
        <div className="notice lock-notice">
          <Icon name="lock" size={16} />
          <span>{field!.system_key === "record_id"
            ? "Built in: every table has its record IDs. It can be hidden in a view but not changed."
            : "Locked: this field is part of the synced calendar / contact item, so it can't be renamed, changed or deleted. Hide it in a view if you don't need it, or add your own field."}</span>
        </div>
      )}
      <fieldset className="form plain-fieldset" disabled={system}>
        <div className="row-2">
          <label className="stack">Name<input className="input" autoFocus={!wide} value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !wide && save()} /></label>
          <label className="stack">Type
            <TypePicker value={type} onChange={pickType} disabled={system} />
          </label>
        </div>
        {conversionNote && <div className="notice">{conversionNote}</div>}
        {field?.options._error && !typeChanged && <div className="error">{field.options._error}</div>}

        {/* ---------------- basic */}
        {(type === "single_select" || type === "multi_select") && (
          <div className="stack">Options
            {choices.map((c, i) => (
              <div key={i} className="choice-row">
                <input type="color" value={c.color} onChange={(e) => setChoices(choices.map((x, j) => j === i ? { ...x, color: e.target.value } : x))} />
                <input className="input" value={c.name} onChange={(e) => setChoices(choices.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
                <button className="icon-btn" onClick={() => setChoices(choices.filter((_, j) => j !== i))}><Icon name="x" size={14} /></button>
              </div>
            ))}
            <button className="ghost-btn" onClick={() => setChoices([...choices, { name: "", color: PALETTE[choices.length % PALETTE.length] }])}>
              <Icon name="plus" size={14} /> Add option
            </button>
          </div>
        )}
        {type === "date" && (
          <label className="inline-check"><input type="checkbox" checked={!!o.includeTime} onChange={(e) => set({ includeTime: e.target.checked })} /> Include time</label>
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
        {error && <div className="error">{error}</div>}
      </fieldset>
    </Modal>
  );
}

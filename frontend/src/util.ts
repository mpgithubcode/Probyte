import type { Field, FieldOptions, FieldType, TableInfo, TableKind, Filter, FilterGroup, Rec, Refs, SortSpec, ValueType, ViewConfig, SharePerms, LinkPerms, DateGrouping } from "./types";

export const PALETTE = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#14b8a6", "#ec4899", "#64748b", "#84cc16", "#f97316"];

export interface FieldTypeInfo { type: FieldType; label: string; desc: string; group: "Basic" | "Advanced" | "System" }

export const FIELD_TYPES: FieldTypeInfo[] = [
  { type: "text", label: "Single line text", desc: "A short piece of text", group: "Basic" },
  { type: "long_text", label: "Long text", desc: "Notes and paragraphs", group: "Basic" },
  { type: "number", label: "Number", desc: "Integers or decimals", group: "Basic" },
  { type: "single_select", label: "Single select", desc: "Pick one option from a list", group: "Basic" },
  { type: "multi_select", label: "Multiple select", desc: "Pick several options (tags)", group: "Basic" },
  { type: "user", label: "User", desc: "One or more Probyte users", group: "Basic" },
  { type: "date", label: "Date", desc: "A date, optionally with time", group: "Basic" },
  { type: "rating", label: "Rating", desc: "Stars from 1 to N", group: "Basic" },
  { type: "checkbox", label: "Checkbox", desc: "Yes / no", group: "Basic" },
  { type: "attachment", label: "Attachment", desc: "Upload files and images", group: "Basic" },
  { type: "url", label: "URL", desc: "A web link", group: "Basic" },
  { type: "email", label: "Email", desc: "An email address", group: "Basic" },
  { type: "phone", label: "Phone number", desc: "A phone number you can call", group: "Basic" },
  { type: "formula", label: "Formula", desc: "Calculate a value from other fields", group: "Advanced" },
  { type: "recurrence", label: "Repeat", desc: "Makes the record repeat on the calendar and timeline", group: "Advanced" },
  { type: "link", label: "Link to record", desc: "Connect records in another table", group: "Advanced" },
  { type: "lookup", label: "Lookup", desc: "Show a field from linked records", group: "Advanced" },
  { type: "rollup", label: "Rollup", desc: "Summarise a field of linked records", group: "Advanced" },
  { type: "conditional_lookup", label: "Conditional lookup", desc: "Show values from records that match conditions", group: "Advanced" },
  { type: "conditional_rollup", label: "Conditional rollup", desc: "Summarise records that match conditions", group: "Advanced" },
  { type: "button", label: "Button", desc: "Update fields or open a link in one click", group: "Advanced" },
  { type: "autonumber", label: "Auto number", desc: "A unique, increasing number per record", group: "Advanced" },
  { type: "record_id", label: "Record ID", desc: "The row's unique ID (UUID)", group: "System" },
  { type: "created_time", label: "Created time", desc: "When the record was created", group: "System" },
  { type: "last_modified_time", label: "Last modified time", desc: "When the record was last changed", group: "System" },
  { type: "created_by", label: "Created by", desc: "Who created the record", group: "System" },
  { type: "last_modified_by", label: "Last modified by", desc: "Who last changed the record", group: "System" },
];

export const COMPUTED_TYPES = new Set<FieldType>(["formula", "lookup", "rollup", "conditional_lookup", "conditional_rollup", "record_id",
  "button", "autonumber", "created_time", "last_modified_time", "created_by", "last_modified_by"]);

export const ROLLUP_FUNCS: { value: string; label: string }[] = [
  { value: "countall", label: "Count all" }, { value: "counta", label: "Count values" }, { value: "count", label: "Count numbers" },
  { value: "sum", label: "Sum" }, { value: "average", label: "Average" }, { value: "max", label: "Max" }, { value: "min", label: "Min" },
  { value: "and", label: "All true (AND)" }, { value: "or", label: "Any true (OR)" }, { value: "xor", label: "Odd number true (XOR)" },
  { value: "array_join", label: "Join with commas" }, { value: "concatenate", label: "Concatenate" },
  { value: "array_unique", label: "Unique values" }, { value: "array_compact", label: "Non-empty values" },
];

/** How a field's values behave: drives display, sorting, charts and which views can use it. */
export function valueType(f: Field | undefined): ValueType {
  if (!f) return "any";
  switch (f.type) {
    case "number": case "rating": case "autonumber": return "number";
    case "date": case "created_time": case "last_modified_time": return "date";
    case "checkbox": return "boolean";
    case "multi_select": case "link": case "user": case "attachment": case "lookup": case "conditional_lookup": return "array";
    case "formula": case "rollup": case "conditional_rollup": return f.options.resultType ?? "any";
    default: return "text";
  }
}

export const isDateField = (f: Field) => valueType(f) === "date";
export const isEditable = (f: Field) => !f.computed && !f.readonly && !COMPUTED_TYPES.has(f.type);
export const isErrorValue = (v: unknown): v is { __error: string } => !!v && typeof v === "object" && "__error" in (v as object);

/** A pseudo-field describing the items a lookup/rollup collects (for rendering them). */
export function innerField(f: Field): Field | undefined {
  const inn = f.options._inner;
  return inn ? { id: `${f.id}:inner`, name: inn.name, type: inn.type, options: inn.options } : undefined;
}

export const isDateOnly = (v: unknown): v is string => typeof v === "string" && v.length === 10;

const pad = (n: number) => String(n).padStart(2, "0");

/** Local calendar day (YYYY-MM-DD) a value falls on. */
export function dayKey(v: unknown): string | null {
  if (!v || typeof v !== "string") return null;
  if (isDateOnly(v)) return v;
  const d = new Date(v);
  if (isNaN(+d)) return null;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function keyOf(d: Date) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function parseKey(k: string) {
  const [y, m, d] = k.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function addDaysKey(k: string, n: number) {
  const d = parseKey(k);
  d.setDate(d.getDate() + n);
  return keyOf(d);
}

/** Shift a date/datetime value by n days. */
export function shiftValue(v: unknown, n: number): unknown {
  if (!v || typeof v !== "string") return v;
  if (isDateOnly(v)) return addDaysKey(v, n);
  const d = new Date(v);
  if (isNaN(+d)) return v;
  d.setDate(d.getDate() + n); // local-day shift keeps wall-clock time across DST
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export const DATE_FORMATS: { value: NonNullable<FieldOptions["dateFormat"]>; label: string }[] = [
  { value: "local", label: "Local (Oct 11, 2026)" }, { value: "long", label: "Long (Sunday, October 11, 2026)" },
  { value: "iso", label: "ISO (2026-10-11)" }, { value: "us", label: "US (10/11/2026)" }, { value: "eu", label: "European (11/10/2026)" },
];

function formatDay(d: Date, fmt?: FieldOptions["dateFormat"]) {
  switch (fmt) {
    case "iso": return keyOf(d);
    case "us": return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}/${d.getFullYear()}`;
    case "eu": return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
    case "long": return d.toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
    default: return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }
}

/** A date value as text; o: the field's date / time format. */
export function formatDate(v: unknown, includeTime?: boolean, o?: Pick<FieldOptions, "dateFormat" | "timeFormat">) {
  if (!v || typeof v !== "string") return "";
  if (isDateOnly(v)) return formatDay(parseKey(v), o?.dateFormat);
  const d = new Date(v);
  if (isNaN(+d)) return v;
  if (!includeTime) return formatDay(d, o?.dateFormat);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit",
    ...(o?.timeFormat ? { hour12: o.timeFormat === "12" } : {}) });
  return `${formatDay(d, o?.dateFormat)}, ${time}`;
}

export function formatTime(v: unknown) {
  if (!v || typeof v !== "string" || isDateOnly(v)) return "";
  const d = new Date(v);
  return isNaN(+d) ? "" : d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Value -> <input type=date|datetime-local> value. */
export function toInputValue(v: unknown, includeTime?: boolean) {
  if (!v || typeof v !== "string") return "";
  if (isDateOnly(v)) return includeTime ? `${v}T00:00` : v;
  const d = new Date(v);
  if (isNaN(+d)) return "";
  const day = keyOf(d);
  return includeTime ? `${day}T${pad(d.getHours())}:${pad(d.getMinutes())}` : day;
}

export function fromInputValue(s: string, includeTime?: boolean, previous?: unknown): string | null {
  if (!s) return null;
  if (!includeTime) return s;
  // Midnight on a previously date-only value stays date-only (keeps all-day items all-day).
  if (s.endsWith("T00:00") && (isDateOnly(previous) || !previous)) return s.slice(0, 10);
  return new Date(s).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function choiceColor(field: Field | undefined, name: unknown) {
  return field?.options.choices?.find((c) => c.name === name)?.color ?? "#94a3b8";
}

export function primaryField(fields: Field[]) {
  return fields.find((f) => f.is_primary) ?? fields.find((f) => f.type === "text") ?? fields[0];
}

export function recordTitle(fields: Field[], r: Rec) {
  const p = primaryField(fields);
  const v = p ? r.data[p.id] : null;
  return v ? String(v) : "Untitled";
}

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toLocaleString(undefined, { maximumFractionDigits: v < 10 ? 1 : 0 })} ${units[i]}`;
}

/** A number as text; o: the field's precision, or its number format (decimal / percent / currency, separators). */
export function formatNumber(v: unknown, o?: number | Pick<FieldOptions, "precision" | "numberFormat" | "currency" | "separators">) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  const opts = typeof o === "number" ? { precision: o } : o ?? {};
  const p = opts.precision;
  const fixed = p !== undefined && p !== null;
  if (opts.numberFormat === "bytes") return formatBytes(n);
  const x = opts.numberFormat === "percent" ? n * 100 : n;
  let s = x.toLocaleString(undefined, {
    minimumFractionDigits: fixed ? p : opts.numberFormat === "currency" ? 2 : 0,
    maximumFractionDigits: fixed ? p : opts.numberFormat === "currency" ? 2 : 10,
    useGrouping: opts.separators !== false,
  });
  if (opts.numberFormat === "percent") s += "%";
  if (opts.numberFormat === "currency") s = n < 0 ? `-${opts.currency ?? "$"}${s.slice(1)}` : `${opts.currency ?? "$"}${s}`;
  return s;
}

export function displayText(field: Field, v: unknown, refs?: Refs): string {
  if (v === null || v === undefined) return "";
  if (isErrorValue(v)) return "#ERROR";
  switch (field.type) {
    case "link":
      return (Array.isArray(v) ? v : [v]).map((id) => refs?.records[String(id)] ?? "").join(", ");
    case "user": case "created_by": case "last_modified_by":
      return (Array.isArray(v) ? v : [v]).map((id) => refs?.users[String(id)] ?? "").join(", ");
    case "attachment":
      return (Array.isArray(v) ? v : []).map((a: { name?: string }) => a?.name ?? "").join(", ");
    case "lookup": case "conditional_lookup": {
      const inner = innerField(field);
      return (Array.isArray(v) ? v : [v]).map((x) => (inner ? displayText(inner, x, refs) : String(x))).join(", ");
    }
    case "checkbox": return v ? "true" : "false";
    case "phone": return formatPhone(v);
    case "date": return formatDate(v, field.options.includeTime, field.options);
    case "created_time": case "last_modified_time": return formatDate(v, true, field.options);
  }
  const vt = valueType(field);
  if (vt === "date") return formatDate(v, !isDateOnly(v), field.options);
  if (vt === "number" && typeof v === "number") return formatNumber(v, field.type === "rating" ? undefined : field.options);
  if (vt === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

const isEmpty = (v: unknown) => v === null || v === undefined || v === "" || v === false || (Array.isArray(v) && v.length === 0);

/** The days a date filter's value stands for, worked out now: "2026-10-12", "2026-10-01..2026-10-31", today / tomorrow /
 * yesterday (also "today()"), current / last / next week, month or year, "3 days ago", "3 days from now" ("in 3 days"),
 * weeks and months too, "past 7 days", "next 30 days", "today() - 7". [first day, last day] as day keys, or null.
 * Keep in step with date_range() in backend/app/calc/viewfilter.py. */
export function dateRange(value: unknown, today = new Date(), weekStart = weekStartsOn()): [string, string] | null {
  const v = String(value ?? "").trim().toLowerCase().replace(/\(\)/g, "").replace(/\s+/g, " ");
  if (!v) return null;
  const day = (d: Date) => keyOf(d);
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const [y, mo, d] = [t.getFullYear(), t.getMonth(), t.getDate()];
  const plus = (n: number, unit = "day") => {
    if (unit.startsWith("week")) return new Date(y, mo, d + 7 * n);
    if (unit.startsWith("month")) return new Date(y, mo + n, Math.min(d, new Date(y, mo + n + 1, 0).getDate()));  // (Jan 31 + 1 month: Feb 28)
    return new Date(y, mo, d + n);
  };
  let m: RegExpMatchArray | null;
  if ((m = v.match(/^(\d{4}-\d{2}-\d{2})$/))) return [m[1], m[1]];
  if ((m = v.match(/^(\d{4}-\d{2}-\d{2})\s*(?:\.\.|to)\s*(\d{4}-\d{2}-\d{2})$/))) return m[1] <= m[2] ? [m[1], m[2]] : [m[2], m[1]];
  if (v === "today" || v === "now") return [day(t), day(t)];
  if (v === "tomorrow") return [day(plus(1)), day(plus(1))];
  if (v === "yesterday") return [day(plus(-1)), day(plus(-1))];
  if ((m = v.match(/^(current|this|last|previous|next) (week|month|year)$/))) {
    const shift = m[1] === "next" ? 1 : m[1] === "last" || m[1] === "previous" ? -1 : 0;
    if (m[2] === "week") {
      const start = new Date(t.getFullYear(), t.getMonth(), t.getDate() - ((t.getDay() - weekStart + 7) % 7) + 7 * shift);
      return [day(start), day(new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6))];
    }
    if (m[2] === "month") return [day(new Date(t.getFullYear(), t.getMonth() + shift, 1)), day(new Date(t.getFullYear(), t.getMonth() + shift + 1, 0))];
    return [`${t.getFullYear() + shift}-01-01`, `${t.getFullYear() + shift}-12-31`];
  }
  if ((m = v.match(/^(\d+) (day|week|month)s? ago$/))) { const d = plus(-Number(m[1]), m[2]); return [day(d), day(d)]; }
  if ((m = v.match(/^(?:in (\d+) (day|week|month)s?|(\d+) (day|week|month)s? from now)$/))) {
    const d = plus(Number(m[1] ?? m[3]), m[2] ?? m[4]); return [day(d), day(d)];
  }
  if ((m = v.match(/^(?:past|last) (\d+) (day|week|month)s?$/))) return [day(plus(-Number(m[1]), m[2])), day(t)];
  if ((m = v.match(/^next (\d+) (day|week|month)s?$/))) return [day(t), day(plus(Number(m[1]), m[2]))];
  if ((m = v.match(/^today ?([+-]) ?(\d+)$/))) { const d = plus((m[1] === "-" ? -1 : 1) * Number(m[2])); return [day(d), day(d)]; }
  return null;
}

function matches(field: Field, v: unknown, f: Filter, refs?: Refs): boolean {
  const text = displayText(field, v, refs).toLowerCase();
  const val = String(f.value ?? "").toLowerCase();
  const vt = valueType(field);
  // dates compare by day against a day or a period (exact, or worked out now: today, current week, 3 days ago…)
  const range = vt === "date" && ["is", "is_not", "gt", "gte", "lt", "lte"].includes(f.op) ? dateRange(val) : null;
  if (range) {
    const day = dayKey(v);
    if (!day) return f.op === "is_not";
    const [a, b] = range;
    return { is: day >= a && day <= b, is_not: day < a || day > b, gt: day > b, gte: day >= a, lt: day < a, lte: day <= b }[f.op as "is"]!;
  }
  switch (f.op) {
    case "empty": return isEmpty(v);
    case "not_empty": return !isEmpty(v);
    case "contains": return text.includes(val);
    case "not_contains": return !text.includes(val);
    case "is": return Array.isArray(v) ? (displayText(field, v, refs).toLowerCase().split(", ").includes(val) || v.some((x) => String(x).toLowerCase() === val)) : text === val || (field.type === "checkbox" && String(!!v) === val);
    case "is_not": return !matches(field, v, { ...f, op: "is" }, refs);
    case "gt": case "gte": case "lt": case "lte": {
      if (isEmpty(v) || val === "") return false;
      const x = Number(v), y = Number(val);
      if (Number.isNaN(x) || Number.isNaN(y)) return false;
      return f.op === "gt" ? x > y : f.op === "gte" ? x >= y : f.op === "lt" ? x < y : x <= y;
    }
  }
  return true;
}

function compare(field: Field, a: unknown, b: unknown, refs?: Refs) {
  if (isEmpty(a) && isEmpty(b)) return 0;
  if (isEmpty(a)) return 1;
  if (isEmpty(b)) return -1;
  const vt = valueType(field);
  if (vt === "number") return Number(a) - Number(b);
  if (vt === "date") return +new Date(a as string) - +new Date(b as string);
  if (field.type === "single_select") {
    const names = field.options.choices?.map((c) => c.name) ?? [];
    return names.indexOf(a as string) - names.indexOf(b as string);
  }
  return displayText(field, a, refs).localeCompare(displayText(field, b, refs), undefined, { numeric: true });
}

const isGroup = (n: Filter | FilterGroup): n is FilterGroup => Array.isArray((n as FilterGroup).items);

/** A view's filter as a group; older views kept a flat list of AND-ed conditions. */
export function viewFilter(config: ViewConfig): FilterGroup {
  return config.filter && Array.isArray(config.filter.items) ? config.filter : { conjunction: "and", items: [] };
}
export const viewSorts = (config: ViewConfig): SortSpec[] => config.sorts ?? [];
/** Group levels for Table / List / Gallery (older views: groupBy). */
export const viewGroups = (config: ViewConfig): SortSpec[] => config.groups ?? [];  // (a board's groupBy is its columns, not this)
export const countConditions = (g: FilterGroup): number => g.items.reduce((n, i) => n + (isGroup(i) ? countConditions(i) : 1), 0);

function test(byId: Map<string, Field>, r: Rec, node: Filter | FilterGroup, refs?: Refs): boolean {
  if (isGroup(node)) {
    if (!node.items.length) return true;
    return node.conjunction === "or" ? node.items.some((i) => test(byId, r, i, refs)) : node.items.every((i) => test(byId, r, i, refs));
  }
  const field = byId.get(node.field);
  // a condition still waiting for its value doesn't filter anything yet
  if (!field || (node.op !== "empty" && node.op !== "not_empty" && String(node.value ?? "") === "" && field.type !== "checkbox")) return true;
  return matches(field, r.data[field.id], node, refs);
}

/** Does a record meet these conditions (a page block's "show only when…")? No conditions: yes. */
export const recordMatches = (rec: Rec, fields: Field[], group: FilterGroup | null | undefined, refs?: Refs) =>
  !group?.items?.length || test(new Map(fields.map((f) => [f.id, f])), rec, group, refs);

export function applyView(records: Rec[], fields: Field[], config: ViewConfig, search: string, refs?: Refs) {
  const byId = new Map(fields.map((f) => [f.id, f]));
  let out = records;
  const group = viewFilter(config);
  if (group.items.length) out = out.filter((r) => test(byId, r, group, refs));
  const q = search.trim().toLowerCase();
  const qDigits = q ? phoneQuery(q) : null;
  if (q) out = out.filter((r) => fields.some((f) => displayText(f, r.data[f.id], refs).toLowerCase().includes(q)
    || (qDigits !== null && f.type === "phone" && typeof r.data[f.id] === "string" && String(r.data[f.id]).replace(/\D/g, "").includes(qDigits))));
  const sorts = viewSorts(config).flatMap((s) => { const f = byId.get(s.field); return f ? [{ f, dir: s.dir === "desc" ? -1 : 1 }] : []; });
  if (sorts.length) {
    out = [...out].sort((a, b) => {
      for (const { f, dir } of sorts) {
        const ea = isEmpty(a.data[f.id]), eb = isEmpty(b.data[f.id]);
        if (ea !== eb) return ea ? 1 : -1;  // empty values last in both directions
        const c = compare(f, a.data[f.id], b.data[f.id], refs);
        if (c) return dir * c;
      }
      return 0;
    });
  }
  return out;
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number) {
  let t: ReturnType<typeof setTimeout> | undefined;
  return (...a: A) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

export function timeAgo(iso: string | null) {
  if (!iso) return "never";
  const s = Math.round((Date.now() - +new Date(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}


// ------------------------------------------------------------------ hidden fields

/** Field types every view hides until it's turned on (config.shown). */
export const HIDDEN_BY_DEFAULT = new Set<FieldType>(["record_id"]);
/** The built-in fields every table has (when and by whom a record was made and last changed): also hidden until shown. */
export const BUILTIN_KEYS = new Set(["created_time", "modified_time", "created_by", "modified_by"]);
const hiddenByDefault = (f: Field) => HIDDEN_BY_DEFAULT.has(f.type) || BUILTIN_KEYS.has(f.system_key ?? "")
  || BUILTIN_KEYS.has(String((f.options as Record<string, unknown>)?._source_key ?? ""));

export function hiddenIds(fields: Field[], config: ViewConfig): Set<string> {
  const shown = new Set(config.shown ?? []);
  const out = new Set(config.hidden ?? []);
  for (const f of fields) if (hiddenByDefault(f) && !shown.has(f.id)) out.add(f.id);
  return out;
}

/** Config change that shows or hides a field in a view. */
export function setFieldShown(f: Field, show: boolean, config: ViewConfig): Partial<ViewConfig> {
  if (hiddenByDefault(f)) {
    const shown = (config.shown ?? []).filter((x) => x !== f.id);
    return { shown: show ? [...shown, f.id] : shown, hidden: (config.hidden ?? []).filter((x) => x !== f.id) };
  }
  const hidden = (config.hidden ?? []).filter((x) => x !== f.id);
  return { hidden: show ? hidden : [...hidden, f.id] };
}

// ------------------------------------------------------------------ grouping

export const canGroupBy = (f: Field) => !["attachment", "button", "long_text", "record_id", "recurrence"].includes(f.type)
  && f.id !== "__id";

export interface Group { key: string; label: string; color?: string; empty: boolean; records: Rec[] }

/** Records split by a field's value, in a sensible order (choice order, dates and numbers ascending, empty last). */
// relative-to-today date groups, in order (key: label)
const RELATIVE: [string, string][] = [["0", "Earlier"], ["1", "Yesterday"], ["2", "Today"], ["3", "Tomorrow"], ["4", "Later this week"],
  ["5", "Next week"], ["6", "Later"]];

/** A day's group when dates are grouped by week, month, year or relative to today: [key (sorts in order), label]. */
function dateGroup(day: string, by: DateGrouping): [string, string] {
  const d = parseKey(day);
  if (by === "year") return [day.slice(0, 4), day.slice(0, 4)];
  if (by === "month") return [day.slice(0, 7), d.toLocaleDateString(undefined, { month: "long", year: "numeric" })];
  if (by === "week") {
    const start = new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay() - weekStartsOn() + 7) % 7));
    return [keyOf(start), `Week of ${formatDate(keyOf(start), false)}`];
  }
  const [y0] = dateRange("yesterday")!, [t0] = dateRange("today")!, [m0] = dateRange("tomorrow")!;
  const [, weekEnd] = dateRange("current week")!, [, nextEnd] = dateRange("next week")!;
  const i = day < y0 ? 0 : day === y0 ? 1 : day === t0 ? 2 : day === m0 ? 3 : day <= weekEnd ? 4 : day <= nextEnd ? 5 : 6;
  return RELATIVE[i];
}

export function groupRecords(records: Rec[], field: Field, refs?: Refs, dateBy?: DateGrouping): Group[] {
  const groups = new Map<string, Group>();
  const sortKey = new Map<string, string | number>();
  const vt = valueType(field);
  for (const r of records) {
    const v = r.data[field.id];
    let key: string, label: string, sk: string | number;
    if (isErrorValue(v) || v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) {
      if (field.type === "checkbox" || vt === "boolean") { key = "false"; label = "Unchecked"; sk = 1; }
      else { key = "\u0000empty"; label = "(Empty)"; sk = ""; }
    } else if (field.type === "checkbox" || vt === "boolean") {
      key = v ? "true" : "false"; label = v ? "Checked" : "Unchecked"; sk = v ? 0 : 1;
    } else if (vt === "date") {
      key = dayKey(v) ?? String(v); label = formatDate(key, false); sk = key;
      if (dateBy && dateBy !== "day" && dayKey(v)) { [key, label] = dateGroup(key, dateBy); sk = key; }
    } else if (vt === "number" && typeof v === "number") {
      key = String(v); label = displayText(field, v, refs); sk = v;
    } else {
      label = displayText(field, v, refs) || "(Empty)"; key = label.toLowerCase(); sk = key;
      if (field.type === "single_select") {
        const i = (field.options.choices ?? []).findIndex((c) => c.name === v);
        sk = i < 0 ? 9999 : i;
      }
    }
    if (!groups.has(key)) {
      groups.set(key, { key, label, empty: key === "\u0000empty", records: [],
        color: field.type === "single_select" && typeof v === "string" ? choiceColor(field, v) : undefined });
      sortKey.set(key, sk);
    }
    groups.get(key)!.records.push(r);
  }
  return [...groups.values()].sort((a, b) => {
    if (a.empty !== b.empty) return a.empty ? 1 : -1;
    const x = sortKey.get(a.key)!, y = sortKey.get(b.key)!;
    return typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
  });
}


// ------------------------------------------------------------------ nested groups

export interface GroupNode extends Group { path: string; depth: number; field: Field; spec: SortSpec; children: GroupNode[] | null }

/** Records grouped by one or more fields (Teable-style nested groups). */
export function buildGroups(records: Rec[], specs: SortSpec[], fields: Field[], refs?: Refs, depth = 0, parent = ""): GroupNode[] {
  const spec = specs[depth];
  const field = spec && fields.find((f) => f.id === spec.field);
  if (!field) return [];
  let groups = groupRecords(records, field, refs, spec.dateBy);
  if (spec.dir === "desc") groups = [...groups.filter((g) => !g.empty).reverse(), ...groups.filter((g) => g.empty)];
  return groups.map((g) => {
    const path = `${parent}/${g.key}`;
    const next = specs.slice(depth + 1).some((s) => fields.some((f) => f.id === s.field));
    return { ...g, path, depth, field, spec, children: next ? buildGroups(g.records, specs, fields, refs, depth + 1, path) : null };
  });
}

// ------------------------------------------------------------ tasks & events tables: fields per record type

const TASK_STATUSES = new Set(["To Do", "In Progress", "Completed", "Cancelled"]);
const EVENT_STATUSES = new Set(["Tentative", "Confirmed", "Cancelled", "Attended", "Missed"]);
const TASK_ONLY_KEYS = new Set(["started", "completed"]);

/** "Task" or "Event" for a record of a tasks & events table; null elsewhere. */
export function recordKind(fields: Field[], rec: Rec): "Task" | "Event" | null {
  const f = fields.find((x) => x.system_key === "component");
  const v = f ? rec.data[f.id] : null;
  return v === "Task" || v === "Event" ? v : null;
}

/** Whether a field means anything for this kind of record (an event has no Started / Completed At). */
export const fieldApplies = (f: Field, kind: "Task" | "Event" | null) => !(kind === "Event" && f.system_key && TASK_ONLY_KEYS.has(f.system_key));

/** The field as it applies to this kind of record: Status offers only that kind's statuses (plus the current value). */
export function fitField(f: Field, kind: "Task" | "Event" | null, current?: unknown): Field {
  if (!kind || f.system_key !== "status" || !f.options.choices) return f;
  const allowed = kind === "Task" ? TASK_STATUSES : EVENT_STATUSES;
  return { ...f, options: { ...f.options, choices: f.options.choices.filter((c) => allowed.has(c.name) || c.name === current) } };
}

// ------------------------------------------------------------ first day of the week (a user setting)

let weekStartDay = 1;  // 0 = Sunday, 1 = Monday … 6 = Saturday
export const setWeekStart = (d: number | undefined) => { weekStartDay = d !== undefined && d >= 0 && d <= 6 ? d : 1; };
export const weekStartsOn = () => weekStartDay;
/** How many days a date is into its week (0 = the first day of the week). */
export const dayOfWeek = (d: Date) => (d.getDay() - weekStartDay + 7) % 7;
/** Short weekday names in week order. */
export const weekdayNames = () => ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].slice(weekStartDay)
  .concat(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].slice(0, weekStartDay));

/** Fields in a view's own column order (dragged in the table view; the title field too); new fields go last. */
export function orderFields(fields: Field[], order?: string[]): Field[] {
  if (!order?.length) return fields;
  const pos = new Map(order.map((id, i) => [id, i]));
  return [...fields].sort((a, b) => (pos.get(a.id) ?? Infinity) - (pos.get(b.id) ?? Infinity) || fields.indexOf(a) - fields.indexOf(b));
}

/** Text copied from a spreadsheet (tab-separated; cells with tabs, line breaks or quotes come "quoted") -> rows of cells. */
export function parseTsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], cell = "", i = 0, quoted = false;
  const s = text.replace(/\r\n?/g, "\n");
  while (i < s.length) {
    const c = s[i];
    if (quoted) {
      if (c === '"' && s[i + 1] === '"') { cell += '"'; i += 2; continue; }
      if (c === '"') { quoted = false; i++; continue; }
      cell += c; i++; continue;
    }
    if (c === '"' && cell === "") { quoted = true; i++; continue; }
    if (c === "\t") { row.push(cell); cell = ""; i++; continue; }
    if (c === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; i++; continue; }
    cell += c; i++;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

/** Fields as one view shows them: with the names that view gives them (the field's own name in `realName`). */
export function withDisplayNames(fields: Field[], config: ViewConfig): Field[] {
  const names = config.fieldNames;
  if (!names || !Object.keys(names).length) return fields;
  return fields.map((f) => (names[f.id]?.trim() ? { ...f, name: names[f.id].trim(), realName: f.name } : f));
}

/** What a table really is: a linked table is treated like its source (files, contacts, tasks & events…). */
/** What a link allows, every permission spelled out (upload_files / edit_files follow add / edit unless set). */
export function linkCan(p: SharePerms | LinkPerms | null | undefined) {
  const add = !!p?.add, edit = !!p?.edit;
  return { add, edit, delete: !!p?.delete, upload_files: p?.upload_files ?? add, edit_files: p?.edit_files ?? edit };
}

/** "can add, edit, upload files" / "view only" */
export function linkSummary(p: SharePerms | LinkPerms | null | undefined, files = true): string {
  const c = linkCan(p);
  const words = [c.add && "add", c.edit && "edit", c.delete && "delete",
    files && c.upload_files && "upload files", files && c.edit_files && "edit files"].filter(Boolean);
  return words.length ? `can ${words.join(", ")}` : "view only";
}

export function nativeKind(t?: TableInfo | null): TableKind | "combo" | undefined {
  if (!t) return undefined;
  return t.kind === "remote" ? t.remote?.source_kind || "remote" : t.kind;
}

// ------------------------------------------------------------ email / URL / phone: the same rules as the server
// (backend/app/fieldformats.py), for feedback while typing

const EMAIL_RE = /^[^@\s<>"',;]+@[^@\s<>"',;]+\.[^@\s<>"',;.]{2,}$/;
const DOMAINISH_RE = /^[\w-]+(\.[\w-]+)+(:\d+)?([/?#].*)?$/;

/** A typed value as it will be stored, or why it can't be. */
export function cleanFormatted(type: FieldType, input: string): { value: string | null } | { error: string } {
  const text = input.split(/\s+/).filter(Boolean).join(" ");
  if (!text) return { value: null };
  if (type === "email") {
    const t = text.replace(/^mailto:/i, "");
    if (!EMAIL_RE.test(t)) return { error: "That isn't a valid email address" };
    const at = t.lastIndexOf("@");
    return { value: t.slice(0, at) + "@" + t.slice(at + 1).toLowerCase() };
  }
  if (type === "url") {
    if (text.includes(" ")) return { error: "A web address can't have spaces" };
    const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(text);
    let t = text;
    if (!scheme || (!/^https?$/i.test(scheme[1]) && DOMAINISH_RE.test(text))) {
      if (!DOMAINISH_RE.test(text)) return { error: "That isn't a web address" };
      t = "https://" + text;
    } else if (!/^https?$/i.test(scheme[1])) return { error: "A web address starts with http:// or https://" };
    if (!/^https?:\/\/[^/\s]+/i.test(t)) return { error: "That isn't a web address" };
    return { value: t };
  }
  if (type === "phone") {
    const p = phoneParts(text);
    if (!p) return { error: "That isn't a phone number" };
    return { value: (p.plus ? "+" : "") + p.digits + (p.ext ? ` x${p.ext}` : "") };
  }
  return { value: text };
}

export const FORMAT_TYPES = new Set<FieldType>(["email", "url", "phone"]);

/** tel: link for a phone number (digits and a leading +; an extension as ;ext=). */
export function telHref(phone: string) {
  const p = phoneParts(phone);
  if (!p) return `tel:${phone.replace(/[^\d+]/g, "")}`;
  return `tel:${p.plus ? "+" : ""}${p.digits}${p.ext ? `;ext=${p.ext}` : ""}`;
}

/* Phone numbers: the same rules as backend/app/fieldformats.py (stored as +15551234567 x12; shown as formatPhone). */
const KEYPAD: Record<string, string> = Object.fromEntries("ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").map((c, i) => [c, "22233344455566677778889999"[i]]));
const CC1 = new Set(["1", "7"]);
const CC2 = new Set("20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 60 61 62 63 64 65 66 81 82 84 86 90 91 92 93 94 95 98".split(" "));

export function phoneParts(text: string): { plus: boolean; digits: string; ext: string | null } | null {
  let t = String(text).replace(/^\s*tel:/i, "").trim();
  let ext: string | null = null;
  const m = /(?:\b(?:ext|extension)\b\.?|;ext=|(?:(?<=\d)|\b)x|#)\s*(\d{1,6})\s*$/i.exec(t);
  if (m && /\d/.test(t.slice(0, m.index))) { ext = m[1]; t = t.slice(0, m.index); }
  t = t.replace(/[A-Za-z]+\s*:/g, " ");
  if (/\d/.test(t)) t = t.replace(/(?<=[\d\s\-.])[A-Z]{3,7}\b/g, (w) => w.split("").map((c) => KEYPAD[c]).join(""));
  let plus = /^[^\d]*\+/.test(t);
  let digits = t.replace(/\D/g, "");
  if (digits.length < 3 || digits.length > 20) return null;
  if (!plus && digits.length === 11 && digits[0] === "1") plus = true;
  else if (!plus && digits.startsWith("00") && digits.length >= 10) { plus = true; digits = digits.slice(2); }
  else if (!plus && digits.startsWith("011") && digits.length >= 13) { plus = true; digits = digits.slice(3); }
  return { plus, digits, ext };
}

function phoneGroups(n: string) {
  const out = n.length > 4 ? [n.slice(-4)] : [n];
  let rest = n.length > 4 ? n.slice(0, -4) : "";
  while (rest) { out.unshift(rest.slice(-3)); rest = rest.slice(0, -3); }
  if (out.length > 1 && out[0].length === 1) { const first = out.shift()!; out[0] = first + out[0]; }
  return out.join(" ");
}

/** A phone number as it's shown everywhere: (555) 123-4567, +1 (555) 123-4567, +44 207 946 0958, 123-4567. */
export function formatPhone(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  const p = phoneParts(String(value));
  if (!p) return String(value);
  const d = p.digits;
  let s: string;
  if (p.plus && d.length === 11 && d[0] === "1") s = `+1 (${d.slice(1, 4)}) ${d.slice(4, 7)}-${d.slice(7)}`;
  else if (p.plus) {
    const cc = CC1.has(d.slice(0, 1)) ? d.slice(0, 1) : CC2.has(d.slice(0, 2)) ? d.slice(0, 2) : d.slice(0, 3);
    s = `+${cc} ${phoneGroups(d.slice(cc.length))}`.trim();
  } else if (d.length === 10) s = `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
  else if (d.length === 7) s = `${d.slice(0, 3)}-${d.slice(3)}`;
  else s = d;
  return s + (p.ext ? ` ext. ${p.ext}` : "");
}

/** A search typed as a phone number ("555-1234") matches the numbers with those digits. */
const phoneQuery = (q: string) => (/^[\d\s().+\-]+$/.test(q) && q.replace(/\D/g, "").length >= 3 ? q.replace(/\D/g, "") : null);

/** A web address as shown: without https:// and a trailing slash. */
export const shortUrl = (url: string) => url.replace(/^https?:\/\//i, "").replace(/\/$/, "");


/** Refs merged: newer titles, names and shown values over older ones. */
export function mergeRefs(a: Refs, b: Partial<Refs> | undefined): Refs {
  if (!b) return a;
  const display = { ...(a.display ?? {}) };
  for (const [fid, d] of Object.entries(b.display ?? {})) {
    display[fid] = { field: d.field, values: { ...(display[fid]?.values ?? {}), ...d.values } };
  }
  return { records: { ...a.records, ...b.records }, users: { ...a.users, ...b.users }, display };
}

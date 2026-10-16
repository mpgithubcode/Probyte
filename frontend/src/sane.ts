import type { Field, Rec } from "./types";

/**
 * Values as each field type expects them, whatever is stored: a table written to by something that skipped the
 * checks (an old version, a script writing to the database directly) still opens, rather than a page failing on a
 * select option that's an object or a text that's a list. Cheap; changes nothing that's already right.
 */
const isScalar = (v: unknown) => typeof v === "string" || typeof v === "number";
const asName = (v: unknown): string | null => {
  if (v == null || v === "") return null;
  if (isScalar(v)) return String(v);
  if (typeof v === "object" && !Array.isArray(v) && isScalar((v as { name?: unknown }).name)) return String((v as { name: unknown }).name);
  return JSON.stringify(v);
};

export function saneFields(fields: Field[]): Field[] {
  return fields.map((f) => {
    const ch = f.options?.choices as unknown;
    if (ch === undefined) return f.options ? f : { ...f, options: {} };
    const list = Array.isArray(ch) ? ch : [];
    if (list.every((c) => c && typeof c === "object" && typeof c.name === "string" && typeof c.color === "string") && Array.isArray(ch)) return f;
    const choices = list.map((c) => ({ name: asName(c) ?? "", color: (c && typeof c === "object" && typeof c.color === "string") ? c.color : "#94a3b8" }))
      .filter((c) => c.name);
    return { ...f, options: { ...f.options, choices } };
  });
}

function saneValue(type: string, v: unknown): unknown {
  if (v == null) return v;
  switch (type) {
    case "text": case "long_text": case "url": case "email": case "phone":
      return typeof v === "string" ? v : Array.isArray(v) ? v.map(asName).filter(Boolean).join(", ") : asName(v);
    case "single_select":
      return typeof v === "string" ? v : Array.isArray(v) ? asName(v[0]) : asName(v);
    case "multi_select":
      return Array.isArray(v) ? (v.every((x) => typeof x === "string") ? v : v.map(asName).filter(Boolean)) : [asName(v)].filter(Boolean);
    case "number": case "rating":
      return typeof v === "number" ? v : Number.isFinite(Number(v)) && v !== "" ? Number(v) : null;
    case "checkbox":
      return typeof v === "boolean" ? v : !!v;
    case "date":
      return typeof v === "string" ? v : null;
    case "link": case "user": case "attachment":
      return Array.isArray(v) ? v : [];
    default:
      return v;
  }
}

/** Records with each stored value in the shape its field's type expects (a new object only where something was off). */
export function saneRecords(fields: Field[], records: Rec[]): Rec[] {
  const typed = fields.filter((f) => f.id !== "__source");
  let changed = false;
  const out = records.map((r) => {
    if (!r.data || typeof r.data !== "object") { changed = true; return { ...r, data: {} }; }
    let data: Rec["data"] | null = null;
    for (const f of typed) {
      const v = r.data[f.id];
      if (v == null) continue;
      const s = saneValue(f.type, v);
      if (s !== v) { data ??= { ...r.data }; data[f.id] = s; }
    }
    if (!data) return r;
    changed = true;
    return { ...r, data };
  });
  return changed ? out : records;
}

import type { Dataset, Field, Rec } from "./types";
import { displayText, isDateField, keyOf } from "./util";

/** What a button field does for a record: open a URL, or apply a patch of field values. */
export function buttonAction(ds: Dataset, rec: Rec, field: Field): { url?: string; patch?: Record<string, unknown>; error?: string } {
  const o = field.options;
  if (o.action === "url") {
    const url = (o.url ?? "").replace(/\{([^}]+)\}/g, (_, name: string) => {
      const f = ds.fields.find((x) => x.name === name.trim());
      return f ? encodeURIComponent(displayText(f, rec.data[f.id], ds.refs)) : "";
    });
    return /^(https?:|mailto:)/i.test(url) ? { url } : { error: "The button's URL must start with http://, https:// or mailto:" };
  }
  const patch: Record<string, unknown> = {};
  for (const u of o.updates ?? []) {
    const f = ds.fields.find((x) => x.id === u.fieldId);
    if (!f) continue;
    const raw = typeof u.value === "string" ? u.value.trim() : u.value;
    let v: unknown = raw === "" ? null : raw;
    if (isDateField(f) && typeof raw === "string") {
      if (raw.toLowerCase() === "now") v = f.options.includeTime ? new Date().toISOString().replace(/\.\d{3}Z$/, "Z") : keyOf(new Date());
      else if (raw.toLowerCase() === "today") v = keyOf(new Date());
    } else if (f.type === "checkbox") v = raw === true || String(raw).toLowerCase() === "true";
    else if (f.type === "number" || f.type === "rating") v = raw === "" || raw === null ? null : Number(raw);
    else if (f.type === "multi_select" && typeof raw === "string") v = raw ? raw.split(",").map((x) => x.trim()).filter(Boolean) : [];
    patch[f.id] = v;
  }
  return { patch };
}

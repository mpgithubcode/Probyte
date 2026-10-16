/** The ways each kind of field can be shown: in the table (its cells, and the cards and lists of board, gallery and list
 * views) and on its records (the record panel and pages). The first of each list is the default; a field chooses its
 * own (field settings → How it's shown), and a page's block can choose again. Keep in step with VIEWS in
 * backend/app/data/pages.py. */
import type { Field, FieldView } from "./types";
import { valueType } from "./util";

export type Where = "cell" | "record";

export const VIEW_LABEL: Record<FieldView, string> = {
  chips: "Chips", count: "Count", text: "Plain text", list: "List", table: "Table", cards: "Cards", hcards: "Cards in a row",
  value: "Value", bar: "Progress bar", stat: "Big number", stars: "Stars", number: "Number", check: "Tick", yesno: "Yes / No",
  relative: "Relative (in 3 days)", thumbs: "Thumbnails", gallery: "Gallery", names: "File names", large: "Large text",
};

/** The ways a field can be shown there (none or one: nothing to choose). */
export function displaysFor(f: Field, where: Where): FieldView[] {
  const rec = where === "record";
  const vt = valueType(f);
  switch (f.type) {
    case "link": return rec ? ["chips", "list", "table", "cards", "hcards", "count"] : ["chips", "text", "count"];
    case "lookup": case "conditional_lookup": return rec ? ["chips", "list", "hcards", "text", "count"] : ["chips", "text", "count"];
    case "multi_select": return rec ? ["chips", "list", "text", "count"] : ["chips", "text", "count"];
    case "user": case "created_by": case "last_modified_by": return rec ? ["chips", "list", "text"] : ["chips", "text"];
    case "attachment": return rec ? ["thumbs", "gallery", "names"] : ["thumbs", "names", "count"];
    case "rating": return ["stars", "number"];
    case "checkbox": return ["check", "yesno"];
    case "single_select": return ["chips", "text"];
    case "text": case "url": case "email": case "phone": return rec ? ["value", "large"] : [];
  }
  if (vt === "number") return rec ? ["value", "bar", "stat"] : ["value", "bar"];
  if (vt === "date") return ["value", "relative"];
  if (vt === "boolean") return ["check", "yesno"];
  return [];
}

/** How the field is shown there: a page block's own choice, else the field's, else the default. */
export function displayOf(f: Field, where: Where, own?: FieldView | null): FieldView | null {
  const ok = displaysFor(f, where);
  const v = own ?? f.options.views?.[where];
  return v && ok.includes(v) ? v : ok[0] ?? null;
}

/** Is it the field's ordinary look there (editable in place as usual), or one of its other ways? */
export const isDefaultDisplay = (f: Field, where: Where, v: FieldView | null) => !v || v === displaysFor(f, where)[0];

/** "in 3 days", "yesterday", "2 months ago". */
export function relativeDate(iso: string): string {
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(+d)) return iso;
  const dayStart = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((dayStart(d) - dayStart(new Date())) / 86400000);
  if (iso.length > 10 && days === 0) {
    const mins = Math.round((+d - Date.now()) / 60000);
    if (Math.abs(mins) < 1) return "now";
    if (Math.abs(mins) < 60) return mins > 0 ? `in ${mins} min` : `${-mins} min ago`;
    const h = Math.round(mins / 60);
    return h > 0 ? `in ${h} h` : `${-h} h ago`;
  }
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  const abs = Math.abs(days);
  const [n, unit] = abs < 14 ? [abs, "day"] : abs < 60 ? [Math.round(abs / 7), "week"] : abs < 730 ? [Math.round(abs / 30.4), "month"] : [Math.round(abs / 365), "year"];
  const s = `${n} ${unit}${n === 1 ? "" : "s"}`;
  return days > 0 ? `in ${s}` : `${s} ago`;
}

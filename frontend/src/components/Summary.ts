import type { Field, Rec } from "../types";
import { formatDate, formatNumber, valueType } from "../util";

/** Table view footer: what each column can sum up, and the result over the view's records. */
export interface SummaryKind { id: string; label: string }

const blank = (v: unknown) => v === null || v === undefined || v === "" || v === false || (Array.isArray(v) && !v.length);

export function summaryKinds(f: Field): SummaryKind[] {
  const common = [{ id: "filled", label: "Filled" }, { id: "empty", label: "Empty" }, { id: "unique", label: "Unique" },
    { id: "percent_filled", label: "% filled" }];
  if (f.type === "checkbox") return [{ id: "checked", label: "Checked" }, { id: "unchecked", label: "Unchecked" }, { id: "percent_checked", label: "% checked" }];
  const vt = valueType(f);
  if (vt === "number") return [{ id: "sum", label: "Sum" }, { id: "avg", label: "Average" }, { id: "min", label: "Min" }, { id: "max", label: "Max" }, ...common];
  if (vt === "date") return [{ id: "earliest", label: "Earliest" }, { id: "latest", label: "Latest" }, ...common];
  return common;
}

const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 });

export function summarize(f: Field, kind: string, records: Rec[]): string {
  const vals = records.map((r) => r.data[f.id]);
  const filled = vals.filter((v) => !blank(v));
  const nums = filled.map((v) => Number(v)).filter((n) => Number.isFinite(n));
  // sums keep the field's format (currency, percent…) and its decimal places, or at most 2
  const num = (n: number) => f.type === "rating" ? fmt(n)
    : formatNumber(n, { ...f.options, precision: f.options.precision ?? (Number.isInteger(n) ? 0 : 2) });
  const pct = (n: number) => (records.length ? `${Math.round((n / records.length) * 100)}%` : "–");
  switch (kind) {
    case "sum": return num(nums.reduce((a, b) => a + b, 0));
    case "avg": return nums.length ? num(nums.reduce((a, b) => a + b, 0) / nums.length) : "–";
    case "min": return nums.length ? num(Math.min(...nums)) : "–";
    case "max": return nums.length ? num(Math.max(...nums)) : "–";
    case "earliest": case "latest": {
      const ds = filled.map(String).sort();
      const v = kind === "earliest" ? ds[0] : ds[ds.length - 1];
      return v ? formatDate(v, v.length > 10, f.options) : "–";
    }
    case "filled": return fmt(filled.length);
    case "empty": return fmt(records.length - filled.length);
    case "unique": return fmt(new Set(filled.map((v) => JSON.stringify(v))).size);
    case "percent_filled": return pct(filled.length);
    case "checked": return fmt(vals.filter(Boolean).length);
    case "unchecked": return fmt(vals.filter((v) => !v).length);
    case "percent_checked": return pct(vals.filter(Boolean).length);
    default: return "";
  }
}

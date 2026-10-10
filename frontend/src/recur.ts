/**
 * Repeat values: an iCalendar RRULE plus skipped dates, as stored by the server:
 *   "FREQ=WEEKLY;BYDAY=MO,WE"  or  "FREQ=WEEKLY;BYDAY=MO\nEXDATE:2026-10-12T15:00:00Z,2026-10-19T15:00:00Z"
 * Occurrences are expanded here for the calendar and timeline.
 */
import type { Field, Rec } from "./types";
import { isDateOnly, keyOf, parseKey } from "./util";

export type Freq = "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY";
const FREQS: Freq[] = ["DAILY", "WEEKLY", "MONTHLY", "YEARLY"];
export const WD_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
export const WD_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WD_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export interface Rule {
  freq: Freq;
  interval: number;
  byday: { n: number; wd: number }[];   // wd: 0 = Monday; n: 0 = every, 2 = 2nd, -1 = last
  bymonthday: number[];
  bymonth: number[];                     // 1-12
  bysetpos: number[];
  count?: number;
  until?: string;                        // raw UNTIL (YYYYMMDD or YYYYMMDDTHHMMSSZ)
  extra: string[];                       // parts we don't interpret (kept when rewriting)
}

// ------------------------------------------------------------------ value <-> parts

/** One occurrence changed on its own (an iCalendar RECURRENCE-ID exception). `set` is keyed by system key or field id. */
export interface Override { rid: string; set: Record<string, any>; cancelled?: boolean }

export function parseValue(v: unknown): { rule: string | null; exdates: string[]; overrides: Override[] } {
  if (!v || typeof v !== "string") return { rule: null, exdates: [], overrides: [] };
  let rule: string | null = null;
  const exdates: string[] = [];
  const overrides: Override[] = [];
  for (const raw of v.replace(/\r/g, "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (/^EXDATE:/i.test(line)) exdates.push(...line.slice(7).split(",").map((x) => x.trim()).filter(Boolean));
    else if (/^OVERRIDE:/i.test(line)) {
      try { const o = JSON.parse(line.slice(9)); if (o?.rid) overrides.push({ rid: o.rid, set: o.set ?? {}, ...(o.cancelled ? { cancelled: true } : {}) }); } catch { /* skip */ }
    } else if (rule === null) rule = line.replace(/^RRULE:/i, "");
  }
  return { rule, exdates, overrides };
}

export function composeValue(rule: string | null, exdates: string[] = [], overrides: Override[] = []): string | null {
  if (!rule) return null;
  const ex = [...new Set(exdates)].sort();
  const lines = [rule, ...(ex.length ? [`EXDATE:${ex.join(",")}`] : [])];
  const byRid = new Map(overrides.map((o) => [ridKey(o.rid), o]));
  for (const o of [...byRid.values()].sort((a, b) => a.rid.localeCompare(b.rid))) {
    const sorted = Object.fromEntries(Object.entries(o.set).sort(([a], [b]) => a.localeCompare(b)));
    lines.push(`OVERRIDE:${JSON.stringify(o.cancelled ? { cancelled: true, rid: o.rid, set: sorted } : { rid: o.rid, set: sorted })}`);
  }
  return lines.join("\n");
}

/** Occurrence ids in a comparable form (same instant = same key). */
export const ridKey = (v: string) => (isDateOnly(v) ? v : String(+new Date(v)));

export function parseRule(s: string | null): Rule | null {
  if (!s) return null;
  const r: Rule = { freq: "DAILY", interval: 1, byday: [], bymonthday: [], bymonth: [], bysetpos: [], extra: [] };
  let freq: Freq | null = null;
  for (const part of s.toUpperCase().split(";")) {
    const [k, v = ""] = part.split("=");
    if (!k) continue;
    const nums = () => v.split(",").map(Number).filter((n) => Number.isFinite(n) && n !== 0);
    switch (k) {
      case "FREQ": freq = FREQS.includes(v as Freq) ? (v as Freq) : null; break;
      case "INTERVAL": r.interval = Math.max(1, parseInt(v) || 1); break;
      case "COUNT": r.count = Math.max(1, parseInt(v) || 1); break;
      case "UNTIL": r.until = v; break;
      case "BYMONTHDAY": r.bymonthday = nums(); break;
      case "BYMONTH": r.bymonth = nums(); break;
      case "BYSETPOS": r.bysetpos = nums(); break;
      case "BYDAY":
        r.byday = v.split(",").flatMap((d) => {
          const m = /^([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)$/.exec(d);
          return m ? [{ n: m[1] ? parseInt(m[1]) : 0, wd: WD_CODES.indexOf(m[2]) }] : [];
        });
        break;
      default: r.extra.push(part);
    }
  }
  if (!freq) return null;
  r.freq = freq;
  return r;
}

export function ruleToString(r: Rule): string {
  const parts = [`FREQ=${r.freq}`];
  if (r.interval > 1) parts.push(`INTERVAL=${r.interval}`);
  if (r.byday.length) parts.push(`BYDAY=${r.byday.map((d) => `${d.n || ""}${WD_CODES[d.wd]}`).join(",")}`);
  if (r.bymonthday.length) parts.push(`BYMONTHDAY=${r.bymonthday.join(",")}`);
  if (r.bymonth.length) parts.push(`BYMONTH=${r.bymonth.join(",")}`);
  if (r.bysetpos.length) parts.push(`BYSETPOS=${r.bysetpos.join(",")}`);
  if (r.count) parts.push(`COUNT=${r.count}`);
  else if (r.until) parts.push(`UNTIL=${r.until}`);
  return [...parts, ...r.extra].join(";");
}

/** UNTIL as the last moment it allows (a date-only UNTIL covers that whole day). */
function untilTime(u?: string): number {
  if (!u) return Infinity;
  const m = /^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?$/.exec(u);
  if (!m) return Infinity;
  const [, y, mo, d, h, mi, s, z] = m;
  if (h === undefined) return +new Date(+y, +mo - 1, +d, 23, 59, 59);
  return z ? Date.UTC(+y, +mo - 1, +d, +h, +mi, +s) : +new Date(+y, +mo - 1, +d, +h, +mi, +s);
}

export const untilKey = (u?: string) => (u && /^\d{8}/.test(u) ? keyOf(new Date(untilTime(u))) : "");
export const untilFromKey = (k: string) => k.replace(/-/g, "");

// ------------------------------------------------------------------ wording

const ordinal = (n: number) => (n === -1 ? "last" : n === -2 ? "second to last" : ["", "1st", "2nd", "3rd", "4th", "5th"][n] ?? `${n}th`);

export function describe(v: unknown): string {
  const { rule, exdates } = parseValue(v);
  const r = parseRule(rule);
  if (!r) return rule ? "Custom repeat" : "";
  const unit = { DAILY: "day", WEEKLY: "week", MONTHLY: "month", YEARLY: "year" }[r.freq];
  let s = r.interval > 1 ? `Every ${r.interval} ${unit}s` : { DAILY: "Daily", WEEKLY: "Weekly", MONTHLY: "Monthly", YEARLY: "Yearly" }[r.freq];
  const days = r.byday.filter((d) => !d.n).map((d) => d.wd).sort();
  const weekdays = days.length === 5 && days.every((d, i) => d === i);
  if (r.freq === "WEEKLY" || r.freq === "DAILY") {
    if (weekdays) s = r.interval > 1 ? `${s} on weekdays` : "Every weekday";
    else if (days.length) s += ` on ${days.map((d) => WD_SHORT[d]).join(", ")}`;
  } else if (r.bysetpos.length === 1 && days.length) {
    s += ` on the ${ordinal(r.bysetpos[0])} ${weekdays ? "weekday" : days.map((d) => WD_LONG[d]).join("/")}`;
  } else if (r.byday.some((d) => d.n)) {
    s += ` on the ${r.byday.map((d) => `${ordinal(d.n)} ${WD_LONG[d.wd]}`).join(", ")}`;
  } else if (r.bymonthday.length) {
    s += ` on day ${r.bymonthday.map((d) => (d === -1 ? "last" : d)).join(", ")}`;
  }
  if (r.count) s += ` · ${r.count} times`;
  else if (r.until) s += ` · until ${new Date(untilTime(r.until)).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
  const { overrides } = parseValue(v);
  const cancelled = overrides.filter((o) => o.cancelled).length;
  if (exdates.length + cancelled) s += ` · ${exdates.length + cancelled} skipped`;
  if (overrides.length - cancelled) s += ` · ${overrides.length - cancelled} changed`;
  return s;
}

// ------------------------------------------------------------------ expansion

const DAY = 86400000;
const daysInMonth = (y: number, m: number) => new Date(y, m + 1, 0).getDate();

function monthDays(r: Rule, y: number, m: number, anchorDay: number): Date[] {
  const dim = daysInMonth(y, m);
  let out: number[] = [];
  if (r.byday.length) {
    for (const { n, wd } of r.byday) {
      const all: number[] = [];
      for (let d = 1; d <= dim; d++) if ((new Date(y, m, d).getDay() + 6) % 7 === wd) all.push(d);
      if (!n) out.push(...all);
      else { const pick = n > 0 ? all[n - 1] : all[all.length + n]; if (pick) out.push(pick); }
    }
    if (r.bymonthday.length) {
      const allowed = new Set(r.bymonthday.map((d) => (d < 0 ? dim + d + 1 : d)));
      out = out.filter((d) => allowed.has(d));
    }
  } else if (r.bymonthday.length) {
    out = r.bymonthday.map((d) => (d < 0 ? dim + d + 1 : d)).filter((d) => d >= 1 && d <= dim);
  } else if (anchorDay <= dim) {
    out = [anchorDay];  // e.g. the 31st: months without one are skipped (RFC 5545)
  }
  return out.map((d) => new Date(y, m, d));
}

function periodDates(r: Rule, k: number, a: Date): Date[] {
  const step = k * r.interval;
  let out: Date[];
  if (r.freq === "DAILY") {
    out = [new Date(a.getFullYear(), a.getMonth(), a.getDate() + step)];
  } else if (r.freq === "WEEKLY") {
    const monday = new Date(a.getFullYear(), a.getMonth(), a.getDate() - ((a.getDay() + 6) % 7) + step * 7);
    const wds = r.byday.length ? [...new Set(r.byday.map((d) => d.wd))] : [(a.getDay() + 6) % 7];
    out = wds.map((wd) => new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + wd));
  } else if (r.freq === "MONTHLY") {
    const first = new Date(a.getFullYear(), a.getMonth() + step, 1);
    out = monthDays(r, first.getFullYear(), first.getMonth(), a.getDate());
  } else {
    const y = a.getFullYear() + step;
    const months = r.bymonth.length ? r.bymonth.map((m) => m - 1) : [a.getMonth()];
    out = months.flatMap((m) => monthDays(r, y, m, a.getDate()));
  }
  // BYxxx parts that only narrow the set for this frequency
  if (r.bymonth.length && r.freq !== "YEARLY") out = out.filter((d) => r.bymonth.includes(d.getMonth() + 1));
  if (r.freq === "DAILY" && r.byday.length) out = out.filter((d) => r.byday.some((b) => b.wd === (d.getDay() + 6) % 7));
  if (r.freq === "DAILY" && r.bymonthday.length) {
    out = out.filter((d) => r.bymonthday.some((x) => (x < 0 ? daysInMonth(d.getFullYear(), d.getMonth()) + x + 1 : x) === d.getDate()));
  }
  out.sort((x, y) => +x - +y);
  if (r.bysetpos.length) out = r.bysetpos.map((p) => (p > 0 ? out[p - 1] : out[out.length + p])).filter(Boolean);
  return out;
}

/** Start values of a series between two day keys (inclusive), skipping EXDATEs. The anchor is the record's own start. */
export function occurrences(value: unknown, anchor: string, fromKey: string, toKey: string, limit = 1000): string[] {
  const { rule, exdates } = parseValue(value);
  const r = parseRule(rule);
  const dateOnly = isDateOnly(anchor);
  const a = dateOnly ? parseKey(anchor) : new Date(anchor);
  if (isNaN(+a)) return [];
  const fmt = (d: Date) => (dateOnly ? keyOf(d) : d.toISOString().replace(/\.\d{3}Z$/, "Z"));
  const fromT = +parseKey(fromKey), toT = +parseKey(toKey) + DAY - 1;
  const skip = new Set(exdates.map((x) => (isDateOnly(x) ? x : String(+new Date(x)))));
  const skipped = (d: Date) => skip.has(dateOnly ? keyOf(d) : String(+d));
  const out: string[] = [];
  const emit = (d: Date) => { if (+d >= fromT && +d <= toT && !skipped(d)) out.push(fmt(d)); };
  if (!r) { emit(a); return out; }
  const until = untilTime(r.until);
  let n = 1;  // the anchor is the first occurrence
  emit(a);
  for (let k = 0; k < 50000 && out.length < limit; k++) {
    const ds = periodDates(r, k, a);
    if (!ds.length && k > 0) {
      // nothing in this period (e.g. 31st in a short month): keep going unless we are clearly past the range
      if (+new Date(a.getFullYear(), a.getMonth(), a.getDate() + k * r.interval) > toT && r.freq === "DAILY") break;
      continue;
    }
    let past = false;
    for (const d0 of ds) {
      const d = dateOnly ? d0 : new Date(d0.getFullYear(), d0.getMonth(), d0.getDate(), a.getHours(), a.getMinutes(), a.getSeconds());
      if (+d <= +a) continue;
      if (+d > until || (r.count && n >= r.count) || +d > toT) { past = true; break; }
      n++;
      emit(d);
    }
    if (past) break;
  }
  return out;
}

/** Records with their repeats laid out between two day keys. Each copy carries `occurrence` (which one it is). */
export function expandRecords(records: Rec[], fields: Field[], startField: Field, endField: Field | undefined, fromKey: string, toKey: string): Rec[] {
  const rf = fields.find((f) => f.type === "recurrence");
  if (!rf) return records;
  const out: Rec[] = [];
  for (const rec of records) {
    const value = rec.data[rf.id];
    const start = rec.data[startField.id] ?? (endField ? rec.data[endField.id] : undefined);
    const parsed = parseValue(value);
    if (!parsed.rule || typeof start !== "string") { out.push(rec); continue; }
    const end = endField ? rec.data[endField.id] : undefined;
    const spanMs = typeof end === "string" ? +toDate(end) - +toDate(start) : 0;
    const ovs = new Map(parsed.overrides.map((o) => [ridKey(o.rid), o]));
    // look a month either side: a changed occurrence may have been moved into (or out of) the range
    const back = Math.max(0, Math.ceil(spanMs / DAY)) + 31;
    const from = keyOf(new Date(+parseKey(fromKey) - back * DAY));
    const to = keyOf(new Date(+parseKey(toKey) + 31 * DAY));
    for (const occ of occurrences(value, start, from, to)) {
      const ov = ovs.get(ridKey(occ));
      if (ov?.cancelled) continue;
      const data = { ...rec.data };
      if (rec.data[startField.id]) data[startField.id] = occ;
      if (endField && typeof end === "string") data[endField.id] = endFor(occ, end, spanMs);
      if (ov) applySet(fields, data, ov.set);
      const s0 = dayKeyOf(data[startField.id] ?? (endField ? data[endField.id] : undefined));
      const e0 = dayKeyOf(endField ? data[endField.id] : undefined) ?? s0;
      if (!s0 || s0 > toKey || (e0 ?? s0) < fromKey) continue;
      out.push({ ...rec, data, occurrence: occ, occurrenceOf: start, overridden: !!ov });
    }
  }
  return out;
}

const dayKeyOf = (v: unknown) => (typeof v === "string" ? (isDateOnly(v) ? v : keyOf(new Date(v))) : null);

/** The field an override key refers to: a system key, or a field id. */
export const overrideKey = (f: Field) => f.system_key ?? f.id;
function applySet(fields: Field[], data: Record<string, any>, set: Record<string, any>) {
  for (const [k, v] of Object.entries(set)) {
    const f = fields.find((x) => x.system_key === k) ?? fields.find((x) => x.id === k)
      ?? (k === "summary" ? fields.find((x) => x.is_primary) : undefined);
    if (f) data[f.id] = v;
  }
}

/** The values one occurrence of a record shows: the record's dates moved to that occurrence, plus its own changes. */
export function occurrenceData(fields: Field[], rec: Rec, occurrence: string): Record<string, any> {
  const rf = fields.find((f) => f.type === "recurrence");
  const anchor = anchorOf(fields, rec);
  const data = { ...rec.data };
  if (!rf || !anchor) return data;
  const shiftMs = +toDate(occurrence) - +toDate(anchor);
  const days = Math.round(shiftMs / DAY);
  for (const f of seriesDateFields(fields)) {
    const v = rec.data[f.id];
    if (typeof v !== "string") continue;
    if (isDateOnly(v)) { const d = parseKey(v); d.setDate(d.getDate() + days); data[f.id] = keyOf(d); }
    else data[f.id] = new Date(+new Date(v) + shiftMs).toISOString().replace(/\.\d{3}Z$/, "Z");
  }
  const ov = parseValue(rec.data[rf.id]).overrides.find((o) => ridKey(o.rid) === ridKey(occurrence));
  if (ov) applySet(fields, data, ov.set);
  return data;
}

/** Date fields that move with each occurrence: start / end / due, or every date field in tables without them. */
export function seriesDateFields(fields: Field[]): Field[] {
  const sys = fields.filter((f) => f.system_key && ["dtstart", "dtend", "due"].includes(f.system_key));
  return sys.length ? sys : fields.filter((f) => f.type === "date");
}

export function findOverride(value: unknown, occurrence: string): Override | undefined {
  return parseValue(value).overrides.find((o) => ridKey(o.rid) === ridKey(occurrence));
}

/** Change (patch) or reset (null) one occurrence. */
export function setOverride(value: unknown, occurrence: string, patch: Record<string, any> | null): string | null {
  const { rule, exdates, overrides } = parseValue(value);
  const rest = overrides.filter((o) => ridKey(o.rid) !== ridKey(occurrence));
  const cur = overrides.find((o) => ridKey(o.rid) === ridKey(occurrence));
  if (patch === null) return composeValue(rule, exdates, rest);
  return composeValue(rule, exdates, [...rest, { rid: occurrence, set: { ...(cur?.set ?? {}), ...patch } }]);
}

const toDate = (v: string) => (isDateOnly(v) ? parseKey(v) : new Date(v));
/** The end of an occurrence: same length as the original item. */
function endFor(occ: string, end: string, spanMs: number): string {
  if (isDateOnly(end)) {
    const d = toDate(occ);
    d.setDate(d.getDate() + Math.round(spanMs / DAY));
    return keyOf(d);
  }
  return new Date(+toDate(occ) + spanMs).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Skip one occurrence of a series. */
export function addExdate(value: unknown, occurrence: string): string | null {
  const { rule, exdates, overrides } = parseValue(value);
  return composeValue(rule, [...exdates, occurrence], overrides.filter((o) => ridKey(o.rid) !== ridKey(occurrence)));
}

// ------------------------------------------------------------------ simple editor model

export interface Simple {
  freq: Freq | "NONE";
  interval: number;
  days: number[];                 // weekly
  monthMode: "date" | "nth";      // monthly: same date, or the nth weekday
  nth: number; nthDay: number;
  end: "never" | "until" | "count";
  until: string;                  // day key
  count: number;
}

export function toSimple(value: unknown, anchor?: string): Simple | null {
  const a = anchor ? (isDateOnly(anchor) ? parseKey(anchor) : new Date(anchor)) : new Date();
  const wd = (a.getDay() + 6) % 7;
  const nthOf = Math.ceil(a.getDate() / 7);
  const base: Simple = { freq: "NONE", interval: 1, days: [wd], monthMode: "date", nth: nthOf > 4 ? -1 : nthOf, nthDay: wd,
    end: "never", until: "", count: 10 };
  const { rule } = parseValue(value);
  if (!rule) return base;
  const r = parseRule(rule);
  if (!r || r.extra.some((x) => !x.startsWith("WKST=")) || r.bymonth.length || r.bysetpos.length || r.bymonthday.length) return null;
  const s: Simple = { ...base, freq: r.freq, interval: r.interval,
    end: r.count ? "count" : r.until ? "until" : "never", count: r.count ?? 10, until: untilKey(r.until) };
  if (r.freq === "DAILY" && r.byday.length) return null;
  if (r.freq === "WEEKLY") {
    if (r.byday.some((d) => d.n)) return null;
    if (r.byday.length) s.days = r.byday.map((d) => d.wd).sort();
  } else if (r.freq === "MONTHLY") {
    if (r.byday.length > 1) return null;
    if (r.byday.length) { if (!r.byday[0].n) return null; s.monthMode = "nth"; s.nth = r.byday[0].n; s.nthDay = r.byday[0].wd; }
  } else if (r.byday.length) return null;
  return s;
}

export function fromSimple(s: Simple): string | null {
  if (s.freq === "NONE") return null;
  const r: Rule = { freq: s.freq, interval: Math.max(1, s.interval || 1), byday: [], bymonthday: [], bymonth: [], bysetpos: [], extra: [] };
  if (s.freq === "WEEKLY" && s.days.length) r.byday = [...s.days].sort().map((wd) => ({ n: 0, wd }));
  if (s.freq === "MONTHLY" && s.monthMode === "nth") r.byday = [{ n: s.nth, wd: s.nthDay }];
  if (s.end === "count") r.count = Math.max(1, s.count || 1);
  if (s.end === "until" && s.until) r.until = untilFromKey(s.until);
  return ruleToString(r);
}

export const ORDINALS = [{ n: 1, label: "1st" }, { n: 2, label: "2nd" }, { n: 3, label: "3rd" }, { n: 4, label: "4th" }, { n: -1, label: "last" }];
export { WD_LONG };

/** The date a record's repeats are counted from: its start, else its due/end date, else its first date. */
export function anchorOf(fields: Field[], rec: Rec): string | undefined {
  const pick = (key: string) => { const f = fields.find((x) => x.system_key === key); const v = f && rec.data[f.id]; return typeof v === "string" ? v : undefined; };
  return pick("dtstart") ?? pick("due") ?? pick("dtend")
    ?? fields.filter((f) => f.type === "date").map((f) => rec.data[f.id]).find((v): v is string => typeof v === "string");
}

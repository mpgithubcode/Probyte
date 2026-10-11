import { useEffect, useMemo, useState } from "react";
import type { Field, Rec } from "../types";
import { addDaysKey, dayKey, dayOfWeek, formatTime, isDateField, isEditable, keyOf, parseKey, recordTitle, shiftValue, weekdayNames, weekStartsOn } from "../util";
import { expandRecords, overrideKey, setOverride } from "../recur";
import { TimeGrid } from "./CalendarTimeGrid";
import { Icon } from "./Icon";
import { isTouch, useNarrow } from "./useNarrow";
import type { ViewProps } from "./viewTypes";

const occKey = (r: Rec) => `${r.id}:${r.occurrence ?? ""}`;
const MAX_SPAN_DAYS = 62;

export function CalendarView({ fields, records, view, ops, setConfig }: ViewProps) {
  const canEdit = (f: Field) => ops.can.edit && isEditable(f);
  const dateFields = fields.filter(isDateField);
  const startField = dateFields.find((f) => f.id === view.config.dateField);
  const endField = dateFields.find((f) => f.id === view.config.endField);
  const repeatField = fields.find((f) => f.type === "recurrence" && ops.can.edit && isEditable(f));
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const [dragId, setDragId] = useState<string | null>(null);
  const [overDay, setOverDay] = useState<string | null>(null);
  const today = keyOf(new Date());
  const narrow = useNarrow();
  const [picked, setPicked] = useState<string>(today);
  const mode = view.config.calMode ?? "month";
  const [focus, setFocus] = useState<string>(today);   // the day shown (day view) or inside the week shown (week view)
  const tdays = useMemo(() => {
    if (mode === "day") return [focus];
    const d = parseKey(focus);
    d.setDate(d.getDate() - dayOfWeek(d));
    return Array.from({ length: 7 }, (_, i) => addDaysKey(keyOf(d), i));
  }, [mode, focus, weekStartsOn()]);
  const tExpanded = useMemo(() => (startField && mode !== "month" ? expandRecords(records, fields, startField, endField, tdays[0], tdays[tdays.length - 1]) : []),
    [records, fields, startField, endField, tdays, mode]);
  // Keep the picked day inside the month being shown (today if it's this month).
  useEffect(() => {
    const mk = keyOf(month).slice(0, 7);
    setPicked((p) => (p.slice(0, 7) === mk ? p : today.slice(0, 7) === mk ? today : keyOf(month)));
  }, [month, today]);

  const days = useMemo(() => {
    const first = new Date(month);
    first.setDate(first.getDate() - dayOfWeek(first));  // the week starts on the day chosen in Settings
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(first); d.setDate(first.getDate() + i); return keyOf(d); });
  }, [month, weekStartsOn()]);

  const expanded = useMemo(() => (startField ? expandRecords(records, fields, startField, endField, days[0], days[days.length - 1]) : records),
    [records, fields, startField, endField, days]);
  const { byDay, undated } = useMemo(() => {
    const byDay = new Map<string, { rec: Rec; isStart: boolean }[]>();
    let undated = 0;
    if (!startField) return { byDay, undated };
    for (const r of expanded) {
      const s = dayKey(r.data[startField.id]) ?? (endField ? dayKey(r.data[endField.id]) : null);
      if (!s) { undated++; continue; }
      let e = endField ? dayKey(r.data[endField.id]) ?? s : s;
      if (e < s) e = s;
      let k = s;
      for (let i = 0; i < MAX_SPAN_DAYS && k <= e; i++, k = addDaysKey(k, 1)) {
        if (!byDay.has(k)) byDay.set(k, []);
        byDay.get(k)!.push({ rec: r, isStart: k === s });
      }
    }
    for (const list of byDay.values()) {
      list.sort((a, b) => String(a.rec.data[startField.id] ?? "").localeCompare(String(b.rec.data[startField.id] ?? "")));
    }
    return { byDay, undated };
  }, [expanded, startField, endField]);

  if (!startField) {
    return (
      <div className="empty-state">
        <p>Choose a date field to lay records out on the calendar.</p>
        <select className="input" style={{ width: 240 }} value="" onChange={(e) => setConfig({ dateField: e.target.value })}>
          <option value="" disabled>Date field…</option>
          {dateFields.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
        {!dateFields.length && <p className="muted">This database has no date fields yet.</p>}
      </div>
    );
  }

  const move = (rec: Rec, toDay: string) => {
    const startVal = rec.data[startField.id];
    const fromDay = dayKey(startVal) ?? (endField ? dayKey(rec.data[endField.id]) : null);
    if (!fromDay) return;
    const delta = Math.round((+parseKey(toDay) - +parseKey(fromDay)) / 86400000);
    if (!delta) return;
    const patch: Record<string, unknown> = {};
    if (canEdit(startField) && startVal) patch[startField.id] = shiftValue(startVal, delta);
    if (endField && canEdit(endField) && rec.data[endField.id]) patch[endField.id] = shiftValue(rec.data[endField.id], delta);
    applyPatch(rec, patch);
  };

  /** Write new date values; a repeat changes on its own (the rest of the series stays put). */
  const applyPatch = (rec: Rec, patch: Record<string, unknown>) => {
    if (!Object.keys(patch).length) return;
    const master = records.find((r) => r.id === rec.id);
    if (rec.occurrence && repeatField && master) {
      // a repeat moves on its own; the rest of the series stays put
      const set = Object.fromEntries(Object.entries(patch).map(([fid, v]) => [overrideKey(fields.find((f) => f.id === fid)!), v]));
      ops.update(master, { [repeatField.id]: setOverride(master.data[repeatField.id], rec.occurrence, set) });
    } else ops.update(rec, patch);
  };

  const shift = (n: number) => {
    if (mode === "month") return setMonth((m) => new Date(m.getFullYear(), m.getMonth() + n, 1));
    setFocus((f) => addDaysKey(f, n * (mode === "week" ? 7 : 1)));
  };
  const monthKey = keyOf(month).slice(0, 7);
  const goToday = () => { const d = new Date(); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); setFocus(today); };
  const setMode = (m: "month" | "week" | "day") => {
    // keep looking at roughly the same dates when switching
    if (m !== "month" && mode === "month") setFocus(monthKey === today.slice(0, 7) ? today : keyOf(month));
    if (m === "month" && mode !== "month") { const d = parseKey(focus); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); }
    setConfig({ calMode: m });
  };
  const title = mode === "month" ? month.toLocaleDateString(undefined, { month: "long", year: "numeric" })
    : mode === "day" ? parseKey(focus).toLocaleDateString(undefined, { weekday: narrow ? "short" : "long", month: "long", day: "numeric", year: "numeric" })
    : (() => {
      const a = parseKey(tdays[0]), b = parseKey(tdays[6]);
      const sameMonth = a.getMonth() === b.getMonth();
      return `${a.toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${b.toLocaleDateString(undefined, sameMonth ? { day: "numeric" } : { month: "short", day: "numeric" })}, ${b.getFullYear()}`;
    })();

  const head = (
    <div className="cal-head">
      <button className="icon-btn" onClick={() => shift(-1)} aria-label={`Previous ${mode}`}><Icon name="left" /></button>
      <button className="icon-btn" onClick={() => shift(1)} aria-label={`Next ${mode}`}><Icon name="right" /></button>
      <h2>{title}</h2>
      <button className="btn sm" onClick={goToday}>Today</button>
      <span className="spacer" />
      {undated > 0 && mode === "month" && !narrow && <span className="muted">{undated} without a date</span>}
      <div className="segmented sm">
        {(["day", "week", "month"] as const).map((m) => (
          <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>{m[0].toUpperCase() + m.slice(1)}</button>
        ))}
      </div>
    </div>
  );

  if (mode !== "month") {
    return (
      <div className="calendar time">
        {head}
        <TimeGrid days={tdays} records={tExpanded} fields={fields} startField={startField} endField={endField} ops={ops}
          color={(r) => ops.colorFor(r, view.config.colorField) ?? "#6366f1"}
          canEdit={canEdit(startField) && (!endField || canEdit(endField))} onTimes={applyPatch} />
      </div>
    );
  }

  return (
    <div className="calendar">
      {head}
      <div className={`cal-grid ${narrow ? "compact" : ""}`}>
        {weekdayNames().map((w) => <div key={w} className="cal-weekday">{narrow ? w[0] : w}</div>)}
        {narrow && days.map((k) => {
          const items = byDay.get(k) ?? [];
          return (
            <button key={k} type="button" onClick={() => setPicked(k)}
              className={`cal-day ${k.slice(0, 7) !== monthKey ? "other" : ""} ${k === today ? "today" : ""} ${picked === k ? "picked" : ""}`}>
              <div className="cal-daynum"><span>{parseKey(k).getDate()}</span></div>
              <div className="cal-dots">
                {items.slice(0, 3).map(({ rec }) => <span key={occKey(rec)} style={{ background: ops.colorFor(rec, view.config.colorField) ?? "#6366f1" }} />)}
                {items.length > 3 && <em>+{items.length - 3}</em>}
              </div>
            </button>
          );
        })}
        {!narrow && days.map((k) => (
          <div key={k} data-day={k}
            className={`cal-day ${k.slice(0, 7) !== monthKey ? "other" : ""} ${k === today ? "today" : ""} ${overDay === k ? "over" : ""}`}
            onDragOver={(e) => { if (dragId) { e.preventDefault(); setOverDay(k); } }}
            onDragLeave={() => setOverDay((d) => (d === k ? null : d))}
            onDrop={(e) => {
              e.preventDefault();
              const rec = expanded.find((r) => occKey(r) === dragId);
              if (rec) move(rec, k);
              setDragId(null); setOverDay(null);
            }}
            onDoubleClick={(e) => {
              if (e.target !== e.currentTarget || !ops.can.add || !isEditable(startField)) return;
              ops.create({ [startField.id]: k, ...(endField ? { [endField.id]: k } : {}) }, true);
            }}>
            <div className="cal-daynum">
              <span>{parseKey(k).getDate()}</span>
              {ops.can.add && isEditable(startField) && (
                <button className="cal-add" title="New record"
                  onClick={() => ops.create({ [startField.id]: k, ...(endField ? { [endField.id]: k } : {}) }, true)}>
                  <Icon name="plus" size={12} />
                </button>
              )}
            </div>
            {(byDay.get(k) ?? []).map(({ rec, isStart }) => {
              const color = ops.colorFor(rec, view.config.colorField) ?? "#6366f1";
              const time = isStart ? formatTime(rec.data[startField.id]) : "";
              return (
                <div key={occKey(rec)} className={`cal-item ${isStart ? "" : "cont"}`}
                  draggable={!isTouch && canEdit(startField) && (!rec.occurrence || !!repeatField)}
                  style={{ background: `${color}22`, borderLeftColor: color }}
                  onDragStart={(e) => { setDragId(occKey(rec)); e.dataTransfer.effectAllowed = "move"; }}
                  onDragEnd={() => { setDragId(null); setOverDay(null); }}
                  onClick={() => ops.open(rec)} title={recordTitle(fields, rec)}>
                  {time && <span className="cal-time">{time}</span>}
                  <span className="cal-title">{recordTitle(fields, rec)}</span>
                  {rec.occurrence && <Icon name="repeat" size={10} className="cal-repeat" />}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      {narrow && (
        <div className="cal-agenda">
          <div className="cal-agenda-head">
            <b>{parseKey(picked).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</b>
            {ops.can.add && isEditable(startField) && (
              <button className="btn sm" onClick={() => ops.create({ [startField.id]: picked, ...(endField ? { [endField.id]: picked } : {}) }, true)}>
                <Icon name="plus" size={14} /> New
              </button>
            )}
          </div>
          {(byDay.get(picked) ?? []).map(({ rec, isStart }) => {
            const color = ops.colorFor(rec, view.config.colorField) ?? "#6366f1";
            const time = isStart ? formatTime(rec.data[startField.id]) : "";
            return (
              <button key={occKey(rec)} type="button" className="cal-agenda-item" onClick={() => ops.open(rec)} style={{ borderLeftColor: color }}>
                <span className="cal-agenda-time">{time || (isStart ? "All day" : "Continues")}</span>
                <span className="cal-agenda-title">{recordTitle(fields, rec)}</span>
                {rec.occurrence && <Icon name="repeat" size={12} className="cal-repeat" />}
              </button>
            );
          })}
          {!(byDay.get(picked) ?? []).length && <p className="muted small">Nothing on this day.</p>}
        </div>
      )}
    </div>
  );
}

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type PointerEvent as RPointerEvent } from "react";
import type { Field, Rec } from "../types";
import { addDaysKey, dayKey, isDateField, isEditable, keyOf, parseKey, recordTitle, shiftValue } from "../util";
import { expandRecords } from "../recur";
import { Icon } from "./Icon";
import { holdToDrag } from "./touchDrag";
import { useNarrow } from "./useNarrow";
import { useVirtualRows } from "./useVirtualRows";
import type { ViewProps } from "./viewTypes";

type Scale = "day" | "week" | "month";
const SCALES: Record<Scale, { px: number; days: number; step: number; label: string }> = {
  day: { px: 44, days: 56, step: 14, label: "Day" },
  week: { px: 18, days: 140, step: 28, label: "Week" },
  month: { px: 5, days: 420, step: 91, label: "Month" },
};

const ROW_H = 36;
const HEADER_H = 52;
const DAY_MS = 86400000;

const diffDays = (a: string, b: string) => Math.round((+parseKey(a) - +parseKey(b)) / DAY_MS);

interface Drag { id: string; mode: "move" | "start" | "end"; x0: number; delta: number }

export function TimelineView({ fields, records, view, ops, setConfig }: ViewProps) {
  const canEdit = (f: Field) => ops.can.edit && isEditable(f);
  const dateFields = fields.filter(isDateField);
  const startField = dateFields.find((f) => f.id === view.config.dateField);
  const endField = dateFields.find((f) => f.id === view.config.endField);
  const repeatField = fields.find((f) => f.type === "recurrence");
  const scale: Scale = view.config.scale ?? "week";
  const S = SCALES[scale];
  const today = keyOf(new Date());
  const [rangeStart, setRangeStart] = useState(() => addDaysKey(today, -Math.round(SCALES[scale].days * 0.2)));
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const win = useVirtualRows(records.length, ROW_H, HEADER_H);
  const narrow = useNarrow();
  const TITLE_W = narrow ? 130 : 240;
  const width = S.days * S.px;

  // Re-centre on today when the scale changes.
  useEffect(() => { setRangeStart(addDaysKey(today, -Math.round(S.days * 0.2))); }, [scale]);
  useLayoutEffect(() => {
    if (win.el) win.el.scrollLeft = Math.max(0, diffDays(today, rangeStart) * S.px - 160);
  }, [rangeStart, S.px, today, win.el]);

  const days = useMemo(() => Array.from({ length: S.days }, (_, i) => addDaysKey(rangeStart, i)), [rangeStart, S.days]);
  const months = useMemo(() => {
    const out: { key: string; label: string; left: number; width: number }[] = [];
    days.forEach((d, i) => {
      const m = d.slice(0, 7);
      if (!out.length || out[out.length - 1].key !== m) {
        out.push({ key: m, left: i * S.px, width: 0,
          label: parseKey(d).toLocaleDateString(undefined, scale === "month" ? { month: "short", year: "2-digit" } : { month: "long", year: "numeric" }) });
      }
      out[out.length - 1].width += S.px;
    });
    return out;
  }, [days, S.px, scale]);

  // Global pointer tracking while dragging a bar or one of its edges.
  useEffect(() => {
    if (!drag) return;
    const move = (e: PointerEvent) => {
      const d = dragRef.current!;
      const delta = Math.round((e.clientX - d.x0) / S.px);
      if (delta !== d.delta) { dragRef.current = { ...d, delta }; setDrag(dragRef.current); }
    };
    const up = () => {
      const d = dragRef.current!;
      dragRef.current = null;
      setDrag(null);
      const rec = records.find((r) => r.id === d.id);
      if (!rec) return;
      if (d.delta === 0) { if (d.mode === "move") ops.open(rec); return; }
      commit(rec, d);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  }, [drag?.id]);

  if (!startField) {
    return (
      <div className="empty-state">
        <p>Choose a date field to plot records on the timeline.</p>
        <select className="input" style={{ width: 240 }} value="" onChange={(e) => setConfig({ dateField: e.target.value })}>
          <option value="" disabled>Date field…</option>
          {dateFields.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
        {!dateFields.length && <p className="muted">This database has no date fields yet.</p>}
      </div>
    );
  }

  /** [startKey, endKey] of a record, or null when it has no dates. */
  const span = (r: Rec): [string, string] | null => {
    const s = dayKey(r.data[startField.id]);
    const e = endField ? dayKey(r.data[endField.id]) : null;
    if (!s && !e) return null;
    const a = s ?? e!, b = e ?? s!;
    return b < a ? [a, a] : [a, b];
  };

  function commit(rec: Rec, d: Drag) {
    const sv = rec.data[startField!.id], ev = endField ? rec.data[endField.id] : undefined;
    const [a, b] = span(rec)!;
    const patch: Record<string, unknown> = {};
    if (d.mode === "move") {
      if (sv && canEdit(startField!)) patch[startField!.id] = shiftValue(sv, d.delta);
      if (ev && endField && canEdit(endField)) patch[endField.id] = shiftValue(ev, d.delta);
    } else if (d.mode === "start") {
      const delta = Math.min(d.delta, diffDays(b, a));
      patch[startField!.id] = shiftValue(sv ?? ev, delta);
    } else if (endField) {
      const delta = Math.max(d.delta, -diffDays(b, a));
      patch[endField.id] = shiftValue(ev ?? sv, delta);
    }
    ops.update(rec, patch);
  }

  const startDrag = (e: RPointerEvent, rec: Rec, mode: Drag["mode"]) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const editable = canEdit(startField) || (!!endField && canEdit(endField));
    const begin = (x: number) => { dragRef.current = { id: rec.id, mode, x0: x, delta: 0 }; setDrag(dragRef.current); };
    // touch: a tap opens it, a swipe scrolls, holding it for 3 s picks it up
    if (holdToDrag(e, { onTap: () => ops.open(rec), onStart: (x) => (editable ? begin(x) : ops.open(rec)) })) return;
    e.preventDefault();
    if (!editable) { ops.open(rec); return; }
    begin(e.clientX);
  };

  const setDatesAt = (e: MouseEvent<HTMLDivElement>, rec: Rec) => {
    if (!canEdit(startField)) return;
    const x = e.clientX - e.currentTarget.getBoundingClientRect().left;
    const day = addDaysKey(rangeStart, Math.floor(x / S.px));
    ops.update(rec, { [startField.id]: day, ...(endField && canEdit(endField) ? { [endField.id]: day } : {}) });
  };

  const shift = (n: number) => setRangeStart((r) => addDaysKey(r, n));
  const todayIdx = diffDays(today, rangeStart);

  return (
    <div className="timeline" style={{ ["--tl-title" as string]: `${TITLE_W}px` }}>
      <div className="cal-head">
        <button className="icon-btn" onClick={() => shift(-S.step)} aria-label="Earlier"><Icon name="left" /></button>
        <button className="icon-btn" onClick={() => shift(S.step)} aria-label="Later"><Icon name="right" /></button>
        <button className="btn sm" onClick={() => setRangeStart(addDaysKey(today, -Math.round(S.days * 0.2)))}>Today</button>
        <span className="spacer" />
        <div className="segmented">
          {(Object.keys(SCALES) as Scale[]).map((s) => (
            <button key={s} className={s === scale ? "active" : ""} onClick={() => setConfig({ scale: s })}>{SCALES[s].label}</button>
          ))}
        </div>
      </div>

      <div className="tl-scroll" ref={win.ref}>
        <div className="tl-content" style={{ width: TITLE_W + width }}>
          {/* header */}
          <div className="tl-header">
            <div className="tl-corner">{records.length} records</div>
            <div className="tl-axis" style={{ width }}>
              <div className="tl-months">
                {months.map((m) => <div key={m.key} className="tl-month" style={{ left: m.left, width: m.width }}>{m.width > 40 ? m.label : ""}</div>)}
              </div>
              {scale !== "month" && (
                <div className="tl-days">
                  {days.map((d, i) => {
                    const dt = parseKey(d);
                    const show = scale === "day" || dt.getDay() === 1;
                    return show ? (
                      <div key={d} className={`tl-daylabel ${d === today ? "today" : ""}`} style={{ left: i * S.px, width: scale === "day" ? S.px : S.px * 7 }}>
                        {scale === "day" ? `${dt.toLocaleDateString(undefined, { weekday: "narrow" })} ${dt.getDate()}` : dt.getDate()}
                      </div>
                    ) : null;
                  })}
                </div>
              )}
            </div>
          </div>

          {/* background: weekends + today line */}
          <div className="tl-bg" style={{ left: TITLE_W, width }}>
            {scale === "day" && days.map((d, i) => [0, 6].includes(parseKey(d).getDay())
              ? <div key={d} className="tl-weekend" style={{ left: i * S.px, width: S.px }} /> : null)}
            {scale !== "day" && months.map((m) => <div key={m.key} className="tl-gridline" style={{ left: m.left }} />)}
            {todayIdx >= 0 && todayIdx < S.days && <div className="tl-today" style={{ left: todayIdx * S.px + S.px / 2 }} />}
          </div>

          {/* rows (only those near the visible area are rendered) */}
          {win.padTop > 0 && <div style={{ height: win.padTop }} />}
          {records.slice(win.start, win.end).map((r) => {
            const sp = span(r);
            const color = ops.colorFor(r, view.config.colorField) ?? "var(--accent)";
            // later occurrences of a repeating record share its row
            const occs = repeatField && r.data[repeatField.id] ? expandRecords([r], fields, startField, endField, rangeStart, days[days.length - 1]) : [];
            const repeats = occs.filter((o) => o.occurrence !== o.occurrenceOf || o.overridden);
            // the record's own bar is its first occurrence, unless that one was moved or skipped
            const ownHidden = occs.length > 0 && occs[0].occurrence !== undefined && !occs.some((o) => o.occurrence === o.occurrenceOf && !o.overridden);
            let bar = null;
            if (sp) {
              let a = diffDays(sp[0], rangeStart), b = diffDays(sp[1], rangeStart);
              if (drag?.id === r.id) {
                if (drag.mode === "move") { a += drag.delta; b += drag.delta; }
                else if (drag.mode === "start") a = Math.min(a + drag.delta, b);
                else b = Math.max(b + drag.delta, a);
              }
              if (ownHidden && b >= 0 && a < S.days) {
                bar = null;
              } else if (b >= 0 && a < S.days) {
                const left = Math.max(0, a) * S.px;
                const right = Math.min(S.days, b + 1) * S.px;
                const w = Math.max(right - left - 2, 6);
                const inside = w >= 90;
                bar = (
                  <>
                    <div className={`tl-bar ${drag?.id === r.id ? "dragging" : ""}`}
                      style={{ left: left + 1, width: w, background: `color-mix(in srgb, ${color} 22%, var(--bg))`, borderColor: color }}
                      onPointerDown={(e) => startDrag(e, r, "move")} title={recordTitle(fields, r)}>
                      {canEdit(startField) && <span className="tl-handle l" onPointerDown={(e) => startDrag(e, r, "start")} />}
                      {inside && <span className="tl-bar-label">{recordTitle(fields, r)}</span>}
                      {endField && canEdit(endField) && <span className="tl-handle r" onPointerDown={(e) => startDrag(e, r, "end")} />}
                    </div>
                    {!inside && <span className="tl-outside-label" style={{ left: left + w + 8 }}>{recordTitle(fields, r)}</span>}
                  </>
                );
              } else if (!repeats.length) {
                bar = <span className="tl-offscreen" style={a >= S.days ? { right: 8 } : { left: 8 }}>{a >= S.days ? "→" : "←"} {recordTitle(fields, r)}</span>;
              }
            }
            return (
              <div key={r.id} className="tl-row" style={{ height: ROW_H }}>
                <div className="tl-title" onClick={() => ops.open(r)}>
                  <span className="list-dot" style={{ background: color }} />
                  <span className="tl-title-text">{recordTitle(fields, r)}</span>
                </div>
                <div className={`tl-track ${sp ? "" : "undated"}`} style={{ width }} onClick={(e) => !sp && setDatesAt(e, r)}>
                  {bar}
                  {repeats.map((o) => {
                    const [s0, s1] = span(o)!;
                    const a = Math.max(0, diffDays(s0, rangeStart)), b = Math.min(S.days - 1, diffDays(s1, rangeStart));
                    if (b < 0 || a >= S.days) return null;
                    return (
                      <div key={o.occurrence} className="tl-bar repeat" title={`${recordTitle(fields, r)} (repeat)`}
                        style={{ left: a * S.px + 1, width: Math.max((b - a + 1) * S.px - 2, 6), borderColor: color,
                          background: `color-mix(in srgb, ${color} 12%, var(--bg))` }}
                        onClick={() => ops.open(o)}>
                        {(b - a + 1) * S.px >= 40 && <Icon name="repeat" size={10} />}
                      </div>
                    );
                  })}
                  {!sp && canEdit(startField) && <span className="tl-hint">Click to set dates</span>}
                </div>
              </div>
            );
          })}
          {win.padBottom > 0 && <div style={{ height: win.padBottom }} />}
          <div className="tl-row">
            <div className="tl-title">
              {ops.can.add && <button className="ghost-btn" onClick={() => ops.create({}, true)}><Icon name="plus" size={14} /> New</button>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

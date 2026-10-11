import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import type { Field, Rec } from "../types";
import { isDateOnly, keyOf, parseKey, recordTitle } from "../util";
import type { Ops } from "./viewTypes";
import { Icon } from "./Icon";

const HOUR = 48;          // px per hour
const SNAP = 15;          // minutes
const MIN = 60000;

interface Block { rec: Rec; key: string; start: Date; end: Date; col: number; lane: number; lanes: number; hasEnd: boolean }
interface Drag { key: string; mode: "move" | "resize"; x0: number; y0: number; dMin: number; dDay: number; moved: boolean }

const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
const occKey = (r: Rec) => `${r.id}:${r.occurrence ?? ""}`;

/** Day / week calendar: an all-day row and a 24-hour grid with draggable blocks. */
export function TimeGrid({ days, records, fields, startField, endField, ops, color, canEdit, onTimes }: {
  days: string[];                       // day keys shown as columns
  records: Rec[];                       // already expanded for repeats
  fields: Field[];
  startField: Field;
  endField?: Field;
  ops: Ops;
  color: (r: Rec) => string;
  canEdit: boolean;
  onTimes: (rec: Rec, patch: Record<string, string>) => void;   // new start/end values (handles repeats)
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const cols = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 60000); return () => clearInterval(t); }, []);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    el.scrollTop = HOUR * 7 - 8;
    // a week wider than the screen (phones): start at today's column
    const i = days.indexOf(keyOf(new Date()));
    if (i > 0 && el.scrollWidth > el.clientWidth) el.scrollLeft = Math.max(0, i * ((el.scrollWidth - 56) / days.length) - 24);
  }, [days.join()]);

  const first = parseKey(days[0]);
  const after = new Date(first); after.setDate(after.getDate() + days.length);
  const dayIndex = (d: Date) => Math.floor((+new Date(d.getFullYear(), d.getMonth(), d.getDate()) - +first) / 864e5 + 0.01);

  // ---- split records into all-day items and timed blocks
  const allDay: { rec: Rec; from: number; to: number }[] = [];
  const timed: Omit<Block, "lane" | "lanes" | "col">[] = [];
  const allDayField = fields.find((f) => f.system_key === "all_day");
  for (const r of records) {
    const sv = r.data[startField.id] ?? (endField ? r.data[endField.id] : undefined);
    if (typeof sv !== "string") continue;
    const ev = endField ? r.data[endField.id] : undefined;
    if (isDateOnly(sv) || (allDayField && r.data[allDayField.id])) {
      const a = parseKey(sv.slice(0, 10)), b = typeof ev === "string" ? parseKey(ev.slice(0, 10)) : a;
      const from = Math.max(0, dayIndex(a)), to = Math.min(days.length - 1, dayIndex(b < a ? a : b));
      if (to >= 0 && from < days.length && dayIndex(b < a ? a : b) >= 0) allDay.push({ rec: r, from, to });
      continue;
    }
    const start = new Date(sv);
    let end = typeof ev === "string" && !isDateOnly(ev) ? new Date(ev) : new Date(+start + 30 * MIN);
    if (+end <= +start) end = new Date(+start + 30 * MIN);
    if (+end - +start > 24 * 3600000) {  // long timed items read better in the all-day row
      const from = Math.max(0, dayIndex(start)), to = Math.min(days.length - 1, dayIndex(new Date(+end - 1)));
      if (to >= 0 && from < days.length) allDay.push({ rec: r, from, to });
      continue;
    }
    if (+end <= +first || +start >= +after) continue;
    timed.push({ rec: r, key: occKey(r), start, end, hasEnd: typeof ev === "string" });
  }

  // ---- per column: pieces of blocks, laid out side by side when they overlap
  const blocks: Block[] = [];
  days.forEach((k, col) => {
    const d0 = parseKey(k), d1 = new Date(d0); d1.setDate(d1.getDate() + 1);
    const pieces = timed.filter((b) => +b.end > +d0 && +b.start < +d1)
      .map((b) => ({ ...b, start: new Date(Math.max(+b.start, +d0)), end: new Date(Math.min(+b.end, +d1)), col, lane: 0, lanes: 1 }))
      .sort((a, b) => +a.start - +b.start || +b.end - +a.end);
    let cluster: Block[] = [], clusterEnd = 0;
    const flush = () => { const n = Math.max(1, ...cluster.map((c) => c.lane + 1)); cluster.forEach((c) => { c.lanes = n; }); cluster = []; };
    for (const p of pieces) {
      if (cluster.length && +p.start >= clusterEnd) flush();
      const laneEnds: number[] = [];
      cluster.forEach((c) => { laneEnds[c.lane] = Math.max(laneEnds[c.lane] ?? 0, +c.end); });
      let lane = laneEnds.findIndex((e) => e <= +p.start);
      if (lane < 0) lane = laneEnds.length;
      p.lane = lane;
      cluster.push(p);
      clusterEnd = Math.max(clusterEnd, +p.end);
      blocks.push(p);
    }
    flush();
  });

  // ---- dragging
  useEffect(() => {
    if (!drag) return;
    const move = (e: PointerEvent) => {
      const d = dragRef.current!;
      const colW = (cols.current?.clientWidth ?? 1) / days.length;
      const dMin = Math.round(((e.clientY - d.y0) / HOUR) * 60 / SNAP) * SNAP;
      const dDay = d.mode === "move" ? Math.round((e.clientX - d.x0) / colW) : 0;
      const moved = d.moved || Math.abs(e.clientY - d.y0) > 4 || Math.abs(e.clientX - d.x0) > 4;
      if (dMin !== d.dMin || dDay !== d.dDay || moved !== d.moved) { dragRef.current = { ...d, dMin, dDay, moved }; setDrag(dragRef.current); }
    };
    const up = () => {
      const d = dragRef.current!;
      dragRef.current = null;
      setDrag(null);
      const b = timed.find((x) => x.key === d.key);
      if (!b) return;
      if (!d.moved) { ops.open(b.rec); return; }
      if (!d.dMin && !d.dDay) return;
      const shift = (x: Date) => { const n = new Date(x); n.setDate(n.getDate() + d.dDay); return new Date(+n + d.dMin * MIN); };
      const patch: Record<string, string> = {};
      if (d.mode === "move") {
        if (b.rec.data[startField.id]) patch[startField.id] = iso(shift(b.start));
        if (endField && b.hasEnd) patch[endField.id] = iso(shift(b.end));
        else if (!b.rec.data[startField.id] && endField) patch[endField.id] = iso(shift(b.start));
      } else if (endField) {
        const e = new Date(Math.max(+b.end + d.dMin * MIN, +b.start + SNAP * MIN));
        patch[endField.id] = iso(e);
      }
      if (Object.keys(patch).length) onTimes(b.rec, patch);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  }, [drag?.key, drag?.mode]);

  const startDrag = (e: RPointerEvent, b: Block, mode: Drag["mode"]) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    const begin = (x: number, y: number, moved: boolean) => {
      dragRef.current = { key: b.key, mode, x0: x, y0: y, dMin: 0, dDay: 0, moved };
      setDrag(dragRef.current);
    };
    if (e.pointerType === "touch") return;  // touch: no dragging; a tap opens it (onClick), a swipe scrolls
    if (!canEdit) { ops.open(b.rec); return; }
    e.preventDefault();
    begin(e.clientX, e.clientY, false);
  };

  const createAt = (e: React.MouseEvent<HTMLDivElement>, k: string) => {
    if (!ops.can.add || e.target !== e.currentTarget) return;
    const y = e.clientY - e.currentTarget.getBoundingClientRect().top;
    const mins = Math.max(0, Math.min(23 * 60 + 30, Math.floor((y / HOUR) * 60 / 30) * 30));
    const s = parseKey(k); s.setMinutes(mins);
    const data: Record<string, string> = startField.options.includeTime ? { [startField.id]: iso(s) } : { [startField.id]: k };
    if (endField) data[endField.id] = endField.options.includeTime ? iso(new Date(+s + 3600000)) : k;
    ops.create(data, true);
  };

  const todayIdx = days.indexOf(keyOf(now));
  const fmt = (d: Date) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

  return (
    <div className={`tgrid ${days.length > 1 ? "multi" : ""}`} ref={scroller} style={{ ["--days" as string]: days.length }}>
      <div className="tgrid-inner">
      <div className="tgrid-top">
      <div className="tgrid-head">
        <div className="tgrid-gutter" />
        {days.map((k) => {
          const d = parseKey(k);
          return (
            <div key={k} className={`tgrid-day ${k === keyOf(now) ? "today" : ""}`}>
              <span className="muted small">{d.toLocaleDateString(undefined, { weekday: days.length > 1 ? "short" : "long" })}</span>
              <b>{d.getDate()}</b>
            </div>
          );
        })}
      </div>
      {allDay.length > 0 && (
        <div className="tgrid-allday">
          <div className="tgrid-gutter muted small">all-day</div>
          <div className="tgrid-allday-cells">
            {allDay.map(({ rec, from, to }) => (
              <button key={occKey(rec)} className="tgrid-allday-item" onClick={() => ops.open(rec)} title={recordTitle(fields, rec)}
                style={{ gridColumn: `${from + 1} / ${to + 2}`, background: `${color(rec)}22`, borderLeftColor: color(rec) }}>
                <span className="tgrid-allday-label">{recordTitle(fields, rec)}</span>
                {rec.occurrence && <Icon name="repeat" size={10} className="cal-repeat" />}
              </button>
            ))}
          </div>
        </div>
      )}
      </div>
        <div className="tgrid-body" style={{ height: HOUR * 24 }}>
          <div className="tgrid-gutter tgrid-hours">
            {Array.from({ length: 24 }, (_, h) => (
              <span key={h} style={{ top: h * HOUR }}>{h ? new Date(2000, 0, 1, h).toLocaleTimeString(undefined, { hour: "numeric" }) : ""}</span>
            ))}
          </div>
          <div className="tgrid-cols" ref={cols}>
            {days.map((k, col) => (
              <div key={k} className={`tgrid-col ${col === todayIdx ? "today" : ""}`} onClick={(e) => createAt(e, k)}
                title={ops.can.add ? "Click to add" : undefined}>
                {col === todayIdx && <div className="tgrid-now" style={{ top: (now.getHours() * 60 + now.getMinutes()) / 60 * HOUR }} />}
              </div>
            ))}
            {blocks.map((b) => {
              const d = drag?.key === b.key ? drag : null;
              const top = ((b.start.getHours() * 60 + b.start.getMinutes()) / 60) * HOUR + (d?.mode === "move" ? d.dMin / 60 * HOUR : 0);
              let h = Math.max(((+b.end - +b.start) / 3600000) * HOUR, 18);
              if (d?.mode === "resize") h = Math.max(h + d.dMin / 60 * HOUR, SNAP / 60 * HOUR);
              const col = b.col + (d?.mode === "move" ? d.dDay : 0);
              const c = color(b.rec);
              return (
                <div key={`${b.key}:${b.col}`} className={`tgrid-block ${d ? "dragging" : ""}`} title={recordTitle(fields, b.rec)}
                  style={{ top, height: h - 2, left: `calc(${col} * 100% / var(--days) + ${b.lane} * (100% / var(--days)) / ${b.lanes} + 2px)`,
                    width: `calc((100% / var(--days)) / ${b.lanes} - 4px)`, background: `color-mix(in srgb, ${c} 18%, var(--bg))`, borderLeftColor: c }}
                  onPointerDown={(e) => startDrag(e, b, "move")}
                  onClick={(e) => { if ((e.nativeEvent as PointerEvent).pointerType === "touch") ops.open(b.rec); }}>
                  <span className="tgrid-title">{recordTitle(fields, b.rec)}</span>
                  {h > 30 && <span className="tgrid-time">{fmt(b.start)}{b.hasEnd ? ` – ${fmt(b.end)}` : ""}</span>}
                  {b.rec.occurrence && <Icon name="repeat" size={10} className="cal-repeat" />}
                  {canEdit && endField && b.hasEnd && <span className="tgrid-resize" onPointerDown={(e) => startDrag(e, b, "resize")} />}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

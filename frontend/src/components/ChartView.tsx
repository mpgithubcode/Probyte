import { useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { ChartConfig, Field, Rec, Refs } from "../types";
import { addDaysKey, dayKey, displayText, innerField, parseKey, valueType } from "../util";
import { Icon } from "./Icon";
import type { ViewProps } from "./viewTypes";

// ---------------------------------------------------------------- data

interface Point { key: string; label: string; value: number }

const EMPTY = "__empty";
const SLOTS = 8; // categorical palette size (--series-1..8); more categories fold into "Other"

const DEFAULT_CHART: ChartConfig = { type: "bar", agg: "count", bucket: "month" };

function weekStart(k: string) {
  const d = parseKey(k);
  return addDaysKey(k, -((d.getDay() + 6) % 7));
}

function bucketKeys(field: Field, v: unknown, bucket: ChartConfig["bucket"], refs?: Refs): string[] {
  const empty = v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length);
  if (field.type === "checkbox") return [v ? "Checked" : "Unchecked"];
  if (empty) return [EMPTY];
  if (field.type === "multi_select") return (v as unknown[]).map(String);
  if (Array.isArray(v)) {
    // Group lists (links, users, lookups…) by each item's visible text.
    const inner = innerField(field);
    return v.map((x) => field.type === "link" || field.type === "user" ? displayText(field, [x], refs)
      : inner ? displayText(inner, inner.type === "link" || inner.type === "user" ? [x] : x, refs) : String(x));
  }
  if (valueType(field) === "date") {
    const k = dayKey(v);
    if (!k) return [EMPTY];
    return [bucket === "day" ? k : bucket === "week" ? weekStart(k) : k.slice(0, 7)];
  }
  return [displayText(field, v, refs)];
}

function nextKey(k: string, bucket: ChartConfig["bucket"]) {
  if (bucket === "month") {
    const [y, m] = k.split("-").map(Number);
    return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`;
  }
  return addDaysKey(k, bucket === "week" ? 7 : 1);
}

function dateLabel(k: string, bucket: ChartConfig["bucket"]) {
  if (bucket === "month") return parseKey(`${k}-01`).toLocaleDateString(undefined, { month: "short", year: "numeric" });
  return parseKey(k).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function computePoints(records: Rec[], xf: Field, yf: Field | undefined, cfg: ChartConfig, refs?: Refs): Point[] {
  const groups = new Map<string, number[]>();
  for (const r of records) {
    const y = cfg.agg === "count" ? 1 : Number(r.data[yf?.id ?? ""]);
    for (const k of bucketKeys(xf, r.data[xf.id], cfg.bucket, refs)) {
      if (!groups.has(k)) groups.set(k, []);
      if (cfg.agg === "count" || Number.isFinite(y)) groups.get(k)!.push(y);
    }
  }
  const agg = (vals: number[] = []) => {
    if (cfg.agg === "count") return vals.length;
    const sum = vals.reduce((a, b) => a + b, 0);
    return cfg.agg === "sum" ? sum : vals.length ? sum / vals.length : 0;
  };

  let keys = [...groups.keys()].filter((k) => k !== EMPTY);
  if (valueType(xf) === "date") {
    keys.sort();
    // Fill gaps so time reads evenly (cap the span to keep the chart legible).
    if (keys.length > 1) {
      const filled: string[] = [];
      for (let k = keys[0]; k <= keys[keys.length - 1] && filled.length < 400; k = nextKey(k, cfg.bucket)) filled.push(k);
      keys = filled;
    }
  } else if (xf.type === "single_select" || xf.type === "multi_select") {
    const order = (xf.options.choices ?? []).map((c) => c.name);
    keys.sort((a, b) => (order.indexOf(a) + 1 || 1e6) - (order.indexOf(b) + 1 || 1e6));
  } else if (xf.type === "checkbox") {
    keys.sort((a) => (a === "Checked" ? -1 : 1));
  } else if (xf.type === "number" || xf.type === "rating") {
    keys.sort((a, b) => Number(a) - Number(b));
  } else {
    keys.sort((a, b) => agg(groups.get(b)) - agg(groups.get(a)) || a.localeCompare(b));
  }
  const pts = keys.map((k) => ({ key: k, label: valueType(xf) === "date" ? dateLabel(k, cfg.bucket) : k, value: agg(groups.get(k)) }));
  if (groups.has(EMPTY) && !cfg.hideEmpty) pts.push({ key: EMPTY, label: "Empty", value: agg(groups.get(EMPTY)) });
  return pts;
}

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const precise = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const fmt = (v: number) => (Math.abs(v) >= 10000 ? compact.format(v) : precise.format(v));

function niceTicks(max: number, count = 4) {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw)!;
  const ticks = [];
  for (let t = 0; t <= max + step * 0.001; t += step) ticks.push(+t.toFixed(10));
  if (ticks[ticks.length - 1] < max) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
  return ticks;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

interface Tip { x: number; y: number; label: string; value: string }

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }}>
      <div className="chart-tip-label">{tip.label}</div>
      <div className="chart-tip-value">{tip.value}</div>
    </div>
  );
}

const relPos = (e: MouseEvent, el: HTMLElement | null) => {
  const r = el?.getBoundingClientRect();
  return r ? { x: e.clientX - r.left, y: e.clientY - r.top } : { x: 0, y: 0 };
};

// ---------------------------------------------------------------- marks

/** Categorical magnitude: horizontal bars, value at the tip. */
function HBars({ points, measure }: { points: Point[]; measure: string }) {
  const max = Math.max(...points.map((p) => p.value), 0) || 1;
  return (
    <div className="hbars" role="img" aria-label={measure}>
      {points.map((p) => (
        <div key={p.key} className="hbar-row" title={`${p.label}: ${fmt(p.value)}`}>
          <span className={`hbar-label ${p.key === EMPTY ? "muted" : ""}`}>{p.label}</span>
          <div className="hbar-track">
            {p.value > 0 && <div className="hbar" style={{ width: `${(p.value / max) * 100}%` }} />}
            <span className="hbar-value" style={{ left: `calc(${(p.value / max) * 100}% + 8px)` }}>{fmt(p.value)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

const H = 300;
const M = { t: 20, r: 20, b: 30, l: 48 };

function colPath(x: number, y: number, w: number, h: number) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function Axes({ w, ticks, y, points, xAt }: { w: number; ticks: number[]; y: (v: number) => number; points: Point[]; xAt: (i: number) => number }) {
  const innerW = w - M.l - M.r;
  const every = Math.max(1, Math.ceil((points.length * 64) / Math.max(innerW, 1)));
  return (
    <g>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={M.l} x2={w - M.r} y1={y(t)} y2={y(t)} className={t === 0 ? "chart-baseline" : "chart-grid"} />
          <text x={M.l - 8} y={y(t)} dy="0.32em" textAnchor="end" className="chart-tick">{compact.format(t)}</text>
        </g>
      ))}
      {points.map((p, i) => (i % every === 0 ? (
        <text key={p.key} x={xAt(i)} y={H - M.b + 18} textAnchor="middle" className="chart-tick">{p.label}</text>
      ) : null))}
    </g>
  );
}

/** Time buckets as columns. */
function Columns({ points }: { points: Point[] }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const ticks = niceTicks(Math.max(...points.map((p) => p.value), 0));
  const top = ticks[ticks.length - 1] || 1;
  const innerW = w - M.l - M.r, innerH = H - M.t - M.b;
  const band = innerW / points.length;
  const barW = Math.min(24, Math.max(2, band - 2));
  const y = (v: number) => M.t + innerH * (1 - v / top);
  const xAt = (i: number) => M.l + band * i + band / 2;
  const maxIdx = points.reduce((m, p, i) => (p.value > points[m].value ? i : m), 0);
  return (
    <div className="chart-svg-wrap" ref={ref} onMouseLeave={() => { setHover(null); setTip(null); }}>
      <svg width={w} height={H} role="img">
        <Axes w={w} ticks={ticks} y={y} points={points} xAt={xAt} />
        {points.map((p, i) => p.value > 0 && (
          <path key={p.key} d={colPath(xAt(i) - barW / 2, y(p.value), barW, y(0) - y(p.value))}
            className="chart-col" style={{ opacity: hover === null || hover === i ? 1 : 0.45 }} />
        ))}
        {points[maxIdx]?.value > 0 && (
          <text x={xAt(maxIdx)} y={y(points[maxIdx].value) - 6} textAnchor="middle" className="chart-value">{fmt(points[maxIdx].value)}</text>
        )}
        {points.map((p, i) => (
          <rect key={p.key} x={M.l + band * i} y={M.t} width={band} height={innerH} fill="transparent"
            onMouseMove={(e) => { setHover(i); const pos = relPos(e, ref.current); setTip({ ...pos, label: p.label, value: fmt(p.value) }); }} />
        ))}
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

/** Change over time / ordered buckets: 2px line, 10% area wash, crosshair. */
function LineChart({ points }: { points: Point[] }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const ticks = niceTicks(Math.max(...points.map((p) => p.value), 0));
  const top = ticks[ticks.length - 1] || 1;
  const innerW = w - M.l - M.r, innerH = H - M.t - M.b;
  const n = points.length;
  const xAt = (i: number) => (n === 1 ? M.l + innerW / 2 : M.l + (innerW * i) / (n - 1));
  const y = (v: number) => M.t + innerH * (1 - v / top);
  const line = points.map((p, i) => `${i ? "L" : "M"}${xAt(i)},${y(p.value)}`).join("");
  const area = `${line}L${xAt(n - 1)},${y(0)}L${xAt(0)},${y(0)}Z`;
  const last = n - 1;
  const onMove = (e: MouseEvent) => {
    const { x } = relPos(e, ref.current);
    setHover(n === 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round(((x - M.l) / innerW) * (n - 1)))));
  };
  const tip: Tip | null = hover === null ? null : { x: xAt(hover), y: y(points[hover].value), label: points[hover].label, value: fmt(points[hover].value) };
  return (
    <div className="chart-svg-wrap" ref={ref} onMouseLeave={() => setHover(null)}>
      <svg width={w} height={H} role="img">
        <Axes w={w} ticks={ticks} y={y} points={points} xAt={xAt} />
        <path d={area} className="chart-area" />
        <path d={line} className="chart-line" />
        {n <= 24 && points.map((p, i) => <circle key={p.key} cx={xAt(i)} cy={y(p.value)} r={4} className="chart-dot" />)}
        {n > 24 && <circle cx={xAt(last)} cy={y(points[last].value)} r={4} className="chart-dot" />}
        <text x={xAt(last)} y={y(points[last].value) - 10} textAnchor="end" className="chart-value">{fmt(points[last].value)}</text>
        {hover !== null && (
          <>
            <line x1={xAt(hover)} x2={xAt(hover)} y1={M.t} y2={y(0)} className="chart-crosshair" />
            <circle cx={xAt(hover)} cy={y(points[hover].value)} r={5} className="chart-dot" />
          </>
        )}
        <rect x={M.l} y={M.t} width={Math.max(innerW, 0)} height={innerH} fill="transparent" onMouseMove={onMove} />
      </svg>
      <Tooltip tip={tip} />
    </div>
  );
}

function arc(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number) {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const p = (r: number, a: number) => `${cx + r * Math.sin(a)},${cy - r * Math.cos(a)}`;
  return `M${p(r1, a0)}A${r1},${r1} 0 ${large} 1 ${p(r1, a1)}L${p(r0, a1)}A${r0},${r0} 0 ${large} 0 ${p(r0, a0)}Z`;
}

/** Part-to-whole: donut + legend with values (legend always present for >= 2 slices). */
function Donut({ points, measure }: { points: Point[]; measure: string }) {
  const [hover, setHover] = useState<string | null>(null);
  const positive = points.filter((p) => p.value > 0);
  // Colors follow the category's stable position, never its rank. Past 8 slices the smallest fold into "Other".
  let slices = positive.map((p, i) => ({ ...p, color: `var(--series-${(i % SLOTS) + 1})` }));
  if (positive.length > SLOTS) {
    const keep = new Set([...positive].sort((a, b) => b.value - a.value).slice(0, SLOTS - 1).map((p) => p.key));
    const kept = positive.filter((p) => keep.has(p.key));
    const other = positive.filter((p) => !keep.has(p.key)).reduce((s, p) => s + p.value, 0);
    slices = [...kept.map((p, i) => ({ ...p, color: `var(--series-${i + 1})` })),
      { key: "__other", label: `Other (${positive.length - kept.length})`, value: other, color: "var(--chart-muted)" }];
  }
  const total = slices.reduce((s, p) => s + p.value, 0);
  const size = 240, R = size / 2, r0 = R * 0.62;
  let a = 0;
  const arcs = slices.map((s) => {
    const a0 = a, a1 = a + (s.value / total) * Math.PI * 2;
    a = a1;
    return { ...s, d: slices.length === 1 ? null : arc(R, R, r0, R, a0, a1) };
  });
  const hovered = arcs.find((s) => s.key === hover);
  return (
    <div className="donut-wrap">
      <svg width={size} height={size} role="img" aria-label={measure} onMouseLeave={() => setHover(null)}>
        {arcs.map((s) => s.d
          ? <path key={s.key} d={s.d} fill={s.color} className="donut-slice" style={{ opacity: hover && hover !== s.key ? 0.4 : 1 }}
              onMouseEnter={() => setHover(s.key)} />
          : <circle key={s.key} cx={R} cy={R} r={(R + r0) / 2} fill="none" stroke={s.color} strokeWidth={R - r0} />)}
        <text x={R} y={R - 6} textAnchor="middle" className="donut-total">{fmt(hovered?.value ?? total)}</text>
        <text x={R} y={R + 16} textAnchor="middle" className="chart-tick">{hovered ? hovered.label : "Total"}</text>
      </svg>
      <div className="donut-legend">
        {arcs.map((s) => (
          <div key={s.key} className={`legend-row ${hover === s.key ? "active" : ""}`} onMouseEnter={() => setHover(s.key)} onMouseLeave={() => setHover(null)}>
            <span className="legend-swatch" style={{ background: s.color }} />
            <span className="legend-label">{s.label}</span>
            <span className="legend-value">{fmt(s.value)}</span>
            <span className="legend-pct">{total ? Math.round((s.value / total) * 100) : 0}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function DataTable({ points, xName, measure }: { points: Point[]; xName: string; measure: string }) {
  return (
    <div className="chart-table-wrap">
      <table className="chart-table">
        <thead><tr><th>{xName}</th><th>{measure}</th></tr></thead>
        <tbody>{points.map((p) => <tr key={p.key}><td>{p.label}</td><td>{fmt(p.value)}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- view

export function ChartView({ ds, fields, records, view, setConfig }: ViewProps) {
  const cfg: ChartConfig = { ...DEFAULT_CHART, ...view.config.chart };
  const set = (p: Partial<ChartConfig>) => setConfig({ chart: { ...cfg, ...p } });
  const [showData, setShowData] = useState(false);

  const groupable = fields.filter((f) => f.type !== "long_text" && f.type !== "button" && f.type !== "attachment");
  const numeric = fields.filter((f) => valueType(f) === "number");
  const xf = groupable.find((f) => f.id === cfg.xField) ?? groupable.find((f) => f.type === "single_select");
  const yf = numeric.find((f) => f.id === cfg.yField);
  const agg = cfg.agg !== "count" && !yf ? "count" : cfg.agg;
  const measure = agg === "count" ? "Count" : `${agg === "sum" ? "Sum" : "Average"} of ${yf!.name}`;

  // On a time axis (line / columns) undated records have no position: leave them out and say so.
  const timeAxis = !!xf && valueType(xf) === "date" && cfg.type !== "donut";
  const allPoints = useMemo(
    () => (xf ? computePoints(records, xf, yf, { ...cfg, agg }, ds.refs) : []),
    [records, xf, yf, cfg.bucket, cfg.hideEmpty, agg, ds.refs],
  );
  const points = timeAxis ? allPoints.filter((p) => p.key !== EMPTY) : allPoints;
  const undated = timeAxis ? allPoints.find((p) => p.key === EMPTY) : undefined;

  const measureValue = agg === "count" ? "count" : `${agg}:${yf!.id}`;
  return (
    <div className="chart-view">
      <div className="chart-controls">
        <div className="segmented">
          {(["bar", "line", "donut"] as const).map((t) => (
            <button key={t} className={cfg.type === t ? "active" : ""} onClick={() => set({ type: t })}>
              {t === "bar" ? "Bar" : t === "line" ? "Line" : "Donut"}
            </button>
          ))}
        </div>
        <label className="tool-label">Group by
          <select className="input sm" value={xf?.id ?? ""} onChange={(e) => set({ xField: e.target.value })}>
            {!xf && <option value="">Choose…</option>}
            {groupable.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
        {xf && valueType(xf) === "date" && (
          <select className="input sm" value={cfg.bucket} onChange={(e) => set({ bucket: e.target.value as ChartConfig["bucket"] })}>
            <option value="day">by day</option>
            <option value="week">by week</option>
            <option value="month">by month</option>
          </select>
        )}
        <label className="tool-label">Measure
          <select className="input sm" value={measureValue}
            onChange={(e) => { const [a, id] = e.target.value.split(":"); set({ agg: a as ChartConfig["agg"], yField: id }); }}>
            <option value="count">Count of records</option>
            {numeric.map((f) => <option key={`s${f.id}`} value={`sum:${f.id}`}>Sum of {f.name}</option>)}
            {numeric.map((f) => <option key={`a${f.id}`} value={`avg:${f.id}`}>Average of {f.name}</option>)}
          </select>
        </label>
        <label className="inline-check small"><input type="checkbox" checked={!!cfg.hideEmpty} onChange={(e) => set({ hideEmpty: e.target.checked })} /> Hide empty</label>
        <span className="spacer" />
        <button className={`tool-btn ${showData ? "on" : ""}`} onClick={() => setShowData(!showData)}><Icon name="table" size={14} /> Data</button>
      </div>

      {!xf ? (
        <div className="empty-state"><p>Choose a field to group records by.</p></div>
      ) : !points.length || points.every((p) => p.value === 0) ? (
        <div className="empty-state"><p className="muted">No data to chart yet.</p></div>
      ) : (
        <div className="chart-card">
          <div className="chart-title">
            {measure} <span className="muted">by {xf.name}</span>
            {xf.type === "multi_select" && <span className="muted chart-note">Records with several {xf.name} are counted once per value</span>}
            {undated && <span className="muted chart-note">{fmt(undated.value)} {agg === "count" ? "records" : ""} without a {xf.name} not shown</span>}
          </div>
          {cfg.type === "donut" ? <Donut points={points} measure={measure} />
            : cfg.type === "line" ? <LineChart points={points} />
            : valueType(xf) === "date" ? <Columns points={points} />
            : <HBars points={points} measure={measure} />}
          {showData && <DataTable points={points} xName={xf.name} measure={measure} />}
        </div>
      )}
    </div>
  );
}

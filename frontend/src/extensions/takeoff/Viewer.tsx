import { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent as RMouseEvent, type PointerEvent as RPointerEvent } from "react";
import { Icon } from "../../components/Icon";
import { feet, polyArea, polyLength, tk, type Item, type Mark, type Pt, type Rect, type Sheet } from "./tk";

/** A sheet with the takeoff drawn over it. The sheet is laid out in PDF points (1 pt = 1 px at 100% zoom); its
 * picture comes from the server at a resolution that suits the zoom. */

export type Tool = "pan" | "count" | "line" | "area" | "calibrate" | "symbol" | "legend" | "exclude";

const DPIS = [50, 100, 150, 200, 300];
const BOX_TOOLS: Tool[] = ["symbol", "legend", "exclude"];

export function Viewer({ projectId, sheet, marks, items, selectedItem, tool, selectedMark, search, focus, hidden, showRejected,
  onPoint, onShape, onRect, onCalibrate, onMarkClick, onCancelTool }: {
  projectId: string; sheet: Sheet; marks: Mark[]; items: Map<string, Item>; selectedItem: Item | null; tool: Tool;
  selectedMark: string | null; search: Rect[]; focus: { point: Pt; key: number } | null; hidden: Set<string>; showRejected: boolean;
  onPoint: (p: Pt) => void; onShape: (type: "line" | "area", pts: Pt[]) => void; onRect: (tool: Tool, r: Rect) => void;
  onCalibrate: (pts: Pt[]) => void; onMarkClick: (m: Mark, at: { x: number; y: number }) => void; onCancelTool: () => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ z: 0.3, x: 0, y: 0 });
  const [pending, setPending] = useState<Pt[]>([]);
  const [hover, setHover] = useState<Pt | null>(null);
  const [box, setBox] = useState<{ a: Pt; b: Pt } | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);
  const ftPerPt = sheet.scale?.ft_per_pt ?? null;

  const fit = useCallback(() => {
    const el = wrap.current;
    if (!el) return;
    const z = Math.min(el.clientWidth / sheet.width, el.clientHeight / sheet.height) * 0.98;
    setView({ z, x: (el.clientWidth - sheet.width * z) / 2, y: (el.clientHeight - sheet.height * z) / 2 });
  }, [sheet.width, sheet.height]);
  useLayoutEffect(() => { fit(); setPending([]); setBox(null); }, [sheet.page_no, fit]);
  useEffect(() => { setPending([]); setBox(null); }, [tool]);

  // go to a place (a find, a search hit)
  useEffect(() => {
    const el = wrap.current;
    if (!focus || !el) return;
    setView((v) => {
      const z = Math.max(v.z, 1.2);
      return { z, x: el.clientWidth / 2 - focus.point[0] * z, y: el.clientHeight / 2 - focus.point[1] * z };
    });
  }, [focus?.key]);

  const toPage = (cx: number, cy: number): Pt => {
    const r = wrap.current!.getBoundingClientRect();
    return [(cx - r.left - view.x) / view.z, (cy - r.top - view.y) / view.z];
  };
  const zoomAt = (factor: number, cx?: number, cy?: number) => {
    const el = wrap.current!;
    const r = el.getBoundingClientRect();
    const px = cx ?? r.left + el.clientWidth / 2, py = cy ?? r.top + el.clientHeight / 2;
    setView((v) => {
      const z = Math.min(Math.max(v.z * factor, 0.05), 8);
      const k = z / v.z;
      return { z, x: px - r.left - (px - r.left - v.x) * k, y: py - r.top - (py - r.top - v.y) * k };
    });
  };
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => { e.preventDefault(); zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX, e.clientY); };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  // straight lines with Shift: 0 / 45 / 90 degrees from the last point
  const snap = (p: Pt, shift: boolean): Pt => {
    const last = pending[pending.length - 1];
    if (!shift || !last) return p;
    const dx = p[0] - last[0], dy = p[1] - last[1];
    const a = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
    const d = Math.hypot(dx, dy);
    return [last[0] + Math.cos(a) * d, last[1] + Math.sin(a) * d];
  };

  const finish = useCallback(() => {
    if (tool === "line" && pending.length >= 2) onShape("line", pending);
    if (tool === "area" && pending.length >= 3) onShape("area", pending);
    setPending([]);
  }, [tool, pending, onShape]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.key === "Escape") { if (pending.length || box) { setPending([]); setBox(null); } else onCancelTool(); }
      if (e.key === "Enter") finish();
      if (e.key === "Backspace" && pending.length) { e.preventDefault(); setPending((p) => p.slice(0, -1)); }
      if (e.key === "+" || e.key === "=") zoomAt(1.25);
      if (e.key === "-") zoomAt(0.8);
      if (e.key === "0") fit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const down = (e: RPointerEvent) => {
    if (e.button === 2) return;
    const p = toPage(e.clientX, e.clientY);
    const panning = tool === "pan" || e.button === 1 || e.altKey;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    if (!panning && BOX_TOOLS.includes(tool)) setBox({ a: p, b: p });
  };
  const move = (e: RPointerEvent) => {
    const p = toPage(e.clientX, e.clientY);
    setHover(snap(p, e.shiftKey));
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 4) d.moved = true;
    if (box) { setBox({ a: box.a, b: p }); return; }
    if (tool === "pan" || e.buttons === 4 || e.altKey || (d.moved && !["line", "area", "calibrate"].includes(tool))) {
      setView((v) => ({ ...v, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y }));
    }
  };
  const up = (e: RPointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (box) {
      const r: Rect = [Math.min(box.a[0], box.b[0]), Math.min(box.a[1], box.b[1]), Math.max(box.a[0], box.b[0]), Math.max(box.a[1], box.b[1])];
      setBox(null);
      if (r[2] - r[0] > 3 && r[3] - r[1] > 3) onRect(tool, r);
      return;
    }
    if (!d || d.moved || tool === "pan") return;
    const p = snap(toPage(e.clientX, e.clientY), e.shiftKey);
    if (tool === "count") onPoint(p);
    if (tool === "line" || tool === "area") setPending((x) => [...x, p]);
    if (tool === "calibrate") {
      const next = [...pending, p];
      if (next.length === 2) { setPending([]); onCalibrate(next); } else setPending(next);
    }
  };

  // the picture: a quick low-resolution one, and a sharper one on top for the zoom
  const dpr = window.devicePixelRatio || 1;
  const want = 72 * view.z * dpr;
  const dpi = DPIS.find((x) => x >= want) ?? 300;
  const r = 9 / view.z;      // marks keep their size on screen
  const sw = 1.6 / view.z;
  const live = pending.length && hover ? [...pending, hover] : pending;
  const liveLen = tool === "line" || tool === "calibrate" ? polyLength(live) : 0;
  const liveArea = tool === "area" && live.length >= 3 ? polyArea(live) : 0;

  return (
    <div className={`tk-viewer tool-${tool}`} ref={wrap} onPointerDown={down} onPointerMove={move} onPointerUp={up}
      onPointerLeave={() => setHover(null)} onDoubleClick={() => (tool === "line" || tool === "area") && finish()} onContextMenu={(e) => e.preventDefault()}>
      <div className="tk-sheet" style={{ width: sheet.width, height: sheet.height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.z})` }}>
        <img className="tk-img" src={tk.image(projectId, sheet.page_no, 50)} alt="" draggable={false} />
        {dpi > 50 && <img className="tk-img" src={tk.image(projectId, sheet.page_no, dpi)} alt={sheet.sheet_no ?? ""} draggable={false} />}
        <svg className="tk-overlay" viewBox={`0 0 ${sheet.width} ${sheet.height}`} width={sheet.width} height={sheet.height}>
          <defs>
            <pattern id="tk-hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="8" stroke="#888" strokeWidth="2" />
            </pattern>
          </defs>
          {sheet.excluded.map((e, i) => (
            <rect key={`x${i}`} className="tk-excluded" x={e[0]} y={e[1]} width={e[2] - e[0]} height={e[3] - e[1]} />
          ))}
          {selectedItem?.symbol && selectedItem.symbol.page === sheet.page_no && (
            <rect className="tk-symbol-box" x={selectedItem.symbol.rect[0]} y={selectedItem.symbol.rect[1]} strokeWidth={sw}
              width={selectedItem.symbol.rect[2] - selectedItem.symbol.rect[0]} height={selectedItem.symbol.rect[3] - selectedItem.symbol.rect[1]} />
          )}
          {search.map((s, i) => <rect key={`s${i}`} className="tk-hit" x={s[0] - 1} y={s[1] - 1} width={s[2] - s[0] + 2} height={s[3] - s[1] + 2} />)}
          {marks.map((m) => {
            const it = items.get(m.item_id);
            if (!it || hidden.has(m.item_id) || (m.status === "rejected" && !showRejected)) return null;
            const cls = `tk-mark ${m.status} ${m.id === selectedMark ? "selected" : ""} ${selectedItem?.id === m.item_id ? "of-item" : ""}`;
            const click = (e: RMouseEvent) => { e.stopPropagation(); onMarkClick(m, { x: e.clientX, y: e.clientY }); };
            const stop = (e: RPointerEvent) => { if (tool === "pan" || tool === "count") e.stopPropagation(); };
            const pts = m.geom.points;
            if (m.geom.type === "point") {
              return (
                <g key={m.id} className={cls} onPointerDown={stop} onPointerUp={(e) => e.stopPropagation()} onClick={click} style={{ color: it.color }}>
                  <circle cx={pts[0][0]} cy={pts[0][1]} r={r} strokeWidth={sw} />
                  {m.status === "rejected" && <path d={`M${pts[0][0] - r * 0.6},${pts[0][1] - r * 0.6}l${r * 1.2},${r * 1.2}m0,${-r * 1.2}l${-r * 1.2},${r * 1.2}`} strokeWidth={sw} />}
                </g>
              );
            }
            const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join("") + (m.geom.type === "area" ? "Z" : "");
            return (
              <g key={m.id} className={`${cls} ${m.geom.type}`} onPointerDown={stop} onPointerUp={(e) => e.stopPropagation()} onClick={click} style={{ color: it.color }}>
                <path d={d} strokeWidth={m.geom.type === "line" ? 3 / view.z : sw} />
                <path d={d} className="tk-hitpath" strokeWidth={12 / view.z} />
              </g>
            );
          })}
          {live.length > 0 && (
            <g className="tk-live" style={{ color: tool === "calibrate" ? "#0091ff" : selectedItem?.color ?? "#0091ff" }}>
              <path d={live.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join("") + (tool === "area" && live.length > 2 ? "Z" : "")}
                strokeWidth={2 / view.z} className={tool === "area" ? "area" : ""} />
              {pending.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={3 / view.z} />)}
            </g>
          )}
          {box && <rect className="tk-drag-box" x={Math.min(box.a[0], box.b[0])} y={Math.min(box.a[1], box.b[1])} strokeWidth={sw}
            width={Math.abs(box.b[0] - box.a[0])} height={Math.abs(box.b[1] - box.a[1])} />}
        </svg>
      </div>
      {hover && (liveLen > 0 || liveArea > 0) && (
        <div className="tk-readout">
          {tool === "calibrate" ? `${(liveLen / 72).toFixed(2)}" on the sheet`
            : ftPerPt ? (liveArea ? `${(liveArea * ftPerPt * ftPerPt).toFixed(1)} SF` : feet(liveLen * ftPerPt)) : "No scale on this sheet"}
        </div>
      )}
      <div className="tk-zoom">
        <button className="icon-btn" onClick={() => zoomAt(0.8)} aria-label="Zoom out" title="Zoom out (−)"><Icon name="zoom-out" size={15} /></button>
        <span>{Math.round(view.z * 100)}%</span>
        <button className="icon-btn" onClick={() => zoomAt(1.25)} aria-label="Zoom in" title="Zoom in (+)"><Icon name="zoom-in" size={15} /></button>
        <button className="icon-btn" onClick={fit} aria-label="Fit the sheet" title="Fit the sheet (0)"><Icon name="fit" size={15} /></button>
      </div>
    </div>
  );
}

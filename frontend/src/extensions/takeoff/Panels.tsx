import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { Icon } from "../../components/Icon";
import { Modal, Popover, usePopover } from "../../components/Overlay";
import type { Base } from "../../types";
import {
  qty, sheetName, tk, type Item, type Kind, type LegendEntry, type Mark, type Project, type Pt, type LibrarySymbol, type Rect, type Sheet, type SheetTable,
} from "./tk";
import type { Tool } from "./Viewer";

const DISCIPLINE_NAMES = ["Electrical", "Fire alarm", "Fire protection", "Life safety", "Mechanical", "Plumbing", "Architectural",
  "Demolition", "Structural", "Civil", "Interiors", "Landscape", "Telecom", "Audio / video", "Equipment", "General", "Title / general", "Specifications"];
const center = (r: Rect): Pt => [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2];

/** A piece of a sheet (a legend symbol). */
export function Crop({ projectId, sheet, rect, h = 30 }: { projectId: string; sheet: Pick<Sheet, "page_no">; rect: Rect; h?: number }) {
  return <img className="tk-crop" src={tk.crop(projectId, sheet.page_no, rect, h * 2)} alt="" style={{ height: h, maxWidth: h * 3 }} />;
}

// ---------------------------------------------------------------- sheets

export function SheetsPanel({ project, page, onPage, onChanged, onError, onCalibrate }: {
  project: Project; page: number; onPage: (n: number) => void; onChanged: () => void; onError: (e: unknown) => void; onCalibrate: () => void;
}) {
  const [filter, setFilter] = useState<string | null>(null);
  const groups = useMemo(() => {
    const m = new Map<string, number>();
    project.pages.forEach((s) => m.set(s.discipline ?? "Unknown", (m.get(s.discipline ?? "Unknown") ?? 0) + 1));
    return [...m.entries()];
  }, [project.pages]);
  const shown = project.pages.filter((s) => !filter || (s.discipline ?? "Unknown") === filter);
  const patch = (s: Sheet, p: Parameters<typeof tk.page>[2]) => tk.page(project.id, s.page_no, p).then(onChanged).catch(onError);
  const bulk = (included: boolean) => tk.pages(project.id, shown.map((s) => s.page_no), { included }).then(onChanged).catch(onError);
  const unchecked = project.pages.filter((s) => s.included && !s.confirmed).length;

  return (
    <div className="tk-pane">
      <p className="muted small">Choose the sheets for this takeoff and check what was read from each one: its number, title and scale.
        Tick <Icon name="check" size={11} /> when a sheet is right.</p>
      <div className="tk-chips">
        <button className={`chip ${filter === null ? "on" : ""}`} onClick={() => setFilter(null)}>All · {project.pages.length}</button>
        {groups.map(([d, n]) => <button key={d} className={`chip ${filter === d ? "on" : ""}`} onClick={() => setFilter(filter === d ? null : d)}>{d} · {n}</button>)}
      </div>
      <div className="row tk-bulk">
        <button className="btn sm" onClick={() => bulk(true)}><Icon name="plus" size={12} /> Include {filter ? filter.toLowerCase() : "all"}</button>
        <button className="btn sm" onClick={() => bulk(false)}>Leave {filter ? "these" : "all"} out</button>
      </div>
      {unchecked > 0 && <p className="tk-warn small">{unchecked} sheet{unchecked === 1 ? "" : "s"} in the takeoff not checked yet</p>}
      <div className="tk-sheet-list">
        {shown.map((s) => (
          <div key={s.page_no} className={`tk-sheet-row ${s.page_no === page ? "active" : ""} ${s.included ? "in" : ""}`} onClick={() => onPage(s.page_no)}>
            <input type="checkbox" checked={s.included} title="In the takeoff" aria-label={`${sheetName(s)} in the takeoff`}
              onClick={(e) => e.stopPropagation()} onChange={(e) => patch(s, { included: e.target.checked })} />
            <div className="tk-sheet-fields" onClick={(e) => s.page_no === page && e.stopPropagation()}>
              <div className="row">
                <input className="input sm tk-sheet-no" defaultValue={s.sheet_no ?? ""} placeholder={`p. ${s.page_no + 1}`} aria-label="Sheet number"
                  onBlur={(e) => e.target.value !== (s.sheet_no ?? "") && patch(s, { sheet_no: e.target.value })} />
                <input className="input sm" defaultValue={s.title ?? ""} placeholder="Title" aria-label="Sheet title"
                  onBlur={(e) => e.target.value !== (s.title ?? "") && patch(s, { title: e.target.value })} />
              </div>
              <div className="row">
                <select className="input sm" value={s.discipline ?? ""} aria-label="Discipline" onChange={(e) => patch(s, { discipline: e.target.value })}>
                  <option value="">Discipline?</option>
                  {DISCIPLINE_NAMES.map((d) => <option key={d}>{d}</option>)}
                </select>
                <ScalePick sheet={s} onPick={(sc) => sc === "calibrate" ? (onPage(s.page_no), onCalibrate()) : patch(s, { scale: sc })} />
              </div>
              {!s.has_text && <span className="tk-warn small">No text on this sheet (scanned?): labels and scales can't be read; count and calibrate by hand.</span>}
            </div>
            <button className={`icon-btn tk-ok ${s.confirmed ? "on" : ""}`} title={s.confirmed ? "Checked" : "Mark as checked"} aria-label="Checked"
              onClick={(e) => { e.stopPropagation(); patch(s, { confirmed: !s.confirmed }); }}><Icon name="check" size={14} /></button>
          </div>
        ))}
      </div>
    </div>
  );
}

function ScalePick({ sheet, onPick }: { sheet: Sheet; onPick: (s: { ft_per_pt: number; label: string; source: string } | Record<string, never> | "calibrate") => void }) {
  const options = [...sheet.scales];
  if (sheet.scale && !options.some((o) => o.label === sheet.scale!.label)) options.unshift(sheet.scale);
  return (
    <select className="input sm" aria-label="Scale" value={sheet.scale?.label ?? ""} title="The plan's scale (lengths and areas use it)"
      onChange={(e) => {
        const v = e.target.value;
        if (v === "__cal") onPick("calibrate");
        else if (!v) onPick({});
        else { const o = options.find((x) => x.label === v)!; onPick({ ft_per_pt: o.ft_per_pt, label: o.label, source: "text" }); }
      }}>
      <option value="">No scale</option>
      {options.map((o) => <option key={o.label} value={o.label}>{o.label}</option>)}
      <option value="__cal">Calibrate on the sheet…</option>
    </select>
  );
}

// ---------------------------------------------------------------- items

export function ItemsPanel({ project, page, marks, item, onItem, onChanged, onError, onTool, onLegend, onGo, hidden, onToggleHidden }: {
  project: Project; page: number; marks: Mark[]; item: Item | null; onItem: (i: Item | null) => void; onChanged: () => void;
  onError: (e: unknown) => void; onTool: (t: Tool) => void; onLegend: (l: LegendGroup) => void; onGo: (page: number, pt?: Pt) => void;
  hidden: Set<string>; onToggleHidden: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const sum = useMemo(() => new Map(project.summary.map((s) => [s.item_id, s])), [project.summary]);
  const [progress, setProgress] = useState("");
  const [report, setReport] = useState<{ id: string; name: string; found: number; error?: string }[] | null>(null);
  const findable = project.items.filter((i) => i.status === "confirmed" && i.kind === "count" && (i.tag || i.symbol));
  const findAll = async () => {
    const sheets = project.pages.filter((s) => s.included);
    if (!sheets.length) { onError(new Error("Include sheets in the takeoff first (Sheets tab)")); return; }
    const out: { id: string; name: string; found: number; error?: string }[] = [];
    for (const [k, it] of findable.entries()) {
      const row: { id: string; name: string; found: number; error?: string } = { id: it.id, name: it.name, found: 0 };
      for (const s of sheets) {
        setProgress(`${it.name} on ${sheetName(s)} (item ${k + 1} of ${findable.length})…`);
        try { row.found += (await tk.find(it.id, { method: it.tag ? "tag" : "symbol", pages: [s.page_no] })).found; }
        catch (e) { row.error = (e as Error).message; break; }
      }
      out.push(row);
    }
    setProgress(""); setReport(out); onChanged();
  };
  const findLegends = async () => {
    setBusy(true);
    try {
      const found = await tk.legends(project.id, page);
      if (!found.length) onError(new Error("No legend or schedule found on this sheet as a table: box the legend instead (the list tool)"));
      else onLegend({ page, groups: found.map((l) => ({ heading: l.heading, rect: l.rect, rows: l.rows })) });
    } catch (e) { onError(e); }
    setBusy(false);
  };
  const addItem = async (kind: Kind) => {
    const name = window.prompt(kind === "count" ? "Name of what to count (e.g. Duplex receptacle)" : kind === "length" ? "Name of the run (e.g. 3/4\" EMT, Wall type B-2)" : "Name of the area (e.g. LVP flooring)");
    if (!name?.trim()) return;
    try { const [i] = await tk.addItems(project.id, [{ name: name.trim(), kind }]); onChanged(); onItem(i); } catch (e) { onError(e); }
  };
  const cats = useMemo(() => {
    const m = new Map<string, Item[]>();
    project.items.filter((i) => i.status === "confirmed").forEach((i) => { const c = i.category || "Items"; m.set(c, [...(m.get(c) ?? []), i]); });
    return [...m.entries()];
  }, [project.items]);

  if (item) return <ItemDetail key={item.id} project={project} page={page} marks={marks} item={item} summary={sum.get(item.id)} onBack={() => onItem(null)}
    onChanged={onChanged} onError={onError} onTool={onTool} onGo={onGo} />;
  return (
    <div className="tk-pane">
      <p className="muted small">Items are what you take off. Read them from a sheet's legend or schedule, or add them yourself.</p>
      <div className="tk-actions">
        <button className="btn sm primary" disabled={busy} onClick={findLegends}><Icon name="sparkle" size={13} /> {busy ? "Reading…" : "Read legends on this sheet"}</button>
        <button className="btn sm" onClick={() => onTool("legend")} title="Drag a box around a legend on the sheet"><Icon name="boxselect" size={13} /> Box a legend</button>
      </div>
      <div className="tk-actions">
        <button className="btn sm" onClick={() => addItem("count")}><Icon name="target" size={13} /> Item to count</button>
        <button className="btn sm" onClick={() => addItem("length")}><Icon name="polyline" size={13} /> Run</button>
        <button className="btn sm" onClick={() => addItem("area")}><Icon name="area" size={13} /> Area</button>
      </div>
      {findable.length > 0 && (
        <div className="tk-findall">
          <button className="btn sm" disabled={!!progress} onClick={findAll} title="Each item by its label, or its symbol when it has no label, on every sheet in the takeoff">
            <Icon name="search" size={13} /> {progress || `Find all ${findable.length} items on the takeoff's sheets`}</button>
          {report && (
            <div className="tk-report">
              {report.map((r) => (
                <div key={r.id} className={r.error ? "err" : ""}><b>{r.name}</b>: {r.error ?? `${r.found} found`}</div>
              ))}
              <button className="link-btn small" onClick={() => setReport(null)}>Close</button>
            </div>
          )}
        </div>
      )}
      {!cats.length && <p className="muted tk-empty-note">No items yet.</p>}
      {cats.map(([cat, list]) => (
        <div key={cat} className="tk-cat">
          <div className="tk-cat-head">{cat}</div>
          {list.map((i) => {
            const s = sum.get(i.id);
            return (
              <div key={i.id} className="tk-item-row" onClick={() => onItem(i)}>
                <button className="icon-btn" title={hidden.has(i.id) ? "Show on the sheets" : "Hide on the sheets"} aria-label="Show or hide"
                  onClick={(e) => { e.stopPropagation(); onToggleHidden(i.id); }}><Icon name="eye" size={13} className={hidden.has(i.id) ? "off" : ""} /></button>
                <i className="tk-dot" style={{ background: i.color }} />
                {i.symbol && <img className="tk-sym" src={tk.symbolUrl(i.id)} alt="" />}
                <span className="tk-item-name">{i.tag && <span className="tk-tag">{i.tag}</span>}{i.name}</span>
                <span className="tk-item-qty">{s ? qty(s.qty, i.unit) : `0 ${i.unit}`}
                  {s && s.pending > 0 && <span className="tk-pending" title="Suggestions to review">{s.pending}?</span>}</span>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function ItemDetail({ project, page, marks, item, summary, onBack, onChanged, onError, onTool, onGo }: {
  project: Project; page: number; marks: Mark[]; item: Item; summary?: Project["summary"][number]; onBack: () => void; onChanged: () => void;
  onError: (e: unknown) => void; onTool: (t: Tool) => void; onGo: (page: number, pt?: Pt) => void;
}) {
  const [method, setMethod] = useState<"tag" | "symbol">(item.tag ? "tag" : "symbol");
  const [threshold, setThreshold] = useState(0.75);
  const [scope, setScope] = useState<"included" | "page">("included");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  useEffect(() => { if (!item.tag && item.symbol) setMethod("symbol"); if (item.tag && !item.symbol) setMethod("tag"); }, [item.tag, !!item.symbol]);
  const save = (p: Partial<Item>) => tk.item(item.id, p).then(onChanged).catch(onError);
  const sheetOf = (n: number) => project.pages.find((s) => s.page_no === n);
  const here = marks.filter((m) => m.item_id === item.id);
  const pendingHere = here.filter((m) => m.status === "suggested");

  const find = async () => {
    // one sheet at a time (a sheet takes a few seconds by symbol): progress shows, and no request runs long
    const pages = scope === "page" ? [page] : project.pages.filter((s) => s.included).map((s) => s.page_no);
    if (!pages.length) { setResult("No sheets in the takeoff yet: include sheets on the Sheets tab"); return; }
    setBusy(true); setResult("");
    const found: string[] = [];
    let total = 0;
    try {
      for (const [i, n] of pages.entries()) {
        setResult(`Looking on ${sheetName(sheetOf(n)!)} (${i + 1} of ${pages.length})…`);
        const r = await tk.find(item.id, { method, threshold, pages: [n] });
        total += r.found;
        if (r.found) found.push(`${sheetName(sheetOf(n)!)} ${r.found}`);
      }
      setResult(total ? `Found ${total}: ${found.join(", ")}. Review them on the sheets.`
        : `Nothing found${method === "symbol" ? ": try a lower likeness, or box the symbol again tightly" : ""}.`);
      onChanged();
    } catch (e) { setResult((e as Error).message); }
    setBusy(false);
  };
  const review = (status: "confirmed" | "rejected", body: { page_no?: number; min_score?: number }) =>
    tk.review(project.id, { item_id: item.id, status, ...body }).then((r) => { setResult(`${r.changed} ${status === "confirmed" ? "accepted" : "rejected"}`); onChanged(); }).catch(onError);
  const next = async () => {
    const all = (await tk.marks(project.id)).filter((m) => m.item_id === item.id && m.status === "suggested")
      .sort((a, b) => (a.page_no - b.page_no) || ((b.score ?? 1) - (a.score ?? 1)));
    const m = all.find((x) => x.page_no >= page) ?? all[0];
    if (m) onGo(m.page_no, m.geom.points[0]); else setResult("Nothing left to review");
  };

  return (
    <div className="tk-pane tk-detail">
      <button className="link-btn" onClick={onBack}><Icon name="left" size={12} /> All items</button>
      <div className="row tk-detail-head">
        <input type="color" value={item.color} onChange={(e) => save({ color: e.target.value })} aria-label="Color" />
        <input className="input" defaultValue={item.name} aria-label="Name" onBlur={(e) => e.target.value.trim() && e.target.value !== item.name && save({ name: e.target.value })} />
      </div>
      {summary && <div className="tk-detail-qty"><b>{qty(summary.qty, item.unit)}</b>{summary.waste_pct ? <span className="muted"> · {qty(summary.total, item.unit)} with waste</span> : null}
        {summary.pending > 0 && <span className="tk-pending">{summary.pending} to review</span>}</div>}
      <div className="tk-grid2">
        <label className="stack small">Label on drawings<input className="input sm" defaultValue={item.tag ?? ""} placeholder="e.g. A, EM-2"
          onBlur={(e) => e.target.value !== (item.tag ?? "") && save({ tag: e.target.value })} /></label>
        <label className="stack small">Category<input className="input sm" defaultValue={item.category ?? ""} placeholder="Lighting…"
          onBlur={(e) => e.target.value !== (item.category ?? "") && save({ category: e.target.value })} /></label>
        <label className="stack small">Kind<select className="input sm" value={item.kind} onChange={(e) => save({ kind: e.target.value as Kind })}>
          <option value="count">Count</option><option value="length">Length</option><option value="area">Area</option></select></label>
        <label className="stack small">Unit<input className="input sm" defaultValue={item.unit} onBlur={(e) => e.target.value !== item.unit && save({ unit: e.target.value })} /></label>
        <label className="stack small">Waste %<input className="input sm" type="number" min={0} step={1} defaultValue={item.waste_pct}
          onBlur={(e) => Number(e.target.value) !== item.waste_pct && save({ waste_pct: Number(e.target.value) || 0 })} /></label>
        <label className="stack small">Unit cost<input className="input sm" type="number" min={0} step={0.01} defaultValue={item.unit_cost ?? ""}
          onBlur={(e) => save({ unit_cost: e.target.value === "" ? null : Number(e.target.value) })} /></label>
        {item.kind === "length" && <label className="stack small" title="Added to every run: drops, makeup, home runs">Add per run (ft)
          <input className="input sm" type="number" min={0} step={1} defaultValue={item.per_run} onBlur={(e) => save({ per_run: Number(e.target.value) || 0 })} /></label>}
      </div>
      {Object.keys(item.details ?? {}).length > 0 && (
        <dl className="tk-details">{Object.entries(item.details).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      )}

      {item.kind === "count" ? (<>
        <h4>Find it on the sheets</h4>
        <div className="tk-symbol">
          {item.symbol ? <img src={tk.symbolUrl(item.id, item.symbol.rect[0])} alt="Symbol" /> : <span className="muted small">No symbol</span>}
          <button className="btn sm" onClick={() => onTool("symbol")} title="Drag a tight box around the symbol in the legend"><Icon name="boxselect" size={13} /> {item.symbol ? "Box it again" : "Box its symbol"}</button>
          <SymbolPicker project={project} item={item} onPicked={onChanged} onError={onError} />
        </div>
        <div className="tk-find">
          <label className="inline-check small"><input type="radio" checked={method === "tag"} disabled={!item.tag} onChange={() => setMethod("tag")} /> By its label{item.tag ? ` “${item.tag}”` : ""}</label>
          <label className="inline-check small"><input type="radio" checked={method === "symbol"} disabled={!item.symbol} onChange={() => setMethod("symbol")} /> By its symbol</label>
          {method === "symbol" && <label className="stack small">Likeness {Math.round(threshold * 100)}% <input type="range" min={0.5} max={0.95} step={0.01} value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))} /></label>}
          <select className="input sm" value={scope} onChange={(e) => setScope(e.target.value as "included" | "page")} aria-label="Where">
            <option value="included">On the sheets in the takeoff</option><option value="page">On this sheet</option>
          </select>
          <button className="btn sm primary" disabled={busy || (method === "tag" ? !item.tag : !item.symbol)} onClick={find}>
            <Icon name="search" size={13} /> {busy ? "Looking…" : "Find"}</button>
        </div>
      </>) : <p className="muted small">Measure it on the sheets: {item.kind === "length" ? "click along the run, double-click to finish" : "click around the area, double-click to finish"}.</p>}
      {result && <p className="tk-result small">{result}</p>}

      <h4>Review</h4>
      <p className="muted small">On this sheet: {here.filter((m) => m.status === "confirmed").length} in the takeoff, {pendingHere.length} suggested.
        Click a mark to accept (A) or reject (R) it.</p>
      <div className="tk-actions">
        <button className="btn sm" onClick={next}><Icon name="right" size={13} /> Next to review</button>
        <button className="btn sm" disabled={!pendingHere.length} onClick={() => review("confirmed", { page_no: page })}><Icon name="check" size={13} /> Accept all on sheet</button>
        <button className="btn sm" disabled={!pendingHere.length} onClick={() => review("rejected", { page_no: page })}><Icon name="ban" size={13} /> Reject the rest</button>
        {summary && summary.pending > 0 && <button className="btn sm" onClick={() => review("confirmed", { min_score: 0.9 })} title="Every suggestion of this item at least 90% alike, on every sheet">
          Accept all ≥ 90%</button>}
      </div>
      <div className="tk-actions">
        {item.kind === "count" && <button className="btn sm" onClick={() => onTool("count")}><Icon name="target" size={13} /> Count by clicking</button>}
        {item.kind === "length" && <button className="btn sm" onClick={() => onTool("line")}><Icon name="polyline" size={13} /> Measure runs</button>}
        {item.kind === "area" && <button className="btn sm" onClick={() => onTool("area")}><Icon name="area" size={13} /> Measure areas</button>}
      </div>
      {summary && summary.pages.length > 0 && (
        <div className="tk-by-sheet">
          {summary.pages.map((pg) => (
            <button key={pg.page_no} className="tk-by-sheet-row" onClick={() => onGo(pg.page_no)}>
              <span>{pg.sheet_no ?? `p. ${pg.page_no + 1}`}</span><span>{qty(pg.qty, item.unit)}</span>
            </button>
          ))}
        </div>
      )}
      {summary?.unscaled.length ? <p className="tk-warn small">No scale on {summary.unscaled.join(", ")}: set it on the Sheets tab.</p> : null}
      <label className="stack small">Notes<textarea className="input" rows={2} defaultValue={item.notes ?? ""} onBlur={(e) => e.target.value !== (item.notes ?? "") && save({ notes: e.target.value })} /></label>
      <button className="btn sm danger-ghost" onClick={() => window.confirm(`Delete ${item.name} and its marks?`) && tk.removeItem(item.id).then(() => { onBack(); onChanged(); }).catch(onError)}>
        <Icon name="trash" size={13} /> Delete item</button>
      {sheetOf(page) && !sheetOf(page)!.included && <p className="tk-warn small">This sheet isn't in the takeoff: finds look on the takeoff's sheets.</p>}
    </div>
  );
}

/** Give an item a symbol from the legends (or replace it): every legend picture on the sheets, searchable. */
function SymbolPicker({ project, item, onPicked, onError }: { project: Project; item: Item; onPicked: () => void; onError: (e: unknown) => void }) {
  const menu = usePopover();
  const [all, setAll] = useState(false);
  const [list, setList] = useState<LibrarySymbol[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    if (!menu.anchor) return;
    setList(null);
    tk.symbols(project.id, all).then(setList).catch(onError);
  }, [menu.anchor, all]);
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (list ?? []).filter((s) => words.every((w) => `${s.name} ${s.tag ?? ""} ${s.legend ?? ""} ${s.sheet_no ?? ""}`.toLowerCase().includes(w)));
  const pick = async (s: LibrarySymbol) => {
    menu.close();
    try {
      await tk.item(item.id, { symbol: { page: s.page, rect: s.rect, legend: s.legend_rect }, ...(!item.tag && s.tag ? { tag: s.tag } : {}),
        ...(Object.keys(item.details ?? {}).length || !Object.keys(s.details).length ? {} : { details: s.details }) });
      onPicked();
    } catch (e) { onError(e); }
  };
  return (<>
    <button className="btn sm" onClick={menu.open} aria-haspopup="listbox"><Icon name="list" size={13} /> Choose from legends ▾</button>
    {menu.anchor && (
      <Popover anchor={menu.anchor} onClose={menu.close} width={360}>
        <div className="tk-sympick">
          <input className="input sm" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search symbols: receptacle, EM, SD…" autoFocus aria-label="Search symbols" />
          <label className="inline-check small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Legends on every sheet (not only the takeoff's)</label>
          <div className="tk-sympick-list" role="listbox">
            {list === null ? <p className="muted small">Reading the legends…</p> : !shown.length ? <p className="muted small">{list.length ? "No symbol matches." : "No legend symbols found: include the sheet with the legend, or box the symbol on the sheet."}</p>
              : shown.slice(0, 200).map((s, i) => (
                <button key={i} className="tk-sympick-row" role="option" onClick={() => pick(s)}>
                  <Crop projectId={project.id} sheet={{ page_no: s.page }} rect={s.rect} h={26} />
                  <span className="tk-sympick-name">{s.tag && <span className="tk-tag">{s.tag}</span>}{s.name}</span>
                  <span className="muted small">{s.sheet_no ?? `p. ${s.page + 1}`}</span>
                </button>
              ))}
          </div>
        </div>
      </Popover>
    )}
  </>);
}

// ---------------------------------------------------------------- legend → items

export interface LegendGroup { page: number; groups: { heading: string | null; rect: Rect | null; rows: LegendEntry[] }[] }

export function LegendReview({ project, legend, onClose, onAdded, onError }: {
  project: Project; legend: LegendGroup; onClose: () => void; onAdded: (items: Item[]) => void; onError: (e: unknown) => void;
}) {
  const sheet = project.pages.find((s) => s.page_no === legend.page)!;
  const known = new Set(project.items.map((i) => `${i.tag ?? ""}|${i.name}`));
  type Row = LegendEntry & { on: boolean; kind: Kind; group: number };
  const [rows, setRows] = useState<Row[]>(() => legend.groups.flatMap((g, gi) => g.rows.map((r) => ({
    ...r, category: r.category ?? g.heading, on: !known.has(`${r.tag ?? ""}|${r.name}`), group: gi,
    kind: (/WALL|PARTITION|CONDUIT|PIPE|DUCT|WIRE|CABLE|TRACK/i.test(r.name) && !r.tag ? "length" : "count") as Kind,
  }))));
  const [exclude, setExclude] = useState(true);
  const [busy, setBusy] = useState(false);
  const set = (i: number, p: Partial<Row>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...p } : r)));
  const chosen = rows.filter((r) => r.on && r.name.trim());
  const add = async () => {
    setBusy(true);
    try {
      const items = await tk.addItems(project.id, chosen.map((r) => {
        const g = legend.groups[r.group];
        return { name: r.name.trim(), tag: r.tag, category: r.category, kind: r.kind, details: r.details,
          symbol: r.symbol ? { page: legend.page, rect: r.symbol, ...(g.rect ? { legend: g.rect } : {}) } : null };
      }));
      // the legends themselves are left out of automatic finds (visible, and removable, on the sheet)
      const rects = legend.groups.map((g) => g.rect).filter((r): r is Rect => !!r)
        .filter((r) => !sheet.excluded.some((e) => e.every((v, k) => Math.abs(v - r[k]) < 1)));
      if (exclude && rects.length) await tk.page(project.id, legend.page, { excluded: [...sheet.excluded, ...rects] });
      onAdded(items);
    } catch (e) { onError(e); }
    setBusy(false);
  };
  return (
    <Modal title={`Legend entries on ${sheetName(sheet)}`} onClose={onClose} width={720}
      footer={<>
        <label className="inline-check small"><input type="checkbox" checked={exclude} onChange={(e) => setExclude(e.target.checked)} /> Leave the legend out of finds</label>
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !chosen.length} onClick={add}>{busy ? "Adding…" : `Add ${chosen.length} item${chosen.length === 1 ? "" : "s"}`}</button>
      </>}>
      <p className="muted small">Check what was read, correct it, and choose what to take off. The label is the text on the plan next to each one
        (a fixture type); the symbol is its picture. Either is used to find it on the sheets.</p>
      {legend.groups.map((g, gi) => (
        <div key={gi} className="tk-legend-group">
          <div className="row"><b>{g.heading ?? "Legend"}</b><span className="spacer" />
            <button className="link-btn small" onClick={() => setRows((rs) => rs.map((r) => (r.group === gi ? { ...r, on: true } : r)))}>All</button>
            <button className="link-btn small" onClick={() => setRows((rs) => rs.map((r) => (r.group === gi ? { ...r, on: false } : r)))}>None</button></div>
          <table className="tk-legend-table">
            <thead><tr><th /><th>Symbol</th><th>Label</th><th>Name</th><th>Kind</th></tr></thead>
            <tbody>
              {rows.map((r, i) => r.group !== gi ? null : (
                <tr key={i} className={r.on ? "" : "off"}>
                  <td><input type="checkbox" checked={r.on} onChange={(e) => set(i, { on: e.target.checked })} aria-label={`Add ${r.name}`} /></td>
                  <td>{r.symbol ? <Crop projectId={project.id} sheet={sheet} rect={r.symbol} /> : <span className="muted small">—</span>}</td>
                  <td><input className="input sm tk-tag-input" value={r.tag ?? ""} onChange={(e) => set(i, { tag: e.target.value || null })} /></td>
                  <td><input className="input sm" value={r.name} onChange={(e) => set(i, { name: e.target.value })} />
                    {Object.keys(r.details).length > 0 && <div className="muted small tk-legend-details">{Object.entries(r.details).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>}</td>
                  <td><select className="input sm" value={r.kind} onChange={(e) => set(i, { kind: e.target.value as Kind })}>
                    <option value="count">Count</option><option value="length">Length</option><option value="area">Area</option></select></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </Modal>
  );
}

// ---------------------------------------------------------------- search

export function SearchPanel({ project, items, state, onState, onGo, onChanged, onError }: {
  project: Project; items: Item[]; state: { q: string; hits: { page_no: number; rects: Rect[] }[] } | null;
  onState: (s: { q: string; hits: { page_no: number; rects: Rect[] }[] } | null) => void; onGo: (page: number, pt?: Pt) => void;
  onChanged: () => void; onError: (e: unknown) => void;
}) {
  const [q, setQ] = useState(state?.q ?? "");
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState("");
  const counted = items.filter((i) => i.status === "confirmed" && i.kind === "count");
  const run = async () => {
    if (!q.trim()) { onState(null); return; }
    setBusy(true);
    try { onState({ q: q.trim(), hits: await tk.search(project.id, q.trim(), all) }); } catch (e) { onError(e); }
    setBusy(false);
  };
  const total = state?.hits.reduce((n, h) => n + h.rects.length, 0) ?? 0;
  const countAs = async () => {
    if (!state || !target) return;
    if (!window.confirm(`Add ${total} marks to ${items.find((i) => i.id === target)?.name}?`)) return;
    try {
      for (const h of state.hits) for (const r of h.rects) await tk.addMark(project.id, { item_id: target, page_no: h.page_no, geom: { type: "point", points: [center(r)] }, source: "search" });
      onChanged();
    } catch (e) { onError(e); }
  };
  return (
    <div className="tk-pane">
      <form className="row" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Text on the sheets: GFCI, EM-2, WP…" autoFocus aria-label="Search the sheets" />
        <button className="btn" disabled={busy}><Icon name="search" size={14} /></button>
      </form>
      <label className="inline-check small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> All sheets (not only the takeoff's)</label>
      {state && (<>
        <p className="small">{total ? `${total} found on ${state.hits.length} sheet${state.hits.length === 1 ? "" : "s"}` : "Not found"}</p>
        {state.hits.map((h) => {
          const s = project.pages.find((x) => x.page_no === h.page_no)!;
          return (
            <div key={h.page_no} className="tk-hit-sheet">
              <button className="link-btn" onClick={() => onGo(h.page_no, center(h.rects[0]))}><b>{sheetName(s)}</b> {s.title}</button>
              <div className="tk-hit-list">{h.rects.slice(0, 60).map((r, i) => <button key={i} className="chip" onClick={() => onGo(h.page_no, center(r))}>{i + 1}</button>)}</div>
            </div>
          );
        })}
        {total > 0 && counted.length > 0 && (
          <div className="row tk-count-as">
            <select className="input sm" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="Item"><option value="">Count these as…</option>
              {counted.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}</select>
            <button className="btn sm" disabled={!target} onClick={countAs}>Add {total}</button>
          </div>
        )}
      </>)}
    </div>
  );
}

// ---------------------------------------------------------------- the takeoff

export function SummaryPanel({ project, page, onGo, onItem, onError, onWorkspaceChanged, onNote }: {
  project: Project; page: number; onGo: (page: number) => void; onItem: (id: string) => void; onError: (e: unknown) => void;
  onWorkspaceChanged: () => void; onNote: (m: string) => void;
}) {
  const [bases, setBases] = useState<Base[]>([]);
  const [base, setBase] = useState("");
  const [tables, setTables] = useState<SheetTable[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { api.get<{ bases: Base[] }>("/workspace").then((w) => { setBases(w.bases); setBase(w.bases[0]?.id ?? ""); }).catch(onError); }, []);
  const rows = project.summary;
  const cost = rows.reduce((n, r) => n + (r.extended ?? 0), 0);
  const pending = rows.reduce((n, r) => n + r.pending, 0);
  const toTable = async () => {
    try {
      const r = await tk.toTable(project.id, base, "");
      onWorkspaceChanged();
      onNote(`Table created with ${r.rows} rows`);
      location.hash = `/table/${r.table_id}`;
    } catch (e) { onError(e); }
  };
  const schedule = async (t: SheetTable) => {
    const name = window.prompt("Name of the new table", t.heading ?? "Schedule");
    if (name === null) return;
    try { const r = await tk.scheduleToTable(project.id, base, name, t.cells); onWorkspaceChanged(); onNote(`${name}: ${r.rows} rows copied into a table`); } catch (e) { onError(e); }
  };
  const byCat = new Map<string, typeof rows>();
  rows.forEach((r) => byCat.set(r.category || "Items", [...(byCat.get(r.category || "Items") ?? []), r]));
  return (
    <div className="tk-pane">
      {pending > 0 && <p className="tk-warn small">{pending} suggestion{pending === 1 ? "" : "s"} still to review: they aren't counted until accepted.</p>}
      {!rows.length ? <p className="muted">Nothing taken off yet.</p> : (
        <table className="tk-summary">
          <thead><tr><th>Item</th><th className="num">Qty</th><th className="num">+Waste</th><th className="num">Cost</th></tr></thead>
          {[...byCat.entries()].map(([cat, list]) => (
            <tbody key={cat}>
              <tr className="tk-sum-cat"><td colSpan={4}>{cat}</td></tr>
              {list.map((r) => (<Fragment key={r.item_id}>
                <tr className="tk-sum-row" onClick={() => setOpen(open === r.item_id ? null : r.item_id)}>
                  <td><i className="tk-dot" style={{ background: r.color }} />{r.tag && <span className="tk-tag">{r.tag}</span>}{r.name}
                    {r.pending > 0 && <span className="tk-pending">{r.pending}?</span>}{r.unscaled.length > 0 && <span className="tk-warn" title={`No scale on ${r.unscaled.join(", ")}`}> !</span>}</td>
                  <td className="num">{qty(r.qty, r.unit)}</td>
                  <td className="num">{r.waste_pct ? qty(r.total, r.unit) : "—"}</td>
                  <td className="num">{r.extended !== null ? `$${r.extended.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : ""}</td>
                </tr>
                {open === r.item_id && (
                  <tr className="tk-sum-detail"><td colSpan={4}>
                    {r.pages.map((pg) => <button key={pg.page_no} className="chip" onClick={() => onGo(pg.page_no)}>{pg.sheet_no ?? `p. ${pg.page_no + 1}`}: {qty(pg.qty, r.unit)}</button>)}
                    <button className="link-btn small" onClick={() => onItem(r.item_id)}>Edit item</button>
                  </td></tr>
                )}
              </Fragment>))}
            </tbody>
          ))}
          {cost > 0 && <tfoot><tr><td colSpan={3}>Total</td><td className="num">${cost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td></tr></tfoot>}
        </table>
      )}
      <h4>Export</h4>
      <div className="tk-actions">
        <a className="btn sm" href={tk.csvUrl(project.id)}><Icon name="download" size={13} /> CSV</a>
        <a className="btn sm" href={tk.pdfUrl(project.id)}><Icon name="file" size={13} /> Marked-up PDF</a>
      </div>
      {project.source?.kind === "files" && (
        <div className="tk-actions" title="A new file in the drawing set's files table, with its folder and fields (Settings → Extensions)">
          {(["csv", "pdf"] as const).map((what) => (
            <button key={what} className="btn sm" onClick={() => tk.saveToFiles(project.id, what).then(() => onNote(`Saved next to ${project.file_name}`)).catch(onError)}>
              <Icon name="folder" size={13} /> Save {what === "csv" ? "CSV" : "PDF"} next to the drawings
            </button>))}
        </div>
      )}
      <div className="row">
        <select className="input sm" value={base} onChange={(e) => setBase(e.target.value)} aria-label="Base">{bases.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select>
        <button className="btn sm" disabled={!base || !rows.length} onClick={toTable}><Icon name="grid" size={13} /> To a Probyte table</button>
      </div>
      <h4>Schedules on this sheet</h4>
      <p className="muted small">Copy a schedule (fixtures, panels, doors…) from the sheet into a Probyte table.</p>
      {tables === null ? <button className="btn sm" onClick={() => tk.tables(project.id, page).then(setTables).catch(onError)}><Icon name="table" size={13} /> Read the tables on this sheet</button>
        : !tables.length ? <p className="muted small">No tables found on this sheet.</p>
          : tables.map((t, i) => (
            <div key={i} className="tk-sched">
              <div className="row"><b>{t.heading ?? `Table ${i + 1}`}</b><span className="muted small">{t.cells.length} rows</span><span className="spacer" />
                <button className="btn sm" disabled={!base} onClick={() => schedule(t)}>To a table</button></div>
              <div className="tk-sched-preview">{t.cells.slice(0, 3).map((r, j) => <div key={j}>{r.filter(Boolean).join(" | ")}</div>)}</div>
            </div>
          ))}
      <p className="muted small tk-foot">Totals count only accepted marks. Waste is added on top, counts rounded up.</p>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "../../components/Icon";
import { Popover, usePopover } from "../../components/Overlay";
import type { ExtensionProps } from "..";
import { ItemsPanel, LegendReview, SearchPanel, SheetsPanel, SummaryPanel, type LegendGroup } from "./Panels";
import { feet, parseFeet, sheetName, tk, type Item, type Mark, type Project as P, type Pt, type Rect } from "./tk";
import { Viewer, type Tool } from "./Viewer";

type Tab = "sheets" | "items" | "search" | "takeoff";

const TOOLS: { id: Tool; icon: string; label: string; needs?: "item" }[] = [
  { id: "pan", icon: "hand", label: "Move around (drag) and pick marks" },
  { id: "count", icon: "target", label: "Count: click each one", needs: "item" },
  { id: "line", icon: "polyline", label: "Measure a run: click points, double-click to finish (Shift: straight)", needs: "item" },
  { id: "area", icon: "area", label: "Measure an area: click its corners, double-click to finish", needs: "item" },
  { id: "calibrate", icon: "ruler", label: "Set the scale: click both ends of a known dimension" },
  { id: "symbol", icon: "boxselect", label: "Box the selected item's symbol (in the legend)", needs: "item" },
  { id: "legend", icon: "list", label: "Box a legend to read its entries" },
  { id: "exclude", icon: "ban", label: "Leave an area out of automatic finds (notes, details, legends)" },
];

export default function Project({ id, go, onMenu, onError, onWorkspaceChanged }: ExtensionProps & { id: string }) {
  const [p, setP] = useState<P | null>(null);
  const [tab, setTab] = useState<Tab>("sheets");
  const [page, setPage] = useState(0);
  const [marks, setMarks] = useState<Mark[]>([]);
  const [itemId, setItemId] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>("pan");
  const [mark, setMark] = useState<{ m: Mark; x: number; y: number } | null>(null);
  const [search, setSearch] = useState<{ q: string; hits: { page_no: number; rects: Rect[] }[] } | null>(null);
  const [focus, setFocus] = useState<{ point: Pt; key: number } | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showRejected, setShowRejected] = useState(false);
  const [legend, setLegend] = useState<LegendGroup | null>(null);
  const [note, setNote] = useState("");
  const exportMenu = usePopover();

  const reload = useCallback(() => tk.project(id).then(setP).catch((e) => onError(e.message)), [id]);
  const loadMarks = useCallback(() => tk.marks(id, page).then(setMarks).catch((e) => onError(e.message)), [id, page]);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { loadMarks(); setMark(null); }, [loadMarks]);
  // the first sheet in the takeoff, once there is one
  useEffect(() => {
    if (p && !p.pages.find((s) => s.page_no === page)?.included) {
      const first = p.pages.find((s) => s.included);
      if (first && page === 0) setPage(first.page_no);
    }
  }, [p?.id]);
  useEffect(() => { if (note) { const t = setTimeout(() => setNote(""), 5000); return () => clearTimeout(t); } }, [note]);

  const items = useMemo(() => new Map((p?.items ?? []).map((i) => [i.id, i])), [p?.items]);
  const item = itemId ? items.get(itemId) ?? null : null;
  const sheet = p?.pages.find((s) => s.page_no === page);
  const refresh = () => { reload(); loadMarks(); };
  const fail = (e: unknown) => onError((e as Error).message);

  if (!p || !sheet) return <div className="boot">Opening the drawings…</div>;

  const pickTool = (t: Tool) => {
    const def = TOOLS.find((x) => x.id === t)!;
    if (def.needs === "item" && !item) { setTab("items"); setNote("Choose or add an item first"); return; }
    const want = { count: "count", line: "length", area: "area" }[t as "count" | "line" | "area"];
    if (item && want && item.kind !== want) {
      setNote(`${item.name} is ${item.kind === "count" ? "counted" : `measured as ${item.kind === "length" ? "a length" : "an area"}`}: change its kind to measure it this way`);
      return;
    }
    setTool(t);
  };

  const addPoint = async (pt: Pt) => {
    if (!item) return;
    try { const m = await tk.addMark(id, { item_id: item.id, page_no: page, geom: { type: "point", points: [pt] } }); setMarks((x) => [...x, m]); reload(); } catch (e) { fail(e); }
  };
  const addShape = async (type: "line" | "area", pts: Pt[]) => {
    if (!item) return;
    try {
      const m = await tk.addMark(id, { item_id: item.id, page_no: page, geom: { type, points: pts } });
      setMarks((x) => [...x, m]); reload();
      setNote(type === "line" ? `${item.name}: ${sheet.scale ? feet(m.value) : "no scale on this sheet yet"}` : `${item.name}: ${m.value.toFixed(1)} SF`);
    } catch (e) { fail(e); }
  };
  const onRect = async (t: Tool, r: Rect) => {
    try {
      if (t === "symbol" && item) {
        await tk.boxSymbol(item.id, page, r);
        setTool("pan"); reload(); setNote(`Symbol of ${item.name} set: now Find it on the sheets`);
      } else if (t === "legend") {
        const rows = await tk.legendIn(id, page, r);
        if (!rows.length) setNote("No legend entries in that box: box the symbols and their descriptions together");
        else setLegend({ page, groups: [{ heading: rows[0]?.category ?? null, rect: r, rows }] });
        setTool("pan");
      } else if (t === "exclude") {
        await tk.page(id, page, { excluded: [...sheet.excluded, r] }); reload();
      }
    } catch (e) { fail(e); }
  };
  const calibrate = async (pts: Pt[]) => {
    const raw = window.prompt(`How long is that, in real life? (e.g. 20'-0" or 20)`, "");
    if (raw === null) return;
    const ft = parseFeet(raw);
    if (!ft) { setNote("Write the length in feet: 20'-6\" or 20.5"); return; }
    try { await tk.calibrate(id, page, pts, ft); setTool("pan"); refresh(); setNote(`Scale of ${sheetName(sheet)} set from ${feet(ft)}`); } catch (e) { fail(e); }
  };
  const setMarkStatus = async (m: Mark, status: Mark["status"]) => {
    try { const n = await tk.mark(m.id, { status }); setMarks((x) => x.map((y) => (y.id === m.id ? n : y))); setMark(null); reload(); } catch (e) { fail(e); }
  };
  const deleteMark = async (m: Mark) => {
    try { await tk.removeMark(m.id); setMarks((x) => x.filter((y) => y.id !== m.id)); setMark(null); reload(); } catch (e) { fail(e); }
  };
  const goTo = (pageNo: number, point?: Pt) => {
    setPage(pageNo);
    if (point) setFocus({ point, key: Date.now() });
  };
  const included = p.pages.filter((s) => s.included);
  const nav = included.length ? included : p.pages;
  const at = nav.findIndex((s) => s.page_no === page);

  return (
    <div className="page tk-project">
      <header className="tk-head">
        <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Menu"><Icon name="menu" size={20} /></button>
        <button className="icon-btn" onClick={() => go("")} title="All takeoffs" aria-label="All takeoffs"><Icon name="left" size={16} /></button>
        <input className="tk-name" defaultValue={p.name} aria-label="Project name"
          onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== p.name) tk.rename(id, v).then(reload).catch(fail); }}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
        <nav className="tk-tabs">
          {(["sheets", "items", "search", "takeoff"] as Tab[]).map((t) => (
            <button key={t} className={`tk-tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
              {{ sheets: `Sheets · ${included.length}/${p.pages.length}`, items: `Items · ${p.items.filter((i) => i.status === "confirmed").length}`,
                search: "Search", takeoff: "Takeoff" }[t]}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <button className="btn sm" onClick={exportMenu.open}><Icon name="download" size={14} /> Export ▾</button>
        {exportMenu.anchor && (
          <Popover anchor={exportMenu.anchor} onClose={exportMenu.close} align="right" width={240}>
            <div className="menu-list">
              <a className="menu-item" href={tk.csvUrl(id)} onClick={exportMenu.close}><Icon name="table" size={14} /> Takeoff list (CSV)</a>
              <a className="menu-item" href={tk.pdfUrl(id)} onClick={exportMenu.close}><Icon name="file" size={14} /> Marked-up drawings (PDF)</a>
              <button className="menu-item" onClick={() => { exportMenu.close(); setTab("takeoff"); }}><Icon name="grid" size={14} /> To a Probyte table…</button>
            </div>
          </Popover>
        )}
      </header>
      <div className="tk-body">
        <aside className="tk-panel">
          {tab === "sheets" && <SheetsPanel project={p} page={page} onPage={(n) => goTo(n)} onChanged={reload} onError={fail}
            onCalibrate={() => setTool("calibrate")} />}
          {tab === "items" && <ItemsPanel project={p} page={page} marks={marks} item={item} onItem={(i: Item | null) => { setItemId(i?.id ?? null); }}
            onChanged={refresh} onError={fail} onTool={pickTool} onLegend={setLegend} onGo={goTo} hidden={hidden}
            onToggleHidden={(iid) => setHidden((h) => { const n = new Set(h); if (n.has(iid)) n.delete(iid); else n.add(iid); return n; })} />}
          {tab === "search" && <SearchPanel project={p} items={p.items} state={search} onState={setSearch} onGo={goTo} onChanged={refresh} onError={fail} />}
          {tab === "takeoff" && <SummaryPanel project={p} page={page} onGo={goTo} onItem={(iid) => { setItemId(iid); setTab("items"); }}
            onError={fail} onWorkspaceChanged={onWorkspaceChanged} onNote={setNote} />}
        </aside>
        <section className="tk-stage">
          <div className="tk-toolbar">
            <div className="tk-tools" role="toolbar" aria-label="Tools">
              {TOOLS.map((t) => (
                <button key={t.id} className={`icon-btn tk-tool ${tool === t.id ? "on" : ""}`} title={t.label} aria-label={t.label} aria-pressed={tool === t.id}
                  onClick={() => pickTool(t.id)}><Icon name={t.icon} size={16} /></button>
              ))}
            </div>
            {item && <span className="tk-current" title="The selected item: counts and measurements go to it"><i style={{ background: item.color }} />{item.name}</span>}
            <span className="spacer" />
            <button className={`icon-btn tk-tool ${showRejected ? "on" : ""}`} onClick={() => setShowRejected(!showRejected)} aria-pressed={showRejected}
              title={showRejected ? "Hide rejected finds" : "Show rejected finds"} aria-label="Show rejected finds"><Icon name="eye" size={15} /></button>
            <button className="icon-btn" disabled={at <= 0} onClick={() => goTo(nav[at - 1].page_no)} aria-label="Previous sheet"><Icon name="left" size={15} /></button>
            <select className="input sm tk-sheet-pick" value={page} onChange={(e) => goTo(Number(e.target.value))} aria-label="Sheet">
              {p.pages.map((s) => <option key={s.page_no} value={s.page_no}>{sheetName(s)}{s.title ? ` · ${s.title}` : ""}{s.included ? "" : " (not in takeoff)"}</option>)}
            </select>
            <button className="icon-btn" disabled={at < 0 || at >= nav.length - 1} onClick={() => goTo(nav[at + 1].page_no)} aria-label="Next sheet"><Icon name="right" size={15} /></button>
            <span className={`tk-scale ${sheet.scale ? "" : "missing"}`} title={sheet.scale ? `Scale: ${sheet.scale.label}` : "No scale: lengths and areas can't be measured"}>
              <Icon name="ruler" size={13} /> {sheet.scale ? sheet.scale.label : "No scale"}
            </span>
          </div>
          {note && <div className="tk-note" role="status">{note}</div>}
          <Viewer projectId={id} sheet={sheet} marks={marks} items={items} selectedItem={item} tool={tool} selectedMark={mark?.m.id ?? null}
            search={search?.hits.find((h) => h.page_no === page)?.rects ?? []} focus={focus} hidden={hidden} showRejected={showRejected}
            onPoint={addPoint} onShape={addShape} onRect={onRect} onCalibrate={calibrate} onCancelTool={() => setTool("pan")}
            onMarkClick={(m, xy) => { setMark({ m, ...xy }); setItemId(m.item_id); }} />
          {mark && <MarkMenu m={mark.m} at={mark} item={items.get(mark.m.item_id)} items={p.items} scaled={!!sheet.scale}
            onClose={() => setMark(null)} onStatus={setMarkStatus} onDelete={deleteMark}
            onMove={async (iid) => { try { await tk.mark(mark.m.id, { item_id: iid }); setMark(null); refresh(); } catch (e) { fail(e); } }} />}
        </section>
      </div>
      {legend && <LegendReview project={p} legend={legend} onClose={() => setLegend(null)} onError={fail}
        onAdded={(added) => { setLegend(null); refresh(); setTab("items"); if (added[0]) setItemId(added[0].id); setNote(`${added.length} item${added.length === 1 ? "" : "s"} added`); }} />}
    </div>
  );
}

function MarkMenu({ m, at, item, items, scaled, onClose, onStatus, onDelete, onMove }: {
  m: Mark; at: { x: number; y: number }; item?: Item; items: Item[]; scaled: boolean;
  onClose: () => void; onStatus: (m: Mark, s: Mark["status"]) => void; onDelete: (m: Mark) => void; onMove: (itemId: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, select")) return;
      if (e.key === "a" || e.key === "Enter") onStatus(m, "confirmed");
      if (e.key === "r") onStatus(m, "rejected");
      if (e.key === "Delete" || e.key === "Backspace") onDelete(m);
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const value = m.geom.type === "point" ? null : m.geom.type === "line" ? (scaled ? feet(m.value) : "no scale") : `${m.value.toFixed(1)} SF`;
  return (
    <div className="tk-mark-menu" style={{ left: Math.min(at.x + 12, window.innerWidth - 250), top: Math.min(at.y + 12, window.innerHeight - 220) }} role="dialog" aria-label="Mark">
      <div className="tk-mark-head"><i style={{ background: item?.color }} /><b>{item?.name}</b>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" size={13} /></button></div>
      <div className="muted small">
        {m.status === "suggested" ? `Suggested (${m.source === "tag" ? "label" : m.source}${m.score !== null && m.source === "symbol" ? `, ${Math.round(m.score * 100)}% alike` : ""})`
          : m.status === "rejected" ? "Rejected" : "In the takeoff"}{value ? ` · ${value}` : ""}
      </div>
      <div className="row">
        {m.status !== "confirmed" && <button className="btn sm primary" onClick={() => onStatus(m, "confirmed")} title="Accept (A)"><Icon name="check" size={13} /> Accept</button>}
        {m.status !== "rejected" && <button className="btn sm" onClick={() => onStatus(m, "rejected")} title="Reject (R)"><Icon name="ban" size={13} /> Reject</button>}
        <button className="btn sm danger-ghost" onClick={() => onDelete(m)} title="Delete (Del)"><Icon name="trash" size={13} /></button>
      </div>
      <label className="stack small">Belongs to
        <select className="input sm" value={m.item_id} onChange={(e) => onMove(e.target.value)}>
          {items.filter((i) => i.status === "confirmed" && i.kind === item?.kind).map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
        </select>
      </label>
    </div>
  );
}

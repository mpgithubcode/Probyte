import { useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Dataset, Field, Rec } from "../types";
import { displayText, isEditable, primaryField, recordMatches, recordTitle } from "../util";
import { createPortal } from "react-dom";
import { CellValue, RefsContext } from "./Cells";
import { FieldIcon, Icon } from "./Icon";
import { Modal, Popover, usePopover } from "./Overlay";
import { RecordHistory } from "./RecordHistory";
import { RecordValue } from "./LinkedView";
import type { Can } from "./viewTypes";
import type { RecordNav } from "./RecordModal";
import { showToast } from "../toast";
import { layoutFor, outlineOf, pageOf, PageBlocks } from "./PageBlocks";
import { useNarrow } from "./useNarrow";

export { defaultBlocks, pageFields, PageBlocks } from "./PageBlocks";
export type { BlockDesign } from "./PageBlocks";

/** The record's title: its primary field, large, wrapping onto as many lines as it needs; editable in place. */
export function PageTitle({ fields, rec, canEdit, onUpdate }: { fields: Field[]; rec: Rec; canEdit: boolean; onUpdate: (patch: Record<string, any>) => void }) {
  const p = primaryField(fields);
  const { refs } = useContext(RefsContext);
  const raw = p ? rec.data[p.id] : null;
  const text = p && raw !== null && raw !== undefined && raw !== "" ? (p.type === "text" || p.type === "long_text" ? String(raw) : displayText(p, raw, refs)) : "";
  const editable = canEdit && !!p && isEditable(p) && ["text", "long_text", "email", "url", "phone"].includes(p.type);
  const [draft, setDraft] = useState(text);
  const ta = useRef<HTMLTextAreaElement>(null);
  useEffect(() => setDraft(text), [text, rec.id]);
  // as tall as its text: it wraps, never scrolls sideways
  useLayoutEffect(() => {
    const t = ta.current;
    if (!t) return;
    const fit = () => { t.style.height = "auto"; t.style.height = `${t.scrollHeight}px`; };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(t);
    return () => ro.disconnect();
  }, [draft, editable]);
  if (!editable) return <h1 className="page-title-text">{text || <span className="muted">Untitled</span>}</h1>;
  const clean = (s: string) => s.replace(/\s+/g, " ").trim();
  const commit = () => { if (clean(draft) !== clean(text)) onUpdate({ [p!.id]: clean(draft) || null }); };
  return (
    <textarea ref={ta} className="page-title-input" rows={1} value={draft} placeholder="Untitled" aria-label={p!.name}
      onChange={(e) => setDraft(e.target.value.replace(/\n/g, " "))} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
        if (e.key === "Escape") { e.stopPropagation(); setDraft(text); }
      }} />
  );
}

/** The fields the page keeps under its title (Table settings → Page → Under the title). */
function HeaderFields({ ids, fields, rec, canEdit, onUpdate, compact }: {
  ids: string[]; fields: Field[]; rec: Rec; canEdit: boolean; onUpdate: (patch: Record<string, any>) => void; compact?: boolean;
}) {
  const list = ids.map((id) => fields.find((f) => f.id === id)).filter((f): f is Field => !!f);
  if (!list.length) return null;
  return (
    <div className={`page-header-fields ${compact ? "compact" : ""}`}>
      {list.map((f) => (
        <div key={f.id} className="page-header-field">
          <span className="page-label"><FieldIcon type={f.type} /> {f.name}</span>
          {compact ? <span className="page-header-value"><CellValue field={f} value={rec.data[f.id]} /></span>
            : <RecordValue field={f} value={rec.data[f.id]} rec={rec} canEdit={canEdit} onUpdate={onUpdate} />}
        </div>
      ))}
    </div>
  );
}

/** A record as a page (like Notion's): its title (the primary field) large, the fields kept under it, then the table's
 * page layout (Table settings → Page; a layout of its own for some kinds of records): its fields as blocks, each shown
 * its own way and sized, sections and tabs, related records, files, numbers, headings, and a long text as the page's
 * body. The title and the fields under it stay in view while scrolling; an outline jumps to its parts. Printable, and
 * shareable as a link to this one page. `publicMode`: opened from such a link. */
export function PageView({ ds, rec, fields, can, onUpdate, onDelete, onClose, onButton, nav, link, publicMode = false }: {
  ds: Dataset; rec: Rec; fields: Field[]; can: Can;
  onUpdate: (patch: Record<string, any>) => void; onDelete: () => void; onClose?: () => void; onButton: (f: Field) => void;
  nav?: RecordNav; link?: string; publicMode?: boolean;
}) {
  const page = pageOf(ds);
  const narrow = useNarrow();
  const [confirm, setConfirm] = useState(false);
  const [sharing, setSharing] = useState(false);
  useEffect(() => setConfirm(false), [rec.id]);
  const blocks = layoutFor(page, fields, rec);
  const { refs } = useContext(RefsContext);
  const outline = page?.outline ? outlineOf(blocks, (b) => recordMatches(rec, fields, b.when, refs) && !(publicMode && b.type === "related")) : [];
  const head = useRef<HTMLElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const [stuck, setStuck] = useState(false);   // the title has scrolled away: a compact copy stays at the top
  useEffect(() => {
    const el = head.current, root = scroller.current;
    if (!el || !root) return;
    const io = new IntersectionObserver(([e]) => setStuck(!e.isIntersecting), { root, threshold: 0 });
    io.observe(el);
    return () => io.disconnect();
  }, [rec.id]);
  useEffect(() => { scroller.current?.scrollTo(0, 0); }, [rec.id]);

  // J / K and Escape, unless typing somewhere or something is open over the page
  useEffect(() => {
    if (publicMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable=true], .popover") || document.querySelector(".backdrop, .popover")) return;
      if (e.key === "Escape") { e.preventDefault(); onClose?.(); }
      if (nav && (e.key === "j" || e.key === "J")) { e.preventDefault(); nav.next?.(); }
      if (nav && (e.key === "k" || e.key === "K")) { e.preventDefault(); nav.prev?.(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [nav, onClose, publicMode]);

  const jump = (id: string, tab?: string) => {
    if (tab) window.dispatchEvent(new CustomEvent("page-open-tab", { detail: tab }));
    requestAnimationFrame(() => (document.getElementById(`h-${id}`) ?? document.querySelector(`[data-block="${id}"]`))?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  const header = page?.header ?? [];
  const canShare = !publicMode && (ds.access?.role ?? "owner") === "owner" && ds.table?.kind !== "remote";

  // in the app: drawn at the top of the document (over the app; printed on its own), keeping the app's context; a link
  // to the page: where it is (it's the whole page there)
  const place = (el: JSX.Element) => (publicMode ? el : createPortal(el, document.body));
  return place(
    <div className={`page-view ${publicMode ? "public" : ""}`} role={publicMode ? undefined : "dialog"} aria-label={recordTitle(fields, rec)}>
      <div className="page-bar">
        {onClose && <button className="icon-btn" onClick={onClose} aria-label="Close" title="Close (Esc)"><Icon name="x" /></button>}
        <span className="page-crumb muted">{ds.table?.icon ? `${ds.table.icon} ` : ""}{ds.name}</span>
        <span className="spacer" />
        {outline.length > 0 && narrow && <OutlineButton items={outline} onJump={jump} />}
        {nav && nav.total > 1 && (
          <span className="record-nav">
            <button className="icon-btn" onClick={nav.prev} disabled={!nav.prev} title="Previous record (K)" aria-label="Previous record"><Icon name="up" size={16} /></button>
            <span className="muted small">{nav.index + 1} of {nav.total}</span>
            <button className="icon-btn" onClick={nav.next} disabled={!nav.next} title="Next record (J)" aria-label="Next record"><Icon name="down" size={16} /></button>
          </span>
        )}
        <PageMore link={publicMode ? undefined : link} canDelete={!publicMode && can.delete} onDelete={() => setConfirm(true)}
          onShare={canShare ? () => setSharing(true) : undefined} />
      </div>
      <div className={`page-scroll ${outline.length && !narrow ? "with-outline" : ""}`} ref={scroller}>
        {stuck && (
          <div className="page-sticky" aria-hidden>
            <b className="page-sticky-title">{recordTitle(fields, rec)}</b>
            <HeaderFields ids={header} fields={fields} rec={rec} canEdit={false} onUpdate={onUpdate} compact />
          </div>
        )}
        <div className="page-columns">
          <article className="page-paper">
            {confirm && (
              <div className="page-confirm">
                <span>Delete this record?</span>
                <button className="btn danger sm" onClick={onDelete}>Delete</button>
                <button className="btn sm" onClick={() => setConfirm(false)}>Cancel</button>
              </div>
            )}
            <header className="page-title" ref={head}>
              <PageTitle fields={fields} rec={rec} canEdit={can.edit} onUpdate={onUpdate} />
              <HeaderFields ids={header} fields={fields} rec={rec} canEdit={can.edit} onUpdate={onUpdate} />
            </header>
            <PageBlocks blocks={blocks} fields={fields} rec={rec} canEdit={can.edit} onUpdate={onUpdate} onButton={onButton} publicMode={publicMode} />
            {!publicMode && (
              <details className="page-history">
                <summary>History</summary>
                <RecordHistory key={rec.id} recordId={rec.id} fields={fields} />
              </details>
            )}
          </article>
          {outline.length > 0 && !narrow && (
            <nav className="page-outline" aria-label="On this page">
              <div className="page-outline-head">On this page</div>
              {outline.map((o) => (
                <button key={o.id} type="button" style={{ paddingLeft: 8 + o.depth * 12 }} onClick={() => jump(o.id, o.tab)}>{o.text}</button>
              ))}
            </nav>
          )}
        </div>
      </div>
      {sharing && <SharePage ds={ds} rec={rec} fields={fields} onClose={() => setSharing(false)} />}
    </div>,
  );
}

function OutlineButton({ items, onJump }: { items: ReturnType<typeof outlineOf>; onJump: (id: string, tab?: string) => void }) {
  const pop = usePopover();
  return (
    <>
      <button className="icon-btn" onClick={pop.open} aria-label="On this page" title="On this page"><Icon name="list" size={17} /></button>
      {pop.anchor && (
        <Popover anchor={pop.anchor} onClose={pop.close} align="right" width={240}>
          <div className="menu-list">
            <div className="menu-heading">On this page</div>
            {items.map((o) => <button key={o.id} className="menu-item" style={{ paddingLeft: 10 + o.depth * 12 }} onClick={() => { pop.close(); onJump(o.id, o.tab); }}>{o.text}</button>)}
          </div>
        </Popover>
      )}
    </>
  );
}

function PageMore({ link, canDelete, onDelete, onShare }: { link?: string; canDelete: boolean; onDelete: () => void; onShare?: () => void }) {
  const more = usePopover();
  return (
    <>
      <button className="icon-btn" onClick={more.open} aria-label="Record actions" title="More"><Icon name="more" size={18} /></button>
      {more.anchor && (
        <Popover anchor={more.anchor} onClose={more.close} align="right" width={230}>
          <div className="menu-list">
            {link && <button className="menu-item" onClick={() => { more.close(); navigator.clipboard?.writeText(link).then(() => showToast("Link to the record copied"), () => showToast(link)); }}>
              <Icon name="link" size={14} /> Copy link</button>}
            {onShare && <button className="menu-item" onClick={() => { more.close(); onShare(); }}><Icon name="share" size={14} /> Share this page…</button>}
            <button className="menu-item" onClick={() => { more.close(); printPage(); }}><Icon name="download" size={14} /> Print or save as PDF</button>
            {canDelete && <button className="menu-item danger" onClick={() => { more.close(); onDelete(); }}><Icon name="trash" size={14} /> Delete…</button>}
          </div>
        </Popover>
      )}
    </>
  );
}

/** Print just the page (the browser's own dialog also saves it as a PDF). */
function printPage() {
  document.body.classList.add("print-page");
  const done = () => { document.body.classList.remove("print-page"); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  setTimeout(() => window.print(), 50);
}

const EXPIRES: { days: number | null; label: string }[] = [{ days: null, label: "Never" }, { days: 1, label: "In a day" }, { days: 7, label: "In a week" }, { days: 30, label: "In 30 days" }];

/** A public link to this one page: anyone with it sees the record as this page (read-only, or able to edit its fields),
 * optionally with a password and an end date. Managed with the other links in Settings → Public links. */
function SharePage({ ds, rec, fields, onClose }: { ds: Dataset; rec: Rec; fields: Field[]; onClose: () => void }) {
  const [edit, setEdit] = useState(false);
  const [password, setPassword] = useState("");
  const [days, setDays] = useState<number | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const create = async () => {
    setBusy(true); setErr("");
    try {
      const s = await api.post<{ token: string }>("/shares", {
        name: recordTitle(fields, rec), base_id: ds.table?.base_id ?? ds.baseId, items: [{ kind: ds.kind, id: ds.id, record: rec.id }],
        permissions: { edit }, password: password || undefined,
        expires_at: days ? new Date(Date.now() + days * 86400000).toISOString() : undefined,
      });
      setUrl(`${location.origin}/s/${s.token}`);
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  return (
    <Modal title="Share this page" width={460} onClose={onClose}
      footer={<><span className="spacer" />{url ? <button className="btn primary" onClick={onClose}>Done</button>
        : <><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy} onClick={create}>{busy ? "Making…" : "Make the link"}</button></>}</>}>
      <div className="form">
        {url ? (<>
          <p className="muted small">Anyone with this link sees “{recordTitle(fields, rec)}” as this page{edit ? ", and can change its fields" : ""}. Turn it off or delete it
            in Settings → Public links.</p>
          <div className="row"><input className="input" readOnly value={url} onFocus={(e) => e.target.select()} aria-label="Link to the page" />
            <button className="btn" onClick={() => navigator.clipboard?.writeText(url).then(() => showToast("Link copied"), () => undefined)}><Icon name="copy" size={14} /> Copy</button></div>
        </>) : (<>
          <p className="muted small">A public link to this one record, shown as this page. Other records of the table stay private.</p>
          <label className="inline-check"><input type="checkbox" checked={edit} onChange={(e) => setEdit(e.target.checked)} /> People with the link can change its fields</label>
          <label className="stack">Password <span className="muted small">(optional)</span>
            <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          <label className="stack">The link stops working
            <select className="input" value={days ?? ""} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)}>
              {EXPIRES.map((x) => <option key={x.label} value={x.days ?? ""}>{x.label}</option>)}
            </select></label>
          {err && <div className="error">{err}</div>}
        </>)}
      </div>
    </Modal>
  );
}

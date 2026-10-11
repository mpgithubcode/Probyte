import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Base, CreateTarget, Dataset, Field, Rec, Refs, TableInfo, User, View, ViewConfig, ViewType } from "../types";
import { applyView, choiceColor, debounce, hiddenIds, orderFields, recordTitle, timeAgo } from "../util";
import { RefsContext, invalidateTitles } from "./Cells";
import { Icon, tableIcon } from "./Icon";
import { Popover, usePopover } from "./Overlay";
import { PAGED_VIEWS, Pager, usePaged } from "./Pager";
import { RecordModal, SourcePicker, createTargets } from "./RecordModal";
import { VIEW_TYPES, ViewOptions, ViewTabs } from "./Toolbar";
import { CAN_ALL, type Ops } from "./viewTypes";
import { buttonAction } from "../buttonAction";
import { showToast } from "../toast";
import { cached, loadComboSource, loadDataset, onTableChange, refreshDataset, remember } from "../data";

// Each view and the larger dialogs load on first use, keeping the initial download small.
const GridView = lazy(() => import("./GridView").then((m) => ({ default: m.GridView })));
const KanbanView = lazy(() => import("./KanbanView").then((m) => ({ default: m.KanbanView })));
const TimelineView = lazy(() => import("./TimelineView").then((m) => ({ default: m.TimelineView })));
const CalendarView = lazy(() => import("./CalendarView").then((m) => ({ default: m.CalendarView })));
const ListView = lazy(() => import("./ListView").then((m) => ({ default: m.ListView })));
const GalleryView = lazy(() => import("./GalleryView").then((m) => ({ default: m.GalleryView })));
const ChartView = lazy(() => import("./ChartView").then((m) => ({ default: m.ChartView })));
const FieldModal = lazy(() => import("./FieldModal").then((m) => ({ default: m.FieldModal })));
const ComboModal = lazy(() => import("./ComboModal").then((m) => ({ default: m.ComboModal })));
const LinkSettings = lazy(() => import("./LinkSettings").then((m) => ({ default: m.LinkSettings })));
const ImportModal = lazy(() => import("./ImportExport").then((m) => ({ default: m.ImportModal })));
const ExportModal = lazy(() => import("./ImportExport").then((m) => ({ default: m.ExportModal })));

// Changes arrive live (onTableChange); polling is the fallback. Linked tables have no live updates: poll them more often.
const POLL_MS = 60000;
const POLL_LINKED_MS = 15000;

export function DatasetPage({ kind, id, viewId, tables, me, bases, hiddenInSidebar, openRecord, onMenu, onNavigate, onWorkspaceChanged, onError }: {
  hiddenInSidebar?: boolean;
  openRecord?: string;  // a record to open (from search: #/table/<id>?record=<id>)
  kind: "table" | "combo"; id: string; viewId?: string; tables: TableInfo[]; me: User; bases: Base[]; onMenu: () => void;
  onNavigate: (viewId?: string) => void; onWorkspaceChanged: (goHome?: boolean) => void; onError: (msg: string) => void;
}) {
  const [ds, setDs] = useState<Dataset | null>(() => cached(kind, id) ?? null);
  const [search, setSearch] = useState("");
  const [openRec, setOpenRec] = useState<string | null>(null);
  const [openOcc, setOpenOcc] = useState<string | null>(null);  // a later repeat that was clicked
  const [fieldEdit, setFieldEdit] = useState<Field | null | undefined>(undefined);
  const [pendingCreate, setPendingCreate] = useState<{ data: Record<string, any>; open: boolean; choices: CreateTarget[] } | null>(null);
  const [comboEdit, setComboEdit] = useState(false);
  const [combineNew, setCombineNew] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [linkEdit, setLinkEdit] = useState(false);
  const [transfer, setTransfer] = useState<"import" | "export" | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const more = usePopover();
  const inflight = useRef(0);

  const dsRef = useRef(ds);
  dsRef.current = ds;
  const current = (d: Dataset | null): d is Dataset => !!d && d.kind === kind && d.id === id;

  /** Load the whole dataset again (after changes to its structure: fields, views, links, sources). */
  const reload = useCallback(async () => {
    try {
      const d = await loadDataset(kind, id);
      // Skip background refreshes that would race with an in-flight edit.
      if (inflight.current === 0) setDs((prev) => (prev && !current(prev) ? prev : d));
    } catch (e) { onError((e as Error).message); }
  }, [kind, id, onError]);

  /** Fetch just what changed since the last load (falls back to reload when it has to). */
  const refresh = useCallback(async () => {
    const cur = dsRef.current;
    if (!current(cur)) return reload();
    try {
      const update = await refreshDataset(cur);
      if (inflight.current === 0) setDs((prev) => (current(prev) ? update(prev) : prev));
    } catch (e) { onError((e as Error).message); }
  }, [kind, id, reload, onError]);
  const refreshSoon = useMemo(() => debounce(() => refresh(), 300), [refresh]);

  useEffect(() => {
    const hit = cached(kind, id);
    setDs(hit ?? null); setSearch(""); setOpenRec(null);
    if (hit) refresh(); else reload();
  }, [kind, id]);

  useEffect(() => { if (ds) remember(ds); }, [ds]);

  // opened from search: show that record once the table is here, then drop it from the address
  useEffect(() => {
    if (!openRecord || !ds || !ds.records.some((r) => r.id === openRecord)) return;
    setOpenRec(openRecord);
    history.replaceState(null, "", location.hash.replace(/\?record=[^&]*/, ""));
  }, [openRecord, ds?.records.length]);

  // A combined database shows its own tables at once; sources that are linked tables arrive one by one.
  const fetching = useRef(new Set<string>());
  const pendingKey = ds?.pending?.join(",") ?? "";
  useEffect(() => {
    const cur = dsRef.current;
    if (!cur?.pending?.length) return;
    for (const tid of cur.pending) {
      const key = `${cur.id}:${cur.loadedAt}:${tid}`;
      if (fetching.current.has(key)) continue;
      fetching.current.add(key);
      loadComboSource(cur, tid)
        .then((update) => setDs((prev) => (prev && prev.loadedAt === cur.loadedAt ? update(prev) : prev)))
        .catch((e) => { onError(`Couldn't load a linked source: ${(e as Error).message}`);
          setDs((prev) => prev && prev.loadedAt === cur.loadedAt ? { ...prev, pending: prev.pending?.filter((t) => t !== tid) } : prev); })
        .finally(() => fetching.current.delete(key));
    }
  }, [pendingKey, ds?.loadedAt]);

  // Live: refresh when this table (or a source of this combined database) changes anywhere.
  const sourceIds = ds?.kind === "combo" ? (ds.sources ?? []).map((s) => s.table_id).join(",") : id;
  useEffect(() => {
    const mine = new Set(sourceIds.split(","));
    return onTableChange((tid) => { if (mine.has(tid)) refreshSoon(); });
  }, [sourceIds, refreshSoon]);

  const linked = ds?.table?.kind === "remote";
  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, linked ? POLL_LINKED_MS : POLL_MS);
    const onShow = () => { if (document.visibilityState === "visible") refresh(); };
    document.addEventListener("visibilitychange", onShow);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onShow); };
  }, [refresh, linked]);

  const view: View | undefined = ds?.views.find((v) => v.id === viewId) ?? ds?.views[0];

  // ------------------------------------------------------------ view config (local + debounced save)
  const pendingView = useRef<{ vid: string; config: ViewConfig } | null>(null);
  const flushView = useCallback(() => {
    const p = pendingView.current;
    if (!p) return;
    pendingView.current = null;
    // keepalive lets the request finish even when the page is being closed or reloaded.
    fetch(`/api/views/${p.vid}`, {
      method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ config: p.config }), keepalive: true,
    }).then((r) => { if (!r.ok) onError("Could not save view settings"); }).catch(() => undefined);
  }, [onError]);
  const saveView = useMemo(() => debounce(flushView, 400), [flushView]);
  useEffect(() => {
    window.addEventListener("pagehide", flushView);
    return () => { window.removeEventListener("pagehide", flushView); flushView(); };
  }, [flushView]);

  const setConfig = (patch: Partial<ViewConfig>) => {
    if (!view || !ds) return;
    const config = { ...view.config, ...patch };
    setDs({ ...ds, views: ds.views.map((v) => (v.id === view.id ? { ...v, config } : v)) });
    if (pendingView.current && pendingView.current.vid !== view.id) flushView();
    pendingView.current = { vid: view.id, config };
    saveView();
  };

  // ------------------------------------------------------------ record operations
  const patchLocal = (recId: string, fn: (r: Rec) => Rec | null) =>
    setDs((d) => d && { ...d, records: d.records.flatMap((r) => (r.id === recId ? (fn(r) ?? []) : [r])) });

  const track = async <T,>(p: Promise<T>) => {
    inflight.current++;
    try { return await p; } finally { inflight.current--; }
  };

  const restoreRecords = async (ids: string[]) => {
    const r = await api.post<{ restored: string[] }>("/trash/records/restore", { ids });
    invalidateTitles();
    refresh();
    if (r.restored.length < ids.length) onError(`${ids.length - r.restored.length} couldn't be restored`);
  };

  const doCreate = async (data: Record<string, any>, openAfter: boolean, target?: CreateTarget) => {
    if (!ds) return;
    try {
      const rec: Rec = await track(ds.kind === "table"
        ? api.post(`/tables/${ds.id}/records`, { data, table_id: target?.linkTableId })
        : api.post(`/combos/${ds.id}/records`, { table_id: target?.tableId, link_table_id: target?.linkTableId, data }));
      setDs((d) => d && { ...d, records: [...d.records, rec] });
      if (openAfter) { setOpenRec(rec.id); setOpenOcc(null); }
      refreshSoon(); // picks up server-side defaults and newly registered select options
    } catch (e) { onError((e as Error).message); }
  };

  const addRefs = useCallback((r: Partial<Refs>) => setDs((d) => d && {
    ...d, refs: { records: { ...d.refs.records, ...r.records }, users: { ...d.refs.users, ...r.users } },
  }), []);

  // A linked table can do only what its source link allows; its fields come from the source.
  const linkPerms = ds?.table?.kind === "remote" ? ds.table.remote?.permissions ?? {} : null;
  const can = linkPerms ? { add: !!linkPerms.add, edit: !!linkPerms.edit, delete: !!linkPerms.delete, manage: false } : CAN_ALL;

  const ops: Ops = {
    can,
    create(data = {}, openAfter = false) {
      if (!ds) return;
      const choices = createTargets(ds);
      if (choices.length > 1) setPendingCreate({ data, open: openAfter, choices });
      else doCreate(data, openAfter, choices[0]);
    },
    update(rec, patch) {
      if (!ds) return;
      patchLocal(rec.id, (r) => ({ ...r, data: { ...r.data, ...patch } }));
      const url = ds.kind === "table" ? `/tables/${ds.id}/records/${rec.id}` : `/combos/${ds.id}/records/${rec.id}`;
      track(api.patch<Rec>(url, { data: patch, table_id: rec.table_id }))
        .then((saved) => {
          patchLocal(rec.id, () => saved);
          refreshSoon();  // other records it affects (links, lookups) and new select options
        })
        .catch((e) => { onError(e.message); refresh(); });
    },
    remove(rec) {
      patchLocal(rec.id, () => null);
      setOpenRec(null);
      track(api.del(`/tables/${rec.table_id}/records/${rec.id}`))
        .then(() => {
          invalidateTitles(); refreshSoon();
          showToast(`Deleted “${ds ? recordTitle(ds.fields, rec) : "record"}”`, { label: "Undo", run: () => restoreRecords([rec.id]) });
        })
        .catch((e) => { onError(e.message); refresh(); });
    },
    bulk(action, recs, patch = {}) {
      if (!ds || !recs.length) return;
      const ids = new Set(recs.map((r) => r.id));
      // what Undo puts back: the records (delete), or each record's previous values (edit; up to 500 records)
      const before = recs.map((r) => ({ id: r.id, table_id: r.table_id, data: Object.fromEntries(Object.keys(patch).map((k) => [k, r.data[k] ?? null])) }));
      if (action === "delete") setDs((d) => d && { ...d, records: d.records.filter((r) => !ids.has(r.id)) });
      else setDs((d) => d && { ...d, records: d.records.map((r) => (ids.has(r.id) ? { ...r, data: { ...r.data, ...patch } } : r)) });
      track(api.post<{ done: number; errors: { id: string; error: string }[] }>("/records/bulk", {
        action, data: patch, combo_id: ds.kind === "combo" ? ds.id : undefined,
        items: recs.map((r) => ({ id: r.id, table_id: r.table_id })),
      }))
        .then((res) => {
          invalidateTitles();
          refresh();
          if (res.errors.length) onError(`${res.errors.length} of ${recs.length} couldn't be ${action === "delete" ? "deleted" : "changed"}: ${res.errors[0].error}`);
          else if (action === "delete") showToast(`Deleted ${res.done} record${res.done === 1 ? "" : "s"}`, { label: "Undo", run: () => restoreRecords([...ids]) });
          else if (before.length <= 500) showToast(`Changed ${res.done} record${res.done === 1 ? "" : "s"}`, { label: "Undo", run: async () => {
            await Promise.all(before.map((b) => api.patch(ds.kind === "table" ? `/tables/${b.table_id}/records/${b.id}` : `/combos/${ds.id}/records/${b.id}`,
              { data: b.data, table_id: b.table_id })));
            refresh();
          } });
        })
        .catch((e) => { onError(e.message); refresh(); });
    },
    paste(items) {
      if (!ds || ds.kind !== "table") return;
      track(api.post<{ updated: number; created: string[]; errors: string[] }>(`/tables/${ds.id}/paste`, { items }))
        .then((res) => {
          refresh();
          const n = res.updated + res.created.length;
          showToast(res.errors.length ? `Pasted into ${n} rows; ${res.errors.length} couldn't be: ${res.errors[0]}`
            : `Pasted into ${n} row${n === 1 ? "" : "s"}${res.created.length ? ` (${res.created.length} new)` : ""}`);
        })
        .catch((e) => onError(e.message));
    },
    runButton(rec, field) {
      if (!ds) return;
      const r = buttonAction(ds, rec, field);
      if (r.error) onError(r.error);
      else if (r.url) window.open(r.url, "_blank", "noopener");
      else if (r.patch && Object.keys(r.patch).length) ops.update(rec, r.patch);
    },
    open: (rec) => { setOpenRec(rec.id); setOpenOcc(rec.occurrence ?? null); },
    editField: (f) => setFieldEdit(f),
    colorFor(rec, colorField = view?.config.colorField) {
      if (!ds) return undefined;
      if (colorField) {
        const f = ds.fields.find((x) => x.id === colorField);
        if (f) return choiceColor(f, rec.data[f.id]);
      }
      if (ds.kind === "combo") return ds.sources?.find((s) => s.table_id === rec.table_id)?.color;
      return undefined;
    },
  };

  // ------------------------------------------------------------ views
  const createView = async (type: ViewType) => {
    if (!ds) return;
    const config: ViewConfig = {};
    if (type === "kanban") config.groupBy = ds.fields.find((f) => f.type === "single_select" && f.id !== "__source")?.id;
    const dates = ds.fields.filter((f) => f.type === "date");
    if (type === "calendar" || type === "timeline") {
      // Reuse an existing calendar view's date fields when there is one.
      const cal = ds.views.find((v) => v.type === "calendar" || v.type === "timeline");
      config.dateField = cal?.config.dateField ?? dates[0]?.id;
      config.endField = cal?.config.endField ?? null;
      config.colorField = cal?.config.colorField ?? null;
    }
    if (type === "chart") {
      config.chart = { type: "bar", agg: "count", bucket: "month",
        xField: ds.fields.find((f) => f.type === "single_select")?.id ?? dates[0]?.id };
    }
    const name = VIEW_TYPES.find((t) => t.type === type)!.label;
    const url = ds.kind === "table" ? `/tables/${ds.id}/views` : `/combos/${ds.id}/views`;
    try {
      const v = await api.post<View>(url, { name, type, config });
      setDs({ ...ds, views: [...ds.views, v] });
      onNavigate(v.id);
    } catch (e) { onError((e as Error).message); }
  };

  const refreshLink = async () => {
    setSyncing(true);
    try { await api.post(`/tables/${id}/refresh`); } catch (e) { onError((e as Error).message); }
    setSyncing(false);
    reload();
  };

  const syncNow = async () => {
    if (!ds?.table) return;
    setSyncing(true);
    try {
      await api.post(`/tables/${ds.id}/sync`);
    } catch (e) { onError((e as Error).message); }
    setSyncing(false);
    reload();
  };

  const renameDataset = async (name: string) => {
    if (!ds || !name || name === ds.name) return;
    await api.patch(ds.kind === "table" ? `/tables/${ds.id}` : `/combos/${ds.id}`, { name });
    setDs({ ...ds, name });
    onWorkspaceChanged();
  };

  const currentBase = ds?.table?.base_id ?? (ds?.kind === "combo" ? ds.baseId : undefined);
  const moveTo = async (baseId: string) => {
    if (!ds) return;
    try {
      await api.patch(ds.kind === "table" ? `/tables/${ds.id}` : `/combos/${ds.id}`, { base_id: baseId });
      more.close();
      onWorkspaceChanged();
      reload();
    } catch (e) { onError((e as Error).message); }
  };

  const [deleteOnServer, setDeleteOnServer] = useState<boolean | null>(null);
  const deleteDataset = async () => {
    if (!ds) return;
    const onServer = deleteOnServer ?? !!ds.dav?.builtin;
    try {
      await api.del(ds.kind === "table" ? `/tables/${ds.id}${ds.dav ? `?delete_on_server=${onServer}` : ""}` : `/combos/${ds.id}`);
      onWorkspaceChanged(true);
      if (ds.kind === "table") {
        const id = ds.id;
        showToast(`Moved “${ds.name}” to the trash`, { label: "Undo", run: async () => {
          await api.post(`/trash/tables/${id}/restore`);
          onWorkspaceChanged();
          location.hash = `/table/${id}`;
        } });
      }
    } catch (e) { onError((e as Error).message); }
  };

  // the records this view shows, and the page of them on screen (table / list / gallery can be paged)
  const allRecords = useMemo(() => (ds && view ? applyView(ds.records, ds.fields, view.config, search, ds.refs) : []), [ds, view, search]);
  const paging = usePaged(allRecords, view && PAGED_VIEWS.has(view.type) ? view.config.pageSize : undefined,
    `${view?.id}|${search}|${JSON.stringify(view?.config.filter ?? null)}|${JSON.stringify(view?.config.sorts ?? null)}`);

  if (!ds || !view) return <div className="page loading">Loading…</div>;

  const hidden = hiddenIds(ds.fields, view.config);
  const visible = orderFields(ds.fields, view.config.fieldOrder).filter((f) => !hidden.has(f.id));
  const pg = paging;
  const records = PAGED_VIEWS.has(view.type) ? pg.items : allRecords;
  const rec = openRec ? ds.records.find((r) => r.id === openRec) : undefined;
  const props = { ds, view, fields: ds.fields, visible, records, ops, setConfig };
  const t = ds.table;
  const isCaldav = t && ["tasks", "events", "mixed", "contacts"].includes(t.kind);
  const link = t?.kind === "remote" ? t.remote : null;

  return (
    <RefsContext.Provider value={{ refs: ds.refs, addRefs, me }}>
    <div className="page">
      <header className="page-head">
        <div className="title-row">
          <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Menu"><Icon name="menu" size={20} /></button>
          <Icon name={ds.kind === "combo" ? "layers" : tableIcon(t!.kind)} size={20} className="title-icon" />
          {renaming ? (
            <input className="input title-input" autoFocus defaultValue={ds.name}
              onBlur={(e) => { renameDataset(e.target.value.trim()); setRenaming(false); }}
              onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") setRenaming(false); }} />
          ) : (
            <h1 onDoubleClick={() => setRenaming(true)}>{ds.name}</h1>
          )}
          {ds.kind === "combo" && (<>
            {!!ds.pending?.length && (
              <span className="sources-loading" title="Loading the linked tables in this combined database">
                <Icon name="sync" size={13} className="spin" /> Loading {ds.pending.length} linked {ds.pending.length === 1 ? "table" : "tables"}…
              </span>
            )}
            <div className="source-badges">
              {ds.sources?.map((s) => <span key={s.table_id} className="source-badge"><span className="swatch" style={{ background: s.color }} />{s.name}</span>)}
            </div>
          </>)}
          <span className="spacer" />
          {isCaldav && (
            <div className={`sync-status ${t.last_sync_error ? "err" : ""}`} title={t.last_sync_error ?? t.calendar_url ?? ""}>
              <span className="sync-text">{t.last_sync_error ? "Sync error" : `Synced ${timeAgo(t.last_synced_at)}`}</span>
              <button className="btn sm" disabled={syncing} onClick={syncNow}>
                <Icon name="sync" size={14} className={syncing ? "spin" : ""} /> <span className="btn-label">{syncing ? "Syncing" : "Sync"}</span>
              </button>
            </div>
          )}
          {link && (
            <div className="sync-status">
              <span className="remote-badge" title={link.url}>
                <Icon name="link" size={12} /> {link.name ?? "Linked"} · {link.local ? "this server" : new URL(link.origin).host}
              </span>
              <button className="btn sm" disabled={syncing} onClick={refreshLink}>
                <Icon name="sync" size={14} className={syncing ? "spin" : ""} /> <span className="btn-label">Refresh</span>
              </button>
            </div>
          )}
          {ds.kind === "combo" && <button className="btn sm" title="Sources & mapping" onClick={() => setComboEdit(true)}><Icon name="settings" size={14} /> <span className="btn-label">Sources & mapping</span></button>}
          {ds.kind === "table" && !link && <button className="btn sm" title="Add field" onClick={() => setFieldEdit(null)}><Icon name="plus" size={14} /> <span className="btn-label">Field</span></button>}
          <button className="icon-btn" onClick={more.open} aria-label="More"><Icon name="more" /></button>
          {more.anchor && (
            <Popover anchor={more.anchor} onClose={() => { more.close(); setConfirmDelete(false); }} align="right" width={240}>
              <div className="menu-list">
                <button className="menu-item" onClick={() => { setRenaming(true); more.close(); }}><Icon name="edit" size={14} /> Rename</button>
                {ds.kind === "table" && can.add && (
                  <button className="menu-item" onClick={() => { setTransfer("import"); more.close(); }}><Icon name="upload" size={14} /> Import from file…</button>
                )}
                <button className="menu-item" onClick={() => { setTransfer("export"); more.close(); }}><Icon name="download" size={14} /> Export…</button>
                {ds.kind === "table" ? (
                  <button className="menu-item" onClick={() => { setCombineNew(true); more.close(); }}><Icon name="layers" size={14} /> Combine with other tables…</button>
                ) : (
                  <button className="menu-item" onClick={() => { setComboEdit(true); more.close(); }}><Icon name="layers" size={14} /> Add tables…</button>
                )}
                <button className="menu-item" onClick={async () => {
                  more.close();
                  try {
                    await api.patch(ds.kind === "table" ? `/tables/${ds.id}` : `/combos/${ds.id}`, { hidden: !hiddenInSidebar });
                    onWorkspaceChanged();
                  } catch (e) { onError((e as Error).message); }
                }}><Icon name="eye" size={14} /> {hiddenInSidebar ? "Show in sidebar" : "Hide from sidebar"}</button>
                <button className="menu-item" title={ds.id} onClick={() => {
                  navigator.clipboard?.writeText(ds.id).then(() => onError(`Copied ID ${ds.id}`), () => onError(ds.id));
                  more.close();
                }}><Icon name="copy" size={14} /> Copy {ds.kind === "combo" ? "combined database" : "table"} ID</button>
                {link && <button className="menu-item" onClick={() => { setLinkEdit(true); more.close(); }}><Icon name="link" size={14} /> Link settings</button>}
                {bases.length > 1 && <div className="menu-heading">Move to base</div>}
                {bases.length > 1 && bases.map((b) => (
                  <button key={b.id} className="menu-item" disabled={b.id === currentBase} onClick={() => moveTo(b.id)}>
                    <span className="swatch" style={{ background: b.color }} /> {b.name} {b.id === currentBase && <span className="tick">✓</span>}
                  </button>
                ))}
                {confirmDelete ? (
                  <button className="menu-item danger" onClick={deleteDataset}>
                    <Icon name="trash" size={14} /> {ds.kind === "combo" ? "Click again to delete" : "Click again to move to the trash"}
                  </button>
                ) : (
                  <button className="menu-item danger" onClick={() => setConfirmDelete(true)}>
                    <Icon name="trash" size={14} /> {ds.kind === "combo" ? "Delete combined database" : "Move to trash"}
                  </button>
                )}
                {confirmDelete && ds.dav && (ds.dav.shared ? (
                  <div className="muted pad small">The {ds.dav.collection} on the server is kept: another table uses it too.</div>
                ) : (
                  <label className="inline-check pad small">
                    <input type="checkbox" checked={deleteOnServer ?? ds.dav.builtin} onChange={(e) => setDeleteOnServer(e.target.checked)} />
                    <span>{ds.dav.builtin ? `When the trash is emptied (after 30 days), also delete the ${ds.dav.collection} on Probyte's server (phones lose it then)`
                      : `When the trash is emptied (after 30 days), also delete its ${ds.dav.items} item${ds.dav.items === 1 ? "" : "s"} from ${ds.dav.account ?? "the account"}`}</span>
                  </label>
                ))}
              </div>
            </Popover>
          )}
        </div>
        {link?.error && (
          <div className="remote-error">
            <Icon name="link" size={14} /> <span style={{ flex: 1 }}>Couldn't load from the link: {link.error}</span>
            <button className="btn sm" onClick={() => setLinkEdit(true)}>Fix link</button>
          </div>
        )}
        <ViewTabs views={ds.views} active={view} onSelect={(v) => onNavigate(v.id)} onCreate={createView}
          onRename={(v, name) => {
            setDs({ ...ds, views: ds.views.map((x) => (x.id === v.id ? { ...x, name } : x)) });
            api.patch(`/views/${v.id}`, { name }).catch((e) => onError(e.message));
          }}
          onDelete={(v) => {
            setDs({ ...ds, views: ds.views.filter((x) => x.id !== v.id) });
            api.del(`/views/${v.id}`).catch((e) => onError(e.message));
            onNavigate(undefined);
          }} />
        <ViewOptions view={view} fields={ds.fields} config={view.config} setConfig={setConfig} search={search} setSearch={setSearch}
          onAdd={ops.can.add ? () => ops.create({}, true) : undefined} />
      </header>

      <main className="page-body">
        <Suspense fallback={<div className="loading">Loading…</div>}>
        {view.type === "grid" && <GridView {...props} />}
        {view.type === "kanban" && <KanbanView {...props} />}
        {view.type === "timeline" && <TimelineView {...props} />}
        {view.type === "calendar" && <CalendarView {...props} />}
        {view.type === "list" && <ListView {...props} />}
        {view.type === "gallery" && <GalleryView {...props} />}
        {view.type === "chart" && <ChartView {...props} />}
        </Suspense>
        {PAGED_VIEWS.has(view.type) && <Pager {...pg} />}
      </main>

      <Suspense fallback={null}>

      {rec && (
        <RecordModal ds={ds} rec={rec} fields={ds.fields} onClose={() => setOpenRec(null)} occurrence={openOcc}
          onUpdate={(patch) => ops.update(rec, patch)} onDelete={() => ops.remove(rec)} onButton={(f) => ops.runButton(rec, f)}
        />
      )}
      {transfer === "import" && ds.kind === "table" && (
        <ImportModal ds={ds} onClose={() => setTransfer(null)} onDone={(msg) => { setTransfer(null); onError(msg); refresh(); onWorkspaceChanged(); }} />
      )}
      {transfer === "export" && <ExportModal ds={ds} view={view} search={search} onClose={() => setTransfer(null)} />}
      {linkEdit && link && (
        <LinkSettings tableId={ds.id} link={link} onClose={() => setLinkEdit(false)} onSaved={() => { setLinkEdit(false); reload(); onWorkspaceChanged(); }} />
      )}
      {fieldEdit !== undefined && ds.kind === "table" && (
        <FieldModal tableId={ds.id} tableName={ds.name} field={fieldEdit} fields={ds.fields} tables={tables}
          onClose={() => setFieldEdit(undefined)} onSaved={() => { invalidateTitles(); reload(); onWorkspaceChanged(); }} />
      )}
      {pendingCreate && (
        <SourcePicker choices={pendingCreate.choices} onClose={() => setPendingCreate(null)}
          onPick={(t) => { doCreate(pendingCreate.data, pendingCreate.open, t); setPendingCreate(null); }} />
      )}
      {combineNew && ds.kind === "table" && (
        <ComboModal tables={tables} baseId={currentBase} initialTables={[ds.id]} initialName={`${ds.name} (combined)`}
          onClose={() => setCombineNew(false)}
          onSaved={(id) => { onWorkspaceChanged(); location.hash = `/combo/${id}`; }} />
      )}
      {comboEdit && ds.kind === "combo" && (
        <ComboModal tables={tables} baseId={currentBase}
          combo={{ id: ds.id, name: ds.name, sources: (ds.sources ?? []).map((s) => ({ table_id: s.table_id, color: s.color, name: s.name })), fields: ds.comboFields ?? [] }}
          onClose={() => setComboEdit(false)}
          onSaved={() => { reload(); onWorkspaceChanged(); }}
          onDeleted={() => onWorkspaceChanged(true)} />
      )}
      </Suspense>
    </div>
    </RefsContext.Provider>
  );
}

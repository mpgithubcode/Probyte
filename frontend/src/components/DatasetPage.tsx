import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Base, CreateTarget, Dataset, Field, Rec, Refs, TableInfo, User, View, ViewConfig, ViewType } from "../types";
import { applyView, choiceColor, debounce, hiddenIds, timeAgo } from "../util";
import { RefsContext, invalidateTitles } from "./Cells";
import { CalendarView } from "./CalendarView";
import { ChartView } from "./ChartView";
import { ComboModal } from "./ComboModal";
import { FieldModal } from "./FieldModal";
import { GalleryView } from "./GalleryView";
import { GridView } from "./GridView";
import { Icon, tableIcon } from "./Icon";
import { KanbanView } from "./KanbanView";
import { ListView } from "./ListView";
import { TimelineView } from "./TimelineView";
import { Popover, usePopover } from "./Overlay";
import { LinkSettings } from "./LinkSettings";
import { ExportModal, ImportModal } from "./ImportExport";
import { RecordModal, SourcePicker, createTargets } from "./RecordModal";
import { VIEW_TYPES, ViewOptions, ViewTabs } from "./Toolbar";
import { CAN_ALL, type Ops } from "./viewTypes";
import { buttonAction } from "../buttonAction";

const REFRESH_MS = 15000;
// Fields whose values depend on other records: refresh the whole dataset after edits.
const CROSS_RECORD = new Set(["link", "lookup", "rollup", "conditional_lookup", "conditional_rollup"]);

async function loadDataset(kind: "table" | "combo", id: string): Promise<Dataset> {
  if (kind === "table") {
    const r = await api.get(`/tables/${id}`);
    return { kind, id, name: r.table.name, table: r.table, fields: r.fields, views: r.views, records: r.records, refs: r.refs, linkSources: r.link_sources, dav: r.dav };
  }
  const r = await api.get(`/combos/${id}`);
  const comboFields = r.fields.filter((f: Field) => f.id !== "__source").map((f: Field) => ({ id: f.id, name: f.name, mapping: f.mapping ?? {} }));
  return { kind, id, name: r.combo.name, baseId: r.combo.base_id, sources: r.sources, comboFields, fields: r.fields, views: r.views, records: r.records, refs: r.refs };
}

export function DatasetPage({ kind, id, viewId, tables, me, bases, onMenu, onNavigate, onWorkspaceChanged, onError }: {
  kind: "table" | "combo"; id: string; viewId?: string; tables: TableInfo[]; me: User; bases: Base[]; onMenu: () => void;
  onNavigate: (viewId?: string) => void; onWorkspaceChanged: (goHome?: boolean) => void; onError: (msg: string) => void;
}) {
  const [ds, setDs] = useState<Dataset | null>(null);
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

  const reload = useCallback(async () => {
    try {
      const d = await loadDataset(kind, id);
      // Skip background refreshes that would race with an in-flight edit.
      if (inflight.current === 0) setDs(d);
    } catch (e) { onError((e as Error).message); }
  }, [kind, id, onError]);

  useEffect(() => { setDs(null); setSearch(""); setOpenRec(null); reload(); }, [reload]);

  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") reload(); }, REFRESH_MS);
    return () => clearInterval(t);
  }, [reload]);

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

  const doCreate = async (data: Record<string, any>, openAfter: boolean, target?: CreateTarget) => {
    if (!ds) return;
    try {
      const rec: Rec = await track(ds.kind === "table"
        ? api.post(`/tables/${ds.id}/records`, { data, table_id: target?.linkTableId })
        : api.post(`/combos/${ds.id}/records`, { table_id: target?.tableId, link_table_id: target?.linkTableId, data }));
      setDs((d) => d && { ...d, records: [...d.records, rec] });
      if (openAfter) { setOpenRec(rec.id); setOpenOcc(null); }
      reload(); // picks up server-side defaults and newly registered select options
    } catch (e) { onError((e as Error).message); }
  };

  const reloadSoon = useMemo(() => debounce(() => reload(), 300), [reload]);
  const crossRecord = !!ds?.fields.some((f) => CROSS_RECORD.has(f.type));
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
          // New select options are registered server-side; refresh field metadata.
          const fields = ds.fields.filter((f) => patch[f.id] !== undefined && (f.type === "single_select" || f.type === "multi_select"));
          const vals = fields.flatMap((f) => [patch[f.id]].flat());
          if (crossRecord || fields.some((f) => vals.some((v) => v && !f.options.choices?.some((c) => c.name === v)))) reloadSoon();
        })
        .catch((e) => { onError(e.message); reload(); });
    },
    remove(rec) {
      patchLocal(rec.id, () => null);
      setOpenRec(null);
      track(api.del(`/tables/${rec.table_id}/records/${rec.id}`))
        .then(() => { invalidateTitles(); if (crossRecord) reloadSoon(); })
        .catch((e) => { onError(e.message); reload(); });
    },
    bulk(action, recs, patch = {}) {
      if (!ds || !recs.length) return;
      const ids = new Set(recs.map((r) => r.id));
      if (action === "delete") setDs((d) => d && { ...d, records: d.records.filter((r) => !ids.has(r.id)) });
      else setDs((d) => d && { ...d, records: d.records.map((r) => (ids.has(r.id) ? { ...r, data: { ...r.data, ...patch } } : r)) });
      track(api.post<{ done: number; errors: { id: string; error: string }[] }>("/records/bulk", {
        action, data: patch, combo_id: ds.kind === "combo" ? ds.id : undefined,
        items: recs.map((r) => ({ id: r.id, table_id: r.table_id })),
      }))
        .then((res) => {
          if (res.errors.length) onError(`${res.errors.length} of ${recs.length} couldn't be ${action === "delete" ? "deleted" : "changed"}: ${res.errors[0].error}`);
          invalidateTitles();
          reload();
        })
        .catch((e) => { onError(e.message); reload(); });
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
    } catch (e) { onError((e as Error).message); }
  };

  if (!ds || !view) return <div className="page loading">Loading…</div>;

  const hidden = hiddenIds(ds.fields, view.config);
  const visible = ds.fields.filter((f) => !hidden.has(f.id));
  const records = applyView(ds.records, ds.fields, view.config, search, ds.refs);
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
          {ds.kind === "combo" && (
            <div className="source-badges">
              {ds.sources?.map((s) => <span key={s.table_id} className="source-badge"><span className="swatch" style={{ background: s.color }} />{s.name}</span>)}
            </div>
          )}
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
                    <Icon name="trash" size={14} /> Click again to delete
                  </button>
                ) : (
                  <button className="menu-item danger" onClick={() => setConfirmDelete(true)}>
                    <Icon name="trash" size={14} /> Delete {ds.kind === "combo" ? "combined database" : "database"}
                  </button>
                )}
                {confirmDelete && ds.dav && (ds.dav.shared ? (
                  <div className="muted pad small">The {ds.dav.collection} on the server is kept: another table uses it too.</div>
                ) : (
                  <label className="inline-check pad small">
                    <input type="checkbox" checked={deleteOnServer ?? ds.dav.builtin} onChange={(e) => setDeleteOnServer(e.target.checked)} />
                    <span>{ds.dav.builtin ? `Also delete the ${ds.dav.collection} on Probyte's server (phones lose it too)`
                      : `Also delete its ${ds.dav.items} item${ds.dav.items === 1 ? "" : "s"} from ${ds.dav.account ?? "the account"}`}</span>
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
        {view.type === "grid" && <GridView {...props} />}
        {view.type === "kanban" && <KanbanView {...props} />}
        {view.type === "timeline" && <TimelineView {...props} />}
        {view.type === "calendar" && <CalendarView {...props} />}
        {view.type === "list" && <ListView {...props} />}
        {view.type === "gallery" && <GalleryView {...props} />}
        {view.type === "chart" && <ChartView {...props} />}
      </main>

      {rec && (
        <RecordModal ds={ds} rec={rec} fields={ds.fields} onClose={() => setOpenRec(null)} occurrence={openOcc}
          onUpdate={(patch) => ops.update(rec, patch)} onDelete={() => ops.remove(rec)} onButton={(f) => ops.runButton(rec, f)}
        />
      )}
      {transfer === "import" && ds.kind === "table" && (
        <ImportModal ds={ds} onClose={() => setTransfer(null)} onDone={(msg) => { setTransfer(null); onError(msg); reload(); onWorkspaceChanged(); }} />
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
        <ComboModal tables={tables}
          combo={{ id: ds.id, name: ds.name, sources: (ds.sources ?? []).map((s) => ({ table_id: s.table_id, color: s.color, name: s.name })), fields: ds.comboFields ?? [] }}
          onClose={() => setComboEdit(false)}
          onSaved={() => { reload(); onWorkspaceChanged(); }}
          onDeleted={() => onWorkspaceChanged(true)} />
      )}
    </div>
    </RefsContext.Provider>
  );
}

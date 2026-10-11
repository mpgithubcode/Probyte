import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { buttonAction } from "../buttonAction";
import type { Dataset, Field, Rec, Refs, Source, View, ViewConfig } from "../types";
import { applyView, choiceColor, hiddenIds, orderFields } from "../util";
import { CalendarView } from "./CalendarView";
import { RefsContext, setAttachmentBase } from "./Cells";
import { ChartView } from "./ChartView";
import { GalleryView } from "./GalleryView";
import { GridView } from "./GridView";
import { Icon } from "./Icon";
import { KanbanView } from "./KanbanView";
import { ListView } from "./ListView";
import { NewRecordForm, RecordModal, SourcePicker } from "./RecordModal";
import { PAGED_VIEWS, Pager, usePaged } from "./Pager";
import { TimelineView } from "./TimelineView";
import { VIEW_TYPES, ViewOptions } from "./Toolbar";
import type { Can, Ops } from "./viewTypes";

interface Meta {
  name: string; color: string; needs_password: boolean;
  permissions: { add?: boolean; edit?: boolean; delete?: boolean };
  items: { kind: "table" | "combo"; id: string; name: string; table_kind: string; views: { id: string; name: string; type: View["type"] }[] }[];
}

interface ViewPayload {
  item: { kind: "table" | "combo"; id: string; name: string; table_kind: string };
  view: View; views: View[]; fields: Field[]; records: Rec[]; refs: Refs; sources: Source[] | null;
}

const REFRESH_MS = 30000;

export function ShareApp({ token }: { token: string }) {
  const base = `/public/${token}`;
  const embed = new URLSearchParams(location.search).has("embed");
  const [meta, setMeta] = useState<Meta | "missing" | null>(null);
  const [viewId, setViewId] = useState<string | null>(() => location.hash.replace(/^#\/?/, "") || null);
  const [payload, setPayload] = useState<ViewPayload | null>(null);
  const [config, setLocalConfig] = useState<ViewConfig>({});
  const [search, setSearch] = useState("");
  const [openRec, setOpenRec] = useState<string | null>(null);
  const [openOcc, setOpenOcc] = useState<string | null>(null);
  const [pendingCreate, setPendingCreate] = useState<{ data: Record<string, unknown>; open: boolean } | null>(null);
  const [draft, setDraft] = useState<{ data: Record<string, any>; tableId?: string } | null>(null);  // add-only links: a record being filled in
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => { setAttachmentBase(`/api${base}/attachments`); }, [base]);

  const loadMeta = useCallback(() => api.get<Meta>(base).then((m) => {
    setMeta(m);
    document.title = m.name;
    const all = m.items.flatMap((i) => i.views.map((v) => v.id));
    setViewId((cur) => (cur && all.includes(cur) ? cur : all[0] ?? null));
  }).catch(() => setMeta("missing")), [base]);
  useEffect(() => { loadMeta(); }, [loadMeta]);

  const loadView = useCallback(async (keepConfig = false) => {
    if (!viewId) return;
    try {
      const p = await api.get<ViewPayload>(`${base}/views/${viewId}`);
      setPayload(p);
      if (!keepConfig) setLocalConfig(p.view.config);
    } catch (e) { setToast((e as Error).message); }
  }, [base, viewId]);

  useEffect(() => {
    if (meta && meta !== "missing" && !meta.needs_password && viewId) {
      history.replaceState(null, "", `#/${viewId}`);
      setPayload(null);
      loadView();
    }
  }, [meta, viewId, loadView]);

  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") loadView(true); }, REFRESH_MS);
    return () => clearInterval(t);
  }, [loadView]);

  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(null), 5000); return () => clearTimeout(t); } }, [toast]);

  const ds: Dataset | null = useMemo(() => payload && {
    kind: payload.item.kind, id: payload.item.id, name: payload.item.name,
    table: payload.item.kind === "table" ? { id: payload.item.id, base_id: "", name: payload.item.name, kind: payload.item.table_kind as "generic",
      account_id: null, calendar_url: null, last_synced_at: null, last_sync_error: null } : undefined,
    sources: payload.sources ?? undefined, fields: payload.fields, views: payload.views, records: payload.records, refs: payload.refs,
  }, [payload]);

  const viewType = payload?.view.type;
  const allRecords = useMemo(() => (ds && payload ? applyView(ds.records, ds.fields, config, search, ds.refs) : []), [ds, payload, config, search]);
  const paging = usePaged(allRecords, viewType && PAGED_VIEWS.has(viewType) ? config.pageSize : undefined,
    `${viewId}|${search}|${JSON.stringify(config.filter ?? config.filters ?? null)}|${JSON.stringify(config.sorts ?? null)}`);

  if (meta === null) return <div className="boot">Loading…</div>;
  if (meta === "missing") return <div className="boot share-missing"><Icon name="lock" size={28} /><p>This link doesn't exist or is no longer available.</p></div>;
  if (meta.needs_password) return <PasswordGate meta={meta} base={base} onUnlocked={loadMeta} />;

  const perms = meta.permissions;
  const can: Can = { add: !!perms.add, edit: !!perms.edit, delete: !!perms.delete, manage: false };
  const view: View | undefined = payload ? { ...payload.view, config } : undefined;
  const setRecords = (fn: (r: Rec[]) => Rec[]) => setPayload((p) => p && { ...p, records: fn(p.records) });

  const doCreate = async (data: Record<string, unknown>, open: boolean, tableId?: string) => {
    if (open && !can.edit) {  // may add but not edit: fill the record in first, then save it in one go
      setDraft({ data: data as Record<string, any>, tableId });
      return;
    }
    try {
      const rec = await api.post<Rec>(`${base}/views/${viewId}/records`, { data, table_id: tableId });
      setRecords((rs) => [...rs, rec]);
      if (open) { setOpenRec(rec.id); setOpenOcc(null); }
    } catch (e) { setToast((e as Error).message); }
  };
  const saveDraft = async (data: Record<string, any>) => {
    try {
      const rec = await api.post<Rec>(`${base}/views/${viewId}/records`, { data, table_id: draft?.tableId });
      setRecords((rs) => [...rs, rec]);
      setDraft(null);
      setToast("Record added");
    } catch (e) { setToast((e as Error).message); }
  };

  const ops: Ops = {
    can,
    create(data = {}, open = false) {
      if (!can.add || !payload) return;
      const sources = payload.sources ?? [];
      if (payload.item.kind === "combo" && sources.length > 1) setPendingCreate({ data, open });
      else doCreate(data, open, sources[0]?.table_id);
    },
    update(rec, patch) {
      if (!can.edit) return;
      setRecords((rs) => rs.map((r) => (r.id === rec.id ? { ...r, data: { ...r.data, ...patch } } : r)));
      api.patch<Rec>(`${base}/views/${viewId}/records/${rec.id}`, { data: patch })
        .then((saved) => setRecords((rs) => rs.map((r) => (r.id === rec.id ? saved : r))))
        .catch((e) => { setToast(e.message); loadView(true); });
    },
    remove(rec) {
      if (!can.delete) return;
      setRecords((rs) => rs.filter((r) => r.id !== rec.id));
      setOpenRec(null);
      api.del(`${base}/views/${viewId}/records/${rec.id}`)
        .then(() => loadView(true)).catch((e) => { setToast(e.message); loadView(true); });
    },
    open: (rec) => { setOpenRec(rec.id); setOpenOcc(rec.occurrence ?? null); },
    editField: () => undefined,
    runButton(rec, field) {
      if (!ds) return;
      const r = buttonAction(ds, rec, field);
      if (r.error) setToast(r.error);
      else if (r.url) window.open(r.url, "_blank", "noopener");
      else if (r.patch && can.edit) ops.update(rec, r.patch);
    },
    colorFor(rec, colorField = config.colorField) {
      if (!ds) return undefined;
      const f = colorField ? ds.fields.find((x) => x.id === colorField) : undefined;
      if (f) return choiceColor(f, rec.data[f.id]);
      return ds.sources?.find((s) => s.table_id === rec.table_id)?.color;
    },
  };

  const records = view && PAGED_VIEWS.has(view.type) ? paging.items : allRecords;
  const hidden = ds ? hiddenIds(ds.fields, config) : new Set<string>();
  const visible = ds ? orderFields(ds.fields, config.fieldOrder).filter((f) => !hidden.has(f.id)) : [];
  const props = ds && view ? { ds, view, fields: ds.fields, visible, records, ops, setConfig: (p: Partial<ViewConfig>) => setLocalConfig((c) => ({ ...c, ...p })) } : null;
  const rec = openRec && ds ? ds.records.find((r) => r.id === openRec) : undefined;
  const current = meta.items.find((i) => i.views.some((v) => v.id === viewId));

  return (
    <RefsContext.Provider value={{ refs: ds?.refs ?? { records: {}, users: {} }, addRefs: () => undefined }}>
      <div className={`share-app ${embed ? "embed" : ""}`} style={{ ["--share-color" as string]: meta.color }}>
        <header className="share-head">
          {!embed && (
            <div className="share-title">
              <span className="base-dot" style={{ background: meta.color }}>{meta.name[0]?.toUpperCase()}</span>
              <h1>{meta.name}</h1>
            </div>
          )}
          {meta.items.length > 1 && (
            <div className="view-tabs share-items-tabs">
              {meta.items.map((i) => (
                <button key={i.id} className={`view-tab ${current?.id === i.id ? "active" : ""}`} onClick={() => setViewId(i.views[0].id)}>
                  <Icon name={i.kind === "combo" ? "layers" : "table"} size={14} /> {i.name}
                </button>
              ))}
            </div>
          )}
          {current && current.views.length > 1 && (
            <div className="view-tabs">
              {current.views.map((v) => (
                <button key={v.id} className={`view-tab ${v.id === viewId ? "active" : ""}`} onClick={() => setViewId(v.id)}>
                  <Icon name={VIEW_TYPES.find((t) => t.type === v.type)?.icon ?? "grid"} size={14} /> {v.name}
                </button>
              ))}
            </div>
          )}
          {view && ds && <ViewOptions view={view} fields={ds.fields} config={config} setConfig={(p) => setLocalConfig((c) => ({ ...c, ...p }))} search={search} setSearch={setSearch}
            onAdd={can.add ? () => ops.create({}, true) : undefined} />}
        </header>
        <main className="page-body">
          {!props && <div className="page loading">Loading…</div>}
          {props && view?.type === "grid" && <GridView {...props} />}
          {props && view?.type === "kanban" && <KanbanView {...props} />}
          {props && view?.type === "timeline" && <TimelineView {...props} />}
          {props && view?.type === "calendar" && <CalendarView {...props} />}
          {props && view?.type === "list" && <ListView {...props} />}
          {props && view?.type === "gallery" && <GalleryView {...props} />}
          {props && view?.type === "chart" && <ChartView {...props} />}
          {view && PAGED_VIEWS.has(view.type) && <Pager {...paging} />}
        </main>
        {!embed && <footer className="share-foot">Shared with <a href="/" target="_blank" rel="noreferrer">Probyte</a></footer>}
        {rec && ds && (
          <RecordModal ds={ds} rec={rec} fields={ds.fields} can={can} history={false} onClose={() => setOpenRec(null)} occurrence={openOcc}
            onUpdate={(patch) => ops.update(rec, patch)} onDelete={() => ops.remove(rec)} onButton={(f) => ops.runButton(rec, f)} />
        )}
        {draft && ds && <NewRecordForm fields={ds.fields} initial={draft.data} onSave={saveDraft} onClose={() => setDraft(null)} />}
        {pendingCreate && ds && (
          <SourcePicker choices={(ds.sources ?? []).map((s) => ({ label: s.name, color: s.color, tableId: s.table_id }))} onClose={() => setPendingCreate(null)}
            onPick={(t) => { doCreate(pendingCreate.data, pendingCreate.open, t.tableId); setPendingCreate(null); }} />
        )}
        {toast && <div className="toast" onClick={() => setToast(null)}>{toast}</div>}
      </div>
    </RefsContext.Provider>
  );
}

function PasswordGate({ meta, base, onUnlocked }: { meta: Meta; base: string; onUnlocked: () => void }) {
  const [pw, setPw] = useState("");
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try { await api.post(`${base}/unlock`, { password: pw }); onUnlocked(); } catch (err) { setError((err as Error).message); }
  };
  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <div className="brand"><span className="base-dot lg" style={{ background: meta.color }}>{meta.name[0]?.toUpperCase()}</span><span>{meta.name}</span></div>
        <p className="muted">This link is password protected.</p>
        <input className="input" type="password" autoFocus placeholder="Password" value={pw} onChange={(e) => setPw(e.target.value)} />
        {error && <div className="error">{error}</div>}
        <button className="btn primary block" disabled={!pw}>Open</button>
      </form>
    </div>
  );
}

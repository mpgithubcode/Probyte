import { mergeRefs } from "./util";
import { api } from "./api";
import type { Dataset, Field, Rec, Refs } from "./types";

/**
 * The open datasets, kept in memory so switching between tables shows them at once, and brought up to date by
 * fetching only what changed (GET /tables/{id}/changes) rather than the whole table. The server says when a
 * table changed through one shared event stream (/api/events).
 */

const FULL_RELOAD_MS = 10 * 60 * 1000;  // reload completely now and then (views, sync status, time-based values)
const cache = new Map<string, Dataset>();
const key = (kind: string, id: string) => `${kind}:${id}`;

export const cached = (kind: "table" | "combo", id: string) => cache.get(key(kind, id));
export const remember = (ds: Dataset) => { cache.set(key(ds.kind, ds.id), ds); };
export const forget = (kind: "table" | "combo", id: string) => { cache.delete(key(kind, id)); };

export async function loadDataset(kind: "table" | "combo", id: string): Promise<Dataset> {
  const loadedAt = Date.now();
  if (kind === "table") {
    const r = await api.get(`/tables/${id}`);
    return { kind, id, name: r.table.name, table: r.table, access: r.access, fields: r.fields, views: r.views, records: r.records, refs: r.refs,
      linkSources: r.link_sources, dav: r.dav, storage: r.storage, token: r.token, loadedAt };
  }
  // sources that are linked tables (another server, maybe slow) come separately: see loadComboSource
  const r = await api.get(`/combos/${id}?defer_remote=true`);
  const comboFields = r.fields.filter((f: Field) => f.id !== "__source").map((f: Field) => ({ id: f.id, name: f.name, mapping: f.mapping ?? {} }));
  return { kind, id, name: r.combo.name, baseId: r.combo.base_id, access: r.access, sources: r.sources, comboFields, fields: r.fields, views: r.views,
    records: r.records, refs: r.refs, loadedAt, pending: r.pending ?? [], icon: r.combo.icon, description: r.combo.description };
}

/** One pending source of a combined database; returns an updater that merges its records in. */
export async function loadComboSource(ds: Dataset, tableId: string): Promise<(prev: Dataset) => Dataset> {
  const r = await api.get<{ records: Rec[]; refs: Refs; link_sources: Dataset["linkSources"] }>(`/combos/${ds.id}/sources/${tableId}`);
  return (prev) => {
    if (prev.id !== ds.id || !prev.pending?.includes(tableId)) return prev;  // reloaded meanwhile
    const records = [...prev.records.filter((x) => x.table_id !== tableId), ...r.records]
      .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
    return {
      ...prev, records, pending: prev.pending.filter((t) => t !== tableId),
      sources: prev.sources?.map((s) => (s.table_id === tableId ? { ...s, link_sources: r.link_sources ?? undefined } : s)),
      refs: mergeRefs(prev.refs, r.refs),
    };
  };
}

/**
 * Bring a dataset up to date: just the changed records when possible, otherwise load it again. Returns an
 * updater to apply to the dataset as it is by then, so edits made locally in the meantime are kept.
 */
export async function refreshDataset(ds: Dataset): Promise<(prev: Dataset) => Dataset> {
  if (ds.kind !== "table" || !ds.token || Date.now() - (ds.loadedAt ?? 0) > FULL_RELOAD_MS) {
    const fresh = await loadDataset(ds.kind, ds.id);
    // linked sources of a combined database reload separately: keep showing what they had until then
    return (prev) => (fresh.pending?.length && prev.id === fresh.id
      ? { ...fresh, records: [...fresh.records, ...prev.records.filter((r) => fresh.pending!.includes(r.table_id))] } : fresh);
  }
  const ch = await api.get<{ reload?: boolean; token: string; records: Rec[]; deleted: string[]; refs: Refs }>(
    `/tables/${ds.id}/changes?since=${encodeURIComponent(ds.token)}`);
  if (ch.reload) {
    const fresh = await loadDataset(ds.kind, ds.id);
    return () => fresh;
  }
  return (prev) => applyChanges(prev, ch);
}

function applyChanges(ds: Dataset, ch: { token: string; records: Rec[]; deleted: string[]; refs: Refs }): Dataset {
  if (!ch.records.length && !ch.deleted.length) return ch.token === ds.token ? ds : { ...ds, token: ch.token };
  const changed = new Map(ch.records.map((r) => [r.id, r]));
  const gone = new Set(ch.deleted);
  const records = ds.records.flatMap((r) => (gone.has(r.id) ? [] : [changed.get(r.id) ?? r]));
  const known = new Set(ds.records.map((r) => r.id));
  for (const r of ch.records) if (!known.has(r.id) && !gone.has(r.id)) records.push(r);
  return {
    ...ds, records, token: ch.token,
    refs: mergeRefs(ds.refs, ch.refs),
  };
}

// ------------------------------------------------------------ live: which tables just changed

type Listener = (tableId: string) => void;
const listeners = new Set<Listener>();
let source: EventSource | null = null;

/** Be told when one of your tables changes (anywhere: another tab, a phone via DAV sync, the API). */
export function onTableChange(fn: Listener): () => void {
  listeners.add(fn);
  if (!source && typeof EventSource !== "undefined") {
    source = new EventSource("/api/events");
    source.addEventListener("change", (e) => listeners.forEach((l) => l((e as MessageEvent).data)));
  }
  return () => {
    listeners.delete(fn);
    if (!listeners.size && source) { source.close(); source = null; }
  };
}

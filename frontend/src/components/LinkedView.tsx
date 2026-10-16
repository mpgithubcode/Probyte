import { useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api";
import type { Attachment, Dataset, Field, FieldView, Rec, Refs, TableInfo } from "../types";
import { BUILTIN_KEYS, isEditable, primaryField, recordTitle } from "../util";
import { thumbUrl } from "../files";
import { CellValue, fileItems, FieldInput, LinkPicker, RefsContext } from "./Cells";
import { displayOf, isDefaultDisplay } from "../display";
import { useNarrow } from "./useNarrow";
import { FieldIcon, Icon } from "./Icon";
import { RecordModal } from "./RecordModal";
import { showToast } from "../toast";

export { VIEW_LABEL } from "../display";

/** A field's value on a record or a page, shown the way chosen for records (or by the page block). Its ordinary look is
 * edited in place as always; another look (a bar, Yes / No, a big number…) turns into the editor when clicked. Links
 * shown as a list, a table or cards come with their records' fields, and a button to link more. */
export function RecordValue({ field, value, rec: _rec, canEdit, onUpdate, view, anchor }: {
  field: Field; value: any; rec: Rec; canEdit: boolean; onUpdate: (patch: Record<string, any>) => void; view?: FieldView; anchor?: string;
}) {
  const how = displayOf(field, "record", view);
  const editable = canEdit && isEditable(field);
  if (field.type === "link" && (how === "list" || how === "table" || how === "cards" || how === "hcards")) {
    return <LinkedRecords field={field} ids={Array.isArray(value) ? value : []} view={how} editable={editable}
      onChange={(ids) => onUpdate({ [field.id]: ids })} />;
  }
  const commit = (v: any) => onUpdate({ [field.id]: v });
  if (isDefaultDisplay(field, "record", how)) {
    return editable ? <FieldInput field={field} value={value} onCommit={commit} anchor={anchor} />
      : <div className="computed-value"><CellValue field={field} value={value} view={how ?? undefined} /></div>;
  }
  return editable ? <EditInPlace field={field} value={value} how={how!} onCommit={commit} anchor={anchor} />
    : <div className="computed-value"><CellValue field={field} value={value} view={how!} /></div>;
}

function EditInPlace({ field, value, how, onCommit, anchor }: { field: Field; value: any; how: FieldView; onCommit: (v: any) => void; anchor?: string }) {
  const [editing, setEditing] = useState(false);
  if (editing) return <FieldInput field={field} value={value} onCommit={onCommit} onDone={() => setEditing(false)} autoFocus anchor={anchor} />;
  const empty = value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length);
  return (
    <button type="button" className="display-edit" title="Click to change" onClick={() => setEditing(true)}>
      {empty && how !== "yesno" ? <span className="muted">Empty</span> : <CellValue field={field} value={value ?? false} view={how} />}
    </button>
  );
}

interface Linked { fields: Field[]; records: Rec[]; refs: Refs }

/** Some records of another table, every field computed (refetched when the ids change, or `version` does). */
export function useLinked(tableId: string, ids: string[], version: number): { data: Linked | null; error: string } {
  const [data, setData] = useState<Linked | null>(null);
  const [error, setError] = useState("");
  const key = ids.join(",");
  useEffect(() => {
    if (!tableId) return;
    if (!ids.length) { setData((d) => d && { ...d, records: [] }); return; }
    let live = true;
    api.post<Linked>(`/tables/${tableId}/records/lookup`, { ids })
      .then((d) => { if (live) { setData(d); setError(""); } })
      .catch((e) => live && setError((e as Error).message));
    return () => { live = false; };
  }, [tableId, key, version]);
  return { data, error };
}

/** The fields the list / table / cards show: the field's choice, else the title and the next few. */
export function columns(field: Pick<Field, "options">, fields: Field[]): Field[] {
  const usable = fields.filter((f) => f.type !== "button" && f.type !== "record_id");
  const chosen = (field.options.views?.fields ?? []).map((id) => usable.find((f) => f.id === id)).filter((f): f is Field => !!f);
  if (chosen.length) return chosen;
  const p = primaryField(fields);
  return [...(p ? [p] : []), ...usable.filter((f) => f !== p && f.type !== "long_text" && !BUILTIN_KEYS.has(f.system_key ?? "")).slice(0, 3)];
}

/** A link's records as a list, a table or cards: open one (it opens over the page, editable), unlink it, link more. */
export function LinkedRecords({ field, ids, view: chosen, editable, onChange }: {
  field: Field; ids: string[]; view: "list" | "table" | "cards" | "hcards"; editable: boolean; onChange: (ids: string[]) => void;
}) {
  const narrow = useNarrow();
  const view = narrow && chosen === "table" ? "cards" : chosen;   // a phone: a table's rows as cards (nothing to scroll sideways)
  const tid = field.options.foreignTableId ?? "";
  const [version, setVersion] = useState(0);
  const { data, error } = useLinked(tid, ids, version);
  const [peek, setPeek] = useState<string | null>(null);
  const outer = useContext(RefsContext);
  // the linked records' own links and users, as well as this table's
  const refs = useMemo<Refs>(() => data ? {
    records: { ...outer.refs.records, ...data.refs.records }, users: { ...outer.refs.users, ...data.refs.users },
    display: { ...outer.refs.display, ...data.refs.display },
  } : outer.refs, [data, outer.refs]);
  const byId = useMemo(() => new Map((data?.records ?? []).map((r) => [r.id, r])), [data]);
  const cols = data ? columns(field, data.fields) : [];
  const title = (id: string) => { const r = byId.get(id); return r && data ? recordTitle(data.fields, r) : outer.refs.records[id] || "Untitled"; };
  const unlink = (id: string) => onChange(ids.filter((x) => x !== id));
  const picker = editable && <LinkPicker field={field} value={ids} onCommit={onChange} />;

  if (error && !data) {  // (e.g. a public link, or a table this user can't open): the titles, as chips
    return <div className="linked-fallback"><CellValue field={field} value={ids} view="chips" />{picker}</div>;
  }
  const actions = (id: string) => (
    <span className="linked-actions">
      <button type="button" className="icon-btn sm" title="Open" aria-label={`Open ${title(id)}`} onClick={(e) => { e.stopPropagation(); setPeek(id); }}>
        <Icon name="expand" size={13} /></button>
      {editable && <button type="button" className="icon-btn sm" title="Unlink" aria-label={`Unlink ${title(id)}`} onClick={(e) => { e.stopPropagation(); unlink(id); }}>
        <Icon name="x" size={13} /></button>}
    </span>
  );
  const body = !ids.length ? <div className="linked-empty muted">No linked records</div>
    : !data ? <div className="linked-empty muted">Loading…</div>
    : <RecordsDisplay ids={ids} byId={byId} fields={data.fields} cols={cols} view={view} title={title} actions={actions} onOpen={setPeek} />;
  const open = peek && data ? byId.get(peek) : undefined;
  return (
    <RefsContext.Provider value={{ ...outer, refs }}>
      <div className={`linked-records linked-as-${view}`}>
        {body}
        {picker}
      </div>
      {open && data && <PeekRecord tableId={tid} fields={data.fields} rec={open} onClose={() => setPeek(null)} onSaved={() => setVersion((v) => v + 1)} />}
    </RefsContext.Provider>
  );
}

/** Records of another table as a list, a table or cards (in a row): a link's, or a page's related list. */
export function RecordsDisplay({ ids, byId, fields, cols, view, title, actions, onOpen }: {
  ids: string[]; byId: Map<string, Rec>; fields: Field[]; cols: Field[]; view: "list" | "table" | "cards" | "hcards";
  title: (id: string) => string; actions: (id: string) => ReactNode; onOpen: (id: string) => void;
}) {
  if (view === "table") {
    return (
      <div className="linked-table-wrap">
        <table className="linked-table">
          <thead><tr><th className="linked-act-col" aria-label="Actions" />{cols.map((c) => <th key={c.id}><FieldIcon type={c.type} /> {c.name}</th>)}</tr></thead>
          <tbody>
            {ids.map((id) => {
              const r = byId.get(id);
              return (
                <tr key={id} onClick={() => onOpen(id)}>
                  <td className="linked-act-col">{actions(id)}</td>
                  {cols.map((c) => <td key={c.id}>{r ? <CellValue field={c} value={r.data[c.id]} /> : c.is_primary ? title(id) : null}</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }
  if (view === "cards" || view === "hcards") {
    return (
      <div className={view === "hcards" ? "linked-cards linked-row" : "linked-cards"}>
        {ids.map((id) => {
          const r = byId.get(id);
          const cover = r && fields.map((f) => fileItems(r.data[f.id])).flat().find((a: Attachment) => thumbUrl(a));
          return (
            <div key={id} className="linked-card" role="button" tabIndex={0} onClick={() => onOpen(id)} onKeyDown={(e) => e.key === "Enter" && onOpen(id)}>
              {cover && <div className="linked-card-cover"><img src={thumbUrl(cover)!} alt="" loading="lazy" /></div>}
              <div className="linked-card-head"><b>{title(id)}</b>{actions(id)}</div>
              {r && cols.filter((c) => !c.is_primary).map((c) => {
                const v = r.data[c.id];
                if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return null;
                return <div key={c.id} className="linked-card-field"><span className="muted small">{c.name}</span><CellValue field={c} value={v} /></div>;
              })}
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <ul className="linked-list">
      {ids.map((id) => {
        const r = byId.get(id);
        return (
          <li key={id} onClick={() => onOpen(id)}>
            <span className="linked-list-title">{title(id)}</span>
            {r && cols.filter((c) => !c.is_primary).map((c) => {
              const v = r.data[c.id];
              if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return null;
              return <span key={c.id} className="linked-list-prop"><span className="muted small">{c.name}</span> <CellValue field={c} value={v} /></span>;
            })}
            {actions(id)}
          </li>
        );
      })}
    </ul>
  );
}

/** A linked record opened over the page: its fields, editable (saved to its own table). */
export function PeekRecord({ tableId, fields, rec, onClose, onSaved }: {
  tableId: string; fields: Field[]; rec: Rec; onClose: () => void; onSaved: () => void;
}) {
  const [cur, setCur] = useState(rec);
  useEffect(() => setCur(rec), [rec]);
  const ds: Dataset = { kind: "table", id: tableId, name: "", table: { id: tableId, kind: "generic" } as TableInfo, fields, views: [], records: [cur], refs: { records: {}, users: {} } };
  const update = (patch: Record<string, any>) => {
    setCur((r) => ({ ...r, data: { ...r.data, ...patch } }));
    api.patch<Rec>(`/records/${rec.id}`, { data: patch }).then((r) => { setCur(r); onSaved(); }).catch((e) => showToast((e as Error).message));
  };
  return (
    <RecordModal ds={ds} rec={cur} fields={fields} onClose={onClose} history={false} can={{ add: false, edit: true, delete: false, manage: false }}
      link={`${location.origin}${location.pathname}#/table/${tableId}?record=${rec.id}`}
      onUpdate={update} onDelete={() => undefined} onButton={() => showToast("Open the record in its own table to use its buttons")} />
  );
}

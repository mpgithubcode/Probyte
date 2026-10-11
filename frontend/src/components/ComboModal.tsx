import { useEffect, useState } from "react";
import { api } from "../api";
import type { Field, TableInfo } from "../types";
import { PALETTE } from "../util";
import { Icon, tableIcon } from "./Icon";
import { Modal } from "./Overlay";

interface SourceSel { table_id: string; color: string; name?: string }
interface UField { id: string; name: string; mapping: Record<string, string> }

/** Create a combined database, or edit an existing one's sources and field mapping. */
export function ComboModal({ tables, baseId, combo, initialTables, initialName, onClose, onSaved, onDeleted }: {
  tables: TableInfo[];
  baseId?: string;
  initialTables?: string[];   // new combined database: tables picked to start with
  initialName?: string;
  combo?: { id: string; name: string; sources: SourceSel[]; fields: UField[] };
  onClose: () => void;
  onSaved: (id: string) => void;
  onDeleted?: () => void;
}) {
  const [name, setName] = useState(combo?.name ?? initialName ?? "");
  const [sources, setSources] = useState<SourceSel[]>(combo?.sources
    ?? (initialTables ?? []).map((id, i) => ({ table_id: id, color: PALETTE[i % PALETTE.length] })));
  const [fields, setFields] = useState<UField[]>(combo?.fields ?? []);
  const [tableFields, setTableFields] = useState<Record<string, Field[]>>({});
  const [tab, setTab] = useState<"sources" | "mapping">("sources");
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  // a table from another Probyte's public link, added as a linked table and picked as a source
  const [added, setAdded] = useState<TableInfo[]>([]);
  const [linkUrl, setLinkUrl] = useState("");
  const [linkPw, setLinkPw] = useState("");
  const [needsPw, setNeedsPw] = useState(false);
  const [linking, setLinking] = useState(false);
  const allTables = [...tables, ...added.filter((a) => !tables.some((t) => t.id === a.id))];

  const addFromLink = async () => {
    setError("");
    setLinking(true);
    try {
      const meta = await api.post<{ needs_password: boolean; view_hint: string | null; items: { id: string; name: string; views: { id: string }[] }[] }>(
        "/remote/inspect", { url: linkUrl.trim(), password: linkPw || undefined });
      if (meta.needs_password) { setNeedsPw(true); setError("This link has a password"); return; }
      const item = meta.items.find((i) => i.views.some((v) => v.id === meta.view_hint)) ?? meta.items[0];
      if (!item) { setError("Nothing is shared through this link"); return; }
      const view = item.views.find((v) => v.id === meta.view_hint) ?? item.views[0];
      const t = await api.post<TableInfo>("/tables", { name: item.name, kind: "remote", base_id: baseId,
        remote: { url: linkUrl.trim(), password: linkPw || undefined, item_id: item.id, view_id: view.id } });
      setAdded((a) => [...a, t]);
      setSources((s) => [...s, { table_id: t.id, color: PALETTE[s.length % PALETTE.length], name: t.name }]);
      setLinkUrl(""); setLinkPw(""); setNeedsPw(false);
    } catch (e) {
      const msg = (e as Error).message;
      if (msg === "password_required") { setNeedsPw(true); setError(linkPw ? "Wrong password" : "This link has a password"); }
      else setError(msg);
    } finally { setLinking(false); }
  };

  useEffect(() => {
    if (!combo) return;
    for (const s of sources) {
      if (tableFields[s.table_id]) continue;
      api.get<{ fields: Field[] }>(`/tables/${s.table_id}`).then((r) =>
        setTableFields((tf) => ({ ...tf, [s.table_id]: r.fields })));
    }
  }, [combo, sources, tableFields]);

  const toggle = (t: TableInfo) => {
    setSources((s) => s.some((x) => x.table_id === t.id)
      ? s.filter((x) => x.table_id !== t.id)
      : [...s, { table_id: t.id, color: PALETTE[s.length % PALETTE.length] }]);
  };

  const save = async (remap = false) => {
    if (!name.trim()) return setError("Give it a name");
    if (!sources.length) return setError("Pick at least one database");
    try {
      if (combo) {
        const sourcesChanged = sources.map((s) => s.table_id).join() !== combo.sources.map((s) => s.table_id).join();
        await api.patch(`/combos/${combo.id}`, { name: name.trim(), sources, fields, remap: remap || sourcesChanged });
        onSaved(combo.id);
      } else {
        const c = await api.post<{ id: string }>("/combos", { name: name.trim(), sources, base_id: baseId });
        onSaved(c.id);
      }
      onClose();
    } catch (e) { setError((e as Error).message); }
  };

  const remove = async () => {
    await api.del(`/combos/${combo!.id}`);
    onDeleted?.();
    onClose();
  };

  const setMapping = (fid: string, tid: string, sourceField: string) =>
    setFields(fields.map((f) => f.id !== fid ? f : {
      ...f, mapping: sourceField ? { ...f.mapping, [tid]: sourceField } : Object.fromEntries(Object.entries(f.mapping).filter(([k]) => k !== tid)),
    }));

  const tableName = (id: string) =>
    allTables.find((t) => t.id === id)?.name ?? combo?.sources.find((s) => s.table_id === id)?.name ?? "?";

  return (
    <Modal title={combo ? "Edit combined database" : "New combined database"} width={combo && tab === "mapping" ? 760 : 520} onClose={onClose}
      footer={<>
        {combo && (confirmDelete
          ? <><button className="btn danger" onClick={remove}>Delete</button><button className="btn" onClick={() => setConfirmDelete(false)}>Cancel</button></>
          : <button className="btn danger-ghost" onClick={() => setConfirmDelete(true)}><Icon name="trash" size={14} /> Delete</button>)}
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={() => save()}>{combo ? "Save" : "Create"}</button>
      </>}>
      <div className="form">
        <label className="stack">Name
          <input className="input" autoFocus value={name} placeholder="Agenda" onChange={(e) => setName(e.target.value)} />
        </label>
        {combo && (
          <div className="tabs">
            <button className={tab === "sources" ? "active" : ""} onClick={() => setTab("sources")}>Sources</button>
            <button className={tab === "mapping" ? "active" : ""} onClick={() => setTab("mapping")}>Field mapping</button>
          </div>
        )}

        {tab === "sources" && (
          <div className="stack">
            <span>Tables to show together</span>
            <div className="source-list">
              {allTables.map((t) => {
                const sel = sources.find((s) => s.table_id === t.id);
                return (
                  <div key={t.id} className={`source-row ${sel ? "active" : ""}`}>
                    <label className="inline-check">
                      <input type="checkbox" checked={!!sel} onChange={() => toggle(t)} />
                      <Icon name={tableIcon(t.kind)} size={14} /> {t.name}
                    </label>
                    <span className="spacer" />
                    {sel && <input type="color" value={sel.color} title="Color"
                      onChange={(e) => setSources(sources.map((s) => s.table_id === t.id ? { ...s, color: e.target.value } : s))} />}
                  </div>
                );
              })}
              {!allTables.length && <p className="muted">Create some tables in this base first, or add one from a link below.</p>}
            </div>
            <div className="stack combo-link">
              <span className="muted small">From another Probyte (a public link, on this server or any other)</span>
              <div className="row">
                <input className="input" value={linkUrl} placeholder="https://their-probyte.example.com/s/AbC123"
                  onChange={(e) => setLinkUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && linkUrl.trim() && addFromLink()} />
                {needsPw && <input className="input" type="password" value={linkPw} placeholder="Password" style={{ maxWidth: 160 }}
                  onChange={(e) => setLinkPw(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addFromLink()} />}
                <button className="btn" disabled={!linkUrl.trim() || linking} onClick={addFromLink}>
                  {linking ? <Icon name="sync" size={14} className="spin" /> : <Icon name="link" size={14} />} Add
                </button>
              </div>
            </div>
            <p className="hint">Fields are matched automatically (Title ↔ Title, Due ↔ End, Tags ↔ Tags…). You can adjust the mapping afterwards.</p>
          </div>
        )}

        {tab === "mapping" && combo && (
          <div className="mapping-wrap">
            <table className="mapping">
              <thead>
                <tr>
                  <th>Combined field</th>
                  {sources.map((s) => <th key={s.table_id}><span className="swatch" style={{ background: s.color }} /> {tableName(s.table_id)}</th>)}
                  <th />
                </tr>
              </thead>
              <tbody>
                {fields.map((f) => (
                  <tr key={f.id}>
                    <td data-label="Combined field"><input className="input" value={f.name} onChange={(e) => setFields(fields.map((x) => x.id === f.id ? { ...x, name: e.target.value } : x))} /></td>
                    {sources.map((s) => (
                      <td key={s.table_id} data-label={tableName(s.table_id)}>
                        <select className="input" value={f.mapping[s.table_id] ?? ""} onChange={(e) => setMapping(f.id, s.table_id, e.target.value)}>
                          <option value="">—</option>
                          {(tableFields[s.table_id] ?? []).map((tf) => <option key={tf.id} value={tf.id}>{tf.name}</option>)}
                        </select>
                      </td>
                    ))}
                    <td className="mapping-remove"><button className="icon-btn" title="Remove this combined field" onClick={() => setFields(fields.filter((x) => x.id !== f.id))}><Icon name="x" size={14} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row">
              <button className="ghost-btn" onClick={() => setFields([...fields, { id: `f_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`, name: "New field", mapping: {} }])}>
                <Icon name="plus" size={14} /> Add combined field
              </button>
              <span className="spacer" />
              <button className="ghost-btn" onClick={() => save(true)}><Icon name="sync" size={14} /> Reset to automatic mapping</button>
            </div>
            <p className="hint">The first field is used as the record title. Values of mapped fields are read from and written to the source database.</p>
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}

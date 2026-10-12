import { useEffect, useState } from "react";
import { api } from "../api";
import type { Base, Dataset, Field, Storage } from "../types";
import { FIELD_TYPES, timeAgo } from "../util";
import { EmojiPicker } from "./EmojiPicker";
import { FieldIcon, Icon, tableIcon } from "./Icon";
import { Modal, Popover, usePopover } from "./Overlay";
import { StorageEditor } from "./StorageForm";
import { TemplateActions } from "./Templates";

type Section = "general" | "fields" | "sync" | "sharing" | "data" | "template" | "delete";

const KIND_LABEL: Record<string, string> = {
  generic: "Table", tasks: "Tasks (synced calendar)", events: "Events (synced calendar)", mixed: "Tasks & events (synced calendar)",
  contacts: "Contacts (synced address book)", remote: "Linked table (from a public link)", files: "Files (S3 / WebDAV storage)",
};

export interface TableSettingsActions {
  rename: (name: string) => void;
  setIcon: (icon: string) => void;
  setDescription: (text: string) => void;
  moveTo: (baseId: string) => void;
  toggleHidden: () => void;
  editField: (f: Field | null) => void;
  fieldsReordered: () => void;
  syncNow: () => void;
  importFile?: () => void;
  importVcards?: () => void;   // contacts tables
  exportData: () => void;
  combine?: () => void;
  sources?: () => void;
  linkSettings?: () => void;
  feeds?: () => void;
  remove: (deleteOnServer: boolean | null) => void;
}

/** Everything about a table (or combined database) in one window. Only what applies to this kind of table is shown:
 * sync for calendar / contacts tables, the storage for files tables, the link for linked tables, sources for combined
 * databases, calendar links where there are dates. */
export function TableSettings({ ds, bases, currentBase, hiddenInSidebar, syncing, canManage, actions, onClose, initial = "general" }: {
  ds: Dataset; bases: Base[]; currentBase?: string; hiddenInSidebar?: boolean; syncing: boolean; canManage: boolean;
  actions: TableSettingsActions; onClose: () => void; initial?: Section;
}) {
  const t = ds.table;
  const kind = ds.kind === "combo" ? "combo" : t?.kind ?? "generic";
  const synced = ["tasks", "events", "mixed", "contacts"].includes(kind);
  const files = kind === "files";
  const remote = kind === "remote";
  const hasDates = ds.fields.some((f) => f.type === "date" || f.type === "created_time" || f.type === "last_modified_time");
  const sections: { id: Section; label: string }[] = [
    { id: "general", label: "General" },
    ...(ds.kind === "table" && !remote ? [{ id: "fields" as Section, label: "Fields" }] : []),
    ...(synced ? [{ id: "sync" as Section, label: "Sync" }] : files ? [{ id: "sync" as Section, label: "Storage" }]
      : remote ? [{ id: "sync" as Section, label: "Link" }] : ds.kind === "combo" ? [{ id: "sync" as Section, label: "Sources" }] : []),
    ...(actions.feeds && hasDates && !files ? [{ id: "sharing" as Section, label: "Calendar links" }] : []),
    { id: "data", label: "Import & export" },
    ...(ds.kind === "table" && !remote && !files ? [{ id: "template" as Section, label: "Template" }] : []),
    { id: "delete", label: "Delete" },
  ];
  const [section, setSection] = useState<Section>(sections.some((s) => s.id === initial) ? initial : "general");
  const word = ds.kind === "combo" ? "combined database" : "table";

  return (
    <Modal title={<span className="row"><Icon name="settings" size={16} /> {ds.kind === "combo" ? "Combined database" : "Table"} settings</span>}
      width={760} onClose={onClose} footer={<><span className="spacer" /><button className="btn primary" onClick={onClose}>Done</button></>}>
      <div className="settings-split">
        <nav className="settings-nav">
          {sections.map((s) => <button key={s.id} className={section === s.id ? "active" : ""} onClick={() => setSection(s.id)}>{s.label}</button>)}
        </nav>
        <div className="settings-pane">
          {section === "general" && <General ds={ds} kind={kind} bases={bases} currentBase={currentBase} hiddenInSidebar={hiddenInSidebar} actions={actions} word={word} />}
          {section === "fields" && <Fields ds={ds} canManage={canManage} actions={actions} />}
          {section === "sync" && synced && t && (
            <div className="form">
              <dl className="settings-facts">
                <dt>Account</dt><dd>{ds.dav?.account ?? "—"}{ds.dav?.builtin ? " (Probyte's own server: your phone syncs with it)" : ""}</dd>
                <dt>{ds.dav?.collection === "address book" ? "Address book" : "Calendar"}</dt><dd className="mono small">{t.calendar_url}</dd>
                <dt>Items on the server</dt><dd>{ds.dav?.items ?? "—"}{ds.dav?.shared ? " · another table uses the same one" : ""}</dd>
                <dt>Last sync</dt><dd>{t.last_synced_at ? timeAgo(t.last_synced_at) : "never"}{t.last_sync_error && <div className="error small">{t.last_sync_error}</div>}</dd>
              </dl>
              <div className="settings-actions">
                <button className="btn sm" disabled={syncing} onClick={actions.syncNow}><Icon name="sync" size={14} className={syncing ? "spin" : ""} /> {syncing ? "Syncing…" : "Sync now"}</button>
              </div>
              <p className="hint">Changes here are sent to the server within seconds; changes made on phones arrive with the next sync (every few minutes, or now). Phone setup: Settings → Phone sync.</p>
            </div>
          )}
          {section === "sync" && files && t && <StoragePane ds={ds} syncing={syncing} actions={actions} />}
          {section === "sync" && remote && t?.remote && (
            <div className="form">
              <dl className="settings-facts">
                <dt>Shared as</dt><dd>{t.remote.name ?? "—"}</dd>
                <dt>From</dt><dd className="mono small">{t.remote.url}</dd>
                <dt>You can</dt><dd>{["view", t.remote.permissions?.add && "add", t.remote.permissions?.edit && "edit", t.remote.permissions?.delete && "delete"].filter(Boolean).join(", ")}</dd>
                {t.remote.error && (<><dt>Problem</dt><dd className="error small">{t.remote.error}</dd></>)}
              </dl>
              <div className="settings-actions">
                {actions.linkSettings && <button className="btn sm" onClick={actions.linkSettings}><Icon name="link" size={14} /> Change the link…</button>}
                <button className="btn sm" disabled={syncing} onClick={actions.syncNow}><Icon name="sync" size={14} className={syncing ? "spin" : ""} /> Refresh</button>
              </div>
            </div>
          )}
          {section === "sync" && ds.kind === "combo" && (
            <div className="form">
              <p className="muted small">The tables shown together here, and how their fields line up.</p>
              <div className="settings-list">
                {ds.sources?.map((s) => <div key={s.table_id} className="settings-row"><span className="swatch" style={{ background: s.color }} /> {s.name}</div>)}
              </div>
              {actions.sources && <div className="settings-actions"><button className="btn sm" onClick={actions.sources}><Icon name="layers" size={14} /> Sources & field mapping…</button></div>}
            </div>
          )}
          {section === "sharing" && actions.feeds && (
            <div className="form">
              <p>A secret, read-only address that Apple Calendar, Google Calendar, Outlook and other calendar apps can subscribe to. It can follow a view (its filters and date fields).</p>
              <div className="settings-actions"><button className="btn sm" onClick={actions.feeds}><Icon name="calendar" size={14} /> Calendar subscription links…</button></div>
              <p className="hint">Public links (pages anyone can open or install as an app, with the rights you choose) are made in Settings → Public links.</p>
            </div>
          )}
          {section === "data" && (
            <div className="form">
              {actions.importFile && (<div className="settings-item">
                <div><b>Import from a file</b><span className="muted small">CSV, Excel, OpenDocument, Numbers or JSON: rows become records.</span></div>
                <button className="btn sm" onClick={actions.importFile}><Icon name="upload" size={14} /> Import…</button>
              </div>)}
              {actions.importVcards && (<div className="settings-item">
                <div><b>Import contacts</b><span className="muted small">vCard files (.vcf) from a phone, Google, Outlook or iCloud. You can also drop them on the table.</span></div>
                <button className="btn sm" onClick={actions.importVcards}><Icon name="contacts" size={14} /> Import .vcf…</button>
              </div>)}
              {kind === "contacts" && (<div className="settings-item">
                <div><b>Download as vCard</b><span className="muted small">Every contact in one .vcf file, for a phone, another app or a backup.</span></div>
                <a className="btn sm" href={`/api/tables/${ds.id}/vcards`}><Icon name="download" size={14} /> Download .vcf</a>
              </div>)}
              <div className="settings-item">
                <div><b>Export</b><span className="muted small">The current view (or everything) as xlsx, csv, tsv, ods, xls or json.</span></div>
                <button className="btn sm" onClick={actions.exportData}><Icon name="download" size={14} /> Export…</button>
              </div>
              {actions.combine && (<div className="settings-item">
                <div><b>Combine with other tables</b><span className="muted small">Show this table together with others in one calendar, board or list.</span></div>
                <button className="btn sm" onClick={actions.combine}><Icon name="layers" size={14} /> Combine…</button>
              </div>)}
              {files && <p className="hint">Files are added with Upload (or by dropping them on the page), not imported.</p>}
            </div>
          )}
          {section === "template" && <TemplateActions kind="table" tableId={ds.id} defaultName={ds.name} records={ds.records.length} />}
          {section === "delete" && <DeletePane ds={ds} files={files} word={word} actions={actions} />}
        </div>
      </div>
    </Modal>
  );
}

function General({ ds, kind, bases, currentBase, hiddenInSidebar, actions, word }: {
  ds: Dataset; kind: string; bases: Base[]; currentBase?: string; hiddenInSidebar?: boolean; actions: TableSettingsActions; word: string;
}) {
  const [name, setName] = useState(ds.name);
  const [desc, setDesc] = useState((ds.kind === "combo" ? ds.description : ds.table?.description) ?? "");
  const iconPop = usePopover();
  const icon = ds.kind === "combo" ? ds.icon : ds.table?.icon;
  return (
    <div className="form">
      <div className="row settings-name">
        <button className="title-icon-btn big" title="Change icon" aria-label="Change icon" onClick={iconPop.open}>
          {icon ? <span className="emoji-icon big">{icon}</span> : <Icon name={ds.kind === "combo" ? "layers" : tableIcon(kind)} size={22} />}
        </button>
        {iconPop.anchor && (
          <Popover anchor={iconPop.anchor} onClose={iconPop.close} width={300}>
            <EmojiPicker onPick={(e) => { actions.setIcon(e); iconPop.close(); }} onRemove={icon ? () => { actions.setIcon(""); iconPop.close(); } : undefined} />
          </Popover>
        )}
        <label className="stack" style={{ flex: 1 }}>Name
          <input className="input" value={name} onChange={(e) => setName(e.target.value)}
            onBlur={() => name.trim() && name.trim() !== ds.name && actions.rename(name.trim())}
            onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()} />
        </label>
      </div>
      <label className="stack">Description
        <textarea className="input" rows={3} value={desc} placeholder={`What this ${word} is for`} onChange={(e) => setDesc(e.target.value)}
          onBlur={() => desc !== ((ds.kind === "combo" ? ds.description : ds.table?.description) ?? "") && actions.setDescription(desc)} />
      </label>
      <dl className="settings-facts">
        <dt>Kind</dt><dd>{ds.kind === "combo" ? "Combined database" : KIND_LABEL[kind] ?? kind}</dd>
        <dt>Records</dt><dd>{ds.records.length.toLocaleString()}</dd>
        <dt>{ds.kind === "combo" ? "Combined database" : "Table"} ID</dt>
        <dd className="row"><code className="mono small">{ds.id}</code>
          <button className="icon-btn" title="Copy" onClick={() => navigator.clipboard?.writeText(ds.id)}><Icon name="copy" size={13} /></button></dd>
      </dl>
      {bases.length > 1 && (
        <label className="stack">Base
          <select className="input" value={currentBase} onChange={(e) => actions.moveTo(e.target.value)}>
            {bases.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </label>
      )}
      <label className="toggle-row">
        <span className="stack-tight"><span>Show in the sidebar</span><span className="hint">Hidden ones are listed under “Show hidden” in their base.</span></span>
        <span className="switch"><input type="checkbox" checked={!hiddenInSidebar} onChange={actions.toggleHidden} /><span /></span>
      </label>
    </div>
  );
}

/** The table's own fields, in the table's order (drag to change it); views can still show them their own way. */
function Fields({ ds, canManage, actions }: { ds: Dataset; canManage: boolean; actions: TableSettingsActions }) {
  const [order, setOrder] = useState(() => ds.fields.map((f) => f.id));
  const [drag, setDrag] = useState<string | null>(null);
  useEffect(() => setOrder(ds.fields.map((f) => f.id)), [ds.fields]);
  const byId = new Map(ds.fields.map((f) => [f.id, f]));
  const list = order.map((id) => byId.get(id)).filter((f): f is Field => !!f);
  const save = async (ids: string[]) => {
    await api.post(`/tables/${ds.id}/fields/order`, { ids }).catch(() => undefined);
    actions.fieldsReordered();
  };
  return (
    <div className="form">
      <p className="muted small">The table's field order (new views use it; a view can still have its own). Locked fields come from the {ds.table?.kind === "files" ? "file storage" : "synced calendar or address book"} and keep their names.</p>
      <div className="settings-list">
        {list.map((f) => (
          <div key={f.id} className={`settings-row ${drag === f.id ? "dragging" : ""}`} draggable={canManage && f.type !== "record_id"}
            onDragStart={() => setDrag(f.id)}
            onDragOver={(e) => {
              if (!drag || drag === f.id || f.type === "record_id") return;
              e.preventDefault();
              const ids = order.filter((x) => x !== drag);
              ids.splice(ids.indexOf(f.id) + (order.indexOf(drag) < order.indexOf(f.id) ? 1 : 0), 0, drag);
              setOrder(ids);
            }}
            onDragEnd={() => { if (drag) save(order); setDrag(null); }}>
            {canManage && f.type !== "record_id" ? <span className="drag-handle" title="Drag to reorder">⋮⋮</span> : <span className="drag-handle" />}
            <FieldIcon type={f.type} />
            <span className="settings-row-name">{f.name}{f.required && <span className="req-mark"> *</span>}</span>
            <span className="muted small">{FIELD_TYPES.find((x) => x.type === f.type)?.label ?? f.type}</span>
            {f.system_key && f.system_key !== "record_id" && <Icon name="lock" size={12} className="muted" />}
            <span className="spacer" />
            {canManage && <button className="btn sm" onClick={() => actions.editField(f)}>{f.system_key ? "View" : "Edit"}</button>}
          </div>
        ))}
      </div>
      {canManage && <div className="settings-actions"><button className="btn sm" onClick={() => actions.editField(null)}><Icon name="plus" size={14} /> Add field</button></div>}
    </div>
  );
}

function StoragePane({ ds, syncing, actions }: { ds: Dataset; syncing: boolean; actions: TableSettingsActions }) {
  const t = ds.table!;
  const [storage, setStorage] = useState<Storage | null>(null);
  const [edit, setEdit] = useState(false);
  const load = () => api.get<Storage[]>("/storages").then((l) => setStorage(l.find((s) => s.id === t.storage_id) ?? null)).catch(() => undefined);
  useEffect(() => { load(); }, [t.storage_id]);
  return (
    <div className="form">
      <dl className="settings-facts">
        <dt>Connection</dt><dd>{ds.storage?.name ?? "—"} {ds.storage?.kind && <span className="badge">{ds.storage.kind === "s3" ? "S3" : "WebDAV"}</span>}</dd>
        <dt>Where</dt><dd className="mono small">{ds.storage?.location ?? "—"}</dd>
        <dt>Files</dt><dd>{ds.records.length.toLocaleString()}</dd>
        <dt>Last sync</dt><dd>{t.last_synced_at ? timeAgo(t.last_synced_at) : "never"}{t.last_sync_error && <div className="error small">{t.last_sync_error}</div>}</dd>
      </dl>
      <div className="settings-actions">
        <button className="btn sm" disabled={syncing} onClick={actions.syncNow}><Icon name="sync" size={14} className={syncing ? "spin" : ""} /> {syncing ? "Syncing…" : "Sync now"}</button>
        {storage && <button className="btn sm" onClick={() => setEdit(true)}><Icon name="key" size={14} /> Edit connection…</button>}
      </div>
      <p className="hint">Files added, changed or removed in the storage itself show up at the next sync (every few minutes, or now). The connection's address and keys can be changed (e.g. a new access key); the folder a table shows is fixed: make another files table for another folder.</p>
      {edit && storage && <StorageEditor storage={storage} onClose={() => setEdit(false)} onSaved={() => { setEdit(false); load(); actions.syncNow(); }} />}
    </div>
  );
}

function DeletePane({ ds, files, word, actions }: { ds: Dataset; files: boolean; word: string; actions: TableSettingsActions }) {
  const [onServer, setOnServer] = useState<boolean | null>(null);
  const [confirm, setConfirm] = useState(false);
  return (
    <div className="form">
      {ds.kind === "combo"
        ? <p>Deleting a combined database removes only this combined view; its source tables and their records stay.</p>
        : <p>The table goes to the <b>trash</b>, where it can be restored for 30 days, then it's deleted for good.</p>}
      {files && <p className="hint">The files stay in {ds.storage?.location ?? "the storage"}: deleting a files table never deletes files.</p>}
      {ds.dav && (ds.dav.shared
        ? <p className="hint">The {ds.dav.collection} on the server is kept: another table uses it too.</p>
        : <label className="inline-check">
            <input type="checkbox" checked={onServer ?? ds.dav.builtin} onChange={(e) => setOnServer(e.target.checked)} />
            <span>{ds.dav.builtin ? `When the trash is emptied, also delete the ${ds.dav.collection} on Probyte's server (phones lose it then)`
              : `When the trash is emptied, also delete its ${ds.dav.items} item${ds.dav.items === 1 ? "" : "s"} from ${ds.dav.account ?? "the account"}`}</span>
          </label>)}
      <div className="settings-actions">
        {confirm
          ? <><button className="btn danger" onClick={() => actions.remove(onServer)}>{ds.kind === "combo" ? "Delete" : "Move to trash"}</button>
            <button className="btn" onClick={() => setConfirm(false)}>Cancel</button></>
          : <button className="btn danger-ghost" onClick={() => setConfirm(true)}><Icon name="trash" size={14} /> {ds.kind === "combo" ? `Delete this ${word}` : "Move to trash…"}</button>}
      </div>
    </div>
  );
}

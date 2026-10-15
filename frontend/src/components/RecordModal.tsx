import { useContext, useEffect, useState, type ReactNode } from "react";
import { FileView, FullViewer } from "./FileViewer";
import type { Attachment, CreateTarget, Dataset, Field, Rec, RecordSection } from "../types";
import { addExdate, anchorOf, describe, findOverride, occurrences, occurrenceData, overrideKey, seriesDateFields, setOverride } from "../recur";
import { BUILTIN_KEYS, fieldApplies, fitField, formatDate, isEditable, nativeKind, recordKind, recordTitle, timeAgo } from "../util";
import { CellValue, Chip, FieldInput, RefsContext } from "./Cells";
import { CAN_ALL, type Can } from "./viewTypes";
import { FieldIcon, Icon } from "./Icon";
import { Modal, Popover, usePopover } from "./Overlay";
import { showToast } from "../toast";
import { RecordHistory } from "./RecordHistory";
import { RecordValue } from "./LinkedView";

export interface RecordNav { index: number; total: number; prev?: () => void; next?: () => void }

export function RecordModal({ ds, rec, fields, onUpdate, onDelete, onClose, onButton, can = CAN_ALL, occurrence, history = true,
  docked, nav, sections, onSections, onDuplicate, link, onCompleteOccurrence, onPage }: {
  onCompleteOccurrence?: (occurrence: string) => void;   // a repeating record: mark one of its dates done
  onPage?: () => void;  // the table has a page: open the record as one instead
  history?: boolean;  // show "who changed what" (not on public links)
  docked?: boolean;   // a panel beside the view (the app) instead of a dialog (public links)
  nav?: RecordNav;    // the record's place in the view: previous / next (↑ ↓, J / K)
  sections?: RecordSection[];                      // fields grouped under headings (the view's)
  onSections?: (s: RecordSection[]) => void;       // arrange them (left out: can't)
  onDuplicate?: () => void;
  link?: string;      // an address that opens this record
  ds: Dataset; rec: Rec; fields: Field[];
  onUpdate: (patch: Record<string, any>) => void; onDelete: () => void; onClose: () => void; onButton: (f: Field) => void;
  can?: Can;
  occurrence?: string | null;                              // opened from a later repeat on the calendar/timeline
}) {
  const [confirm, setConfirm] = useState(false);
  const [editOcc, setEditOcc] = useState(false);
  const [arranging, setArranging] = useState(false);
  const [closed, setClosed] = useState<Set<string>>(new Set());   // sections folded away
  const more = usePopover();
  useEffect(() => { setConfirm(false); setEditOcc(false); }, [rec.id]);
  // J / K (and the arrows in the header) go through the view's records, unless typing somewhere
  useEffect(() => {
    if (!nav) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, [contenteditable=true], .grid-wrap, .popover")) return;
      if (e.key === "j" || e.key === "J") { e.preventDefault(); nav.next?.(); }
      if (e.key === "k" || e.key === "K") { e.preventDefault(); nav.prev?.(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [nav]);
  const source = ds.sources?.find((s) => s.table_id === rec.table_id);
  const tkind = ds.kind === "table" ? nativeKind(ds.table) : source?.kind;  // a linked table: what its source is
  const files = tkind === "files";
  const caldav = tkind !== "generic" && tkind !== "remote" && !files;
  const ownServer = ds.kind === "table" && !!ds.dav?.builtin;  // on Probyte's own CalDAV server: nothing is ever pending
  // files tables: the file itself, shown at the top
  const fileF = files ? fields.find((f) => f.system_key === "file") : undefined;
  const fileItem = fileF ? (rec.data[fileF.id] as Attachment[] | undefined)?.[0] : undefined;
  const [viewing, setViewing] = useState(false);
  const repeatF = fields.find((f) => f.type === "recurrence");
  const kind = recordKind(fields, rec);
  // (Record ID and the built-in Created / Last modified fields are in the footer)
  const shown = fields.filter((f) => f.id !== "__source" && f.type !== "record_id" && !BUILTIN_KEYS.has(f.system_key ?? "") && fieldApplies(f, kind));
  const builtin = (key: string) => { const f = fields.find((x) => x.system_key === key); return f ? rec.data[f.id] : undefined; };
  const { refs } = useContext(RefsContext);
  const who = (id: unknown) => (typeof id === "string" && refs.users[id] ? ` by ${refs.users[id]}` : "");
  const added = (builtin("created_time") as string | undefined) ?? rec.created_at;
  const changed = builtin("modified_time") as string | undefined;
  // the view's sections, with only fields that are here; the rest at the top
  const secs = (sections ?? []).map((sec) => ({ ...sec, list: sec.fields.map((id) => shown.find((f) => f.id === id)).filter((f): f is Field => !!f) }));
  const placed = new Set(secs.flatMap((sec) => sec.list.map((f) => f.id)));
  const top = shown.filter((f) => !placed.has(f.id));
  // one occurrence of a repeating record
  const occ = occurrence && repeatF ? occurrence : null;
  const ov = occ ? findOverride(rec.data[repeatF!.id], occ) : undefined;
  const occData = occ ? occurrenceData(fields, rec, occ) : null;
  const primary = fields.find((f) => f.is_primary);
  const occFields = [...(primary ? [primary] : []), ...seriesDateFields(fields)].filter((f) => f.type !== "recurrence");
  const occStart = occData && seriesDateFields(fields).map((f) => occData[f.id]).find((v) => typeof v === "string");
  const setOcc = (patch: Record<string, any> | null) => onUpdate({ [repeatF!.id]: setOverride(rec.data[repeatF!.id], occ!, patch) });

  const row = (f: Field): ReactNode => (
          <div key={f.id} className="form-row">
            <span className="form-label" title={f.description ?? undefined}><FieldIcon type={f.type} /> {f.name}{f.required && <span className="req-mark" title="Required"> *</span>}</span>
            <div className="form-input">
              {f.type === "button" ? (
                <button className="cell-button" style={{ background: f.options.color ?? "var(--accent)" }}
                  disabled={!can.edit && f.options.action !== "url"} onClick={() => onButton(f)}>
                  {f.options.label ?? "Click"}
                </button>
              ) : f.mapping && rec.table_id && !f.mapping[rec.table_id] ? (
                <span className="muted">Not mapped for this source</span>
              ) : (
                <RecordValue field={fitField(f, kind, rec.data[f.id])} value={rec.data[f.id]} rec={rec} canEdit={can.edit} onUpdate={onUpdate}
                  anchor={f.type === "recurrence" ? anchorOf(fields, rec) : undefined} />
              )}
            </div>
          </div>
  );

  const copyLink = () => {
    if (!link) return;
    navigator.clipboard?.writeText(link).then(() => showToast("Link to the record copied"), () => showToast(link));
  };
  const actions = (<>
    {onPage && <button className="btn sm ghost" onClick={onPage} title="Open the record as a page"><Icon name="expand" size={14} /> Page</button>}
    {nav && nav.total > 1 && (
      <span className="record-nav">
        <button className="icon-btn" onClick={nav.prev} disabled={!nav.prev} title="Previous record (K)" aria-label="Previous record"><Icon name="up" size={16} /></button>
        <span className="muted small">{nav.index + 1} of {nav.total}</span>
        <button className="icon-btn" onClick={nav.next} disabled={!nav.next} title="Next record (J)" aria-label="Next record"><Icon name="down" size={16} /></button>
      </span>
    )}
    {(onDuplicate || link || onSections || can.delete) && (
      <button className="icon-btn" onClick={more.open} aria-label="Record actions" title="More"><Icon name="more" size={18} /></button>
    )}
    {more.anchor && (
      <Popover anchor={more.anchor} onClose={more.close} align="right" width={230}>
        <div className="menu-list">
          {onDuplicate && can.add && <button className="menu-item" onClick={() => { more.close(); onDuplicate(); }}><Icon name="copy" size={14} /> Duplicate</button>}
          {link && <button className="menu-item" onClick={() => { more.close(); copyLink(); }}><Icon name="link" size={14} /> Copy link</button>}
          {onSections && <button className="menu-item" onClick={() => { more.close(); setArranging(true); }}><Icon name="list" size={14} /> Arrange into sections…</button>}
          {can.delete && <button className="menu-item danger" onClick={() => { more.close(); setConfirm(true); }}><Icon name="trash" size={14} /> Delete…</button>}
        </div>
      </Popover>
    )}
  </>);

  return (
    <Modal width={docked ? 560 : 640} onClose={onClose} docked={docked} actions={actions} className="record-modal"
      title={<span className="record-title">{source && <Chip name={source.name} color={source.color} />}{recordTitle(fields, rec)}</span>}
      footer={<>
        {!can.delete ? null : confirm ? (
          <>
            <span className="muted">{files ? "Delete this file? It's deleted from the storage when the trash is emptied."
              : `Delete this record${caldav ? (ownServer ? " (also from phones)" : " (also from the CalDAV server)") : ""}?`}</span>
            <button className="btn danger" onClick={onDelete}>Delete</button>
            <button className="btn" onClick={() => setConfirm(false)}>Cancel</button>
          </>
        ) : (
          <button className="btn danger-ghost" onClick={() => setConfirm(true)}><Icon name="trash" size={14} /> Delete</button>
        )}
        <span className="spacer" />
        {!confirm && <span className="record-meta muted small">
          <code className="rid" title="Record ID: click to copy" onClick={() => navigator.clipboard?.writeText(rec.id).then(() => showToast("Record ID copied"))}>{rec.id.slice(0, 8)}</code>
          {added && <span title={formatDate(added, true)}> · added {timeAgo(added)}{who(builtin("created_by"))}</span>}
          {changed && added && Math.abs(Date.parse(changed) - Date.parse(added)) > 2000 &&
            <span title={formatDate(changed, true)}> · changed {timeAgo(changed)}{who(builtin("modified_by"))}</span>}
        </span>}
        {caldav && !ownServer && <span className="muted">{rec.sync_status === "dirty" ? "● Pending sync" : "✓ Synced"}</span>}
      </>}>
      {fileItem && (
        <div className="record-file">
          <FileView item={fileItem} onExpand={() => setViewing(true)} />
          {viewing && <FullViewer entries={[{ item: fileItem, rec }]} index={0} fields={fields} onClose={() => setViewing(false)} />}
        </div>
      )}
      <div className="form">
        {occ && occData && (
          <div className="occ-box">
            <div className="repeat-notice">
              <Icon name="repeat" size={14} />
              <span>
                Occurrence on <b>{formatDate(occStart ?? occ, String(occStart ?? occ).length > 10)}</b>
                {ov ? " · changed on its own" : ""}
              </span>
              {can.edit && !ov && !editOcc && <button className="btn sm" onClick={() => setEditOcc(true)}>Change this date only</button>}
              {can.edit && ov && <button className="btn sm" onClick={() => { setOcc(null); setEditOcc(false); }}>Reset to series</button>}
              {can.edit && onCompleteOccurrence && (
                <button className="btn sm" onClick={() => { onCompleteOccurrence(occ); onClose(); }}><Icon name="check" size={14} /> Mark this date done</button>
              )}
              {can.edit && (
                <button className="btn sm" onClick={() => { onUpdate({ [repeatF!.id]: addExdate(rec.data[repeatF!.id], occ) }); onClose(); }}>
                  Skip this date
                </button>
              )}
            </div>
            {(editOcc || ov) && (
              <div className="occ-fields">
                {occFields.map((f) => (
                  <div key={f.id} className="form-row">
                    <span className="form-label"><FieldIcon type={f.type} /> {f.name}</span>
                    <div className="form-input">
                      {can.edit ? <FieldInput field={f} value={occData[f.id]} onCommit={(v) => setOcc({ [overrideKey(f)]: v })} />
                        : <div className="computed-value"><CellValue field={f} value={occData[f.id]} /></div>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="muted small">{editOcc || ov ? "The fields above change only this date. " : ""}Changes below apply to the whole series.</p>
          </div>
        )}
        {arranging && onSections ? (
          <SectionsEditor fields={shown} sections={sections ?? []} onChange={onSections} onDone={() => setArranging(false)} />
        ) : (<>
          {top.map(row)}
          {secs.filter((sec) => sec.list.length).map((sec) => {
            const open = !closed.has(sec.name);
            return (
              <section key={sec.name} className="record-section">
                <button className="record-section-head group-toggle" aria-expanded={open}
                  onClick={() => setClosed((c) => { const n = new Set(c); if (open) n.add(sec.name); else n.delete(sec.name); return n; })}>
                  <span className={`chev ${open ? "open" : ""}`}><Icon name="right" size={12} /></span> {sec.name}
                  <span className="group-count">{sec.list.length}</span>
                </button>
                {open && sec.list.map(row)}
              </section>
            );
          })}
        </>)}
        {!occ && repeatF && rec.data[repeatF.id] && (
          <Upcoming value={rec.data[repeatF.id]} anchor={anchorOf(fields, rec)} canEdit={can.edit}
            onDone={onCompleteOccurrence} onSkip={(d) => onUpdate({ [repeatF.id]: addExdate(rec.data[repeatF.id], d) })} />
        )}
        {history && <RecordHistory key={rec.id} recordId={rec.id} fields={fields} />}
      </div>
    </Modal>
  );
}

/** The places a new record can go (empty or one choice = no need to ask). */
export function createTargets(ds: Dataset): CreateTarget[] {
  if (ds.kind === "table") {
    return (ds.linkSources ?? []).map((l) => ({ label: l.name, color: l.color, linkTableId: l.table_id }));
  }
  return (ds.sources ?? []).flatMap((s) => (s.link_sources?.length ?? 0) > 1
    ? s.link_sources!.map((l) => ({ label: `${s.name} › ${l.name}`, color: s.color, tableId: s.table_id, linkTableId: l.table_id }))
    : [{ label: s.name, color: s.color, tableId: s.table_id }]);
}

export function SourcePicker({ choices, onPick, onClose }: { choices: CreateTarget[]; onPick: (t: CreateTarget) => void; onClose: () => void }) {
  return (
    <Modal title="Add the record to…" width={400} onClose={onClose}>
      <div className="menu-list">
        {choices.map((c) => (
          <button key={`${c.tableId}:${c.linkTableId}`} className="menu-item big" onClick={() => onPick(c)}>
            <span className="swatch" style={{ background: c.color ?? "var(--accent)" }} /> {c.label}
            {c.linkTableId && <span className="muted small" style={{ marginLeft: "auto" }}><Icon name="link" size={12} /> linked</span>}
          </button>
        ))}
      </div>
    </Modal>
  );
}

/**
 * A new record filled in before it's saved: for public links that may add records but not edit them, where
 * creating an empty record first and filling it in afterwards isn't allowed.
 */
export function NewRecordForm({ fields, initial, onSave, onClose }: {
  fields: Field[]; initial: Record<string, any>; onSave: (data: Record<string, any>) => Promise<void> | void; onClose: () => void;
}) {
  const [data, setData] = useState<Record<string, any>>(initial);
  const [busy, setBusy] = useState(false);
  const shown = fields.filter((f) => f.id !== "__source" && isEditable(f) && f.type !== "attachment" && f.type !== "button");
  const save = async () => {
    setBusy(true);
    try { await onSave(data); } finally { setBusy(false); }
  };
  return (
    <Modal width={640} onClose={onClose} title="New record"
      footer={<>
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy} onClick={save}>{busy ? "Adding…" : "Add record"}</button>
      </>}>
      <div className="form">
        {shown.map((f) => (
          <div key={f.id} className="form-row">
            <span className="form-label" title={f.description ?? undefined}><FieldIcon type={f.type} /> {f.name}{f.required && <span className="req-mark" title="Required"> *</span>}</span>
            <div className="form-input">
              <FieldInput field={f} value={data[f.id]} onCommit={(v) => setData((d) => ({ ...d, [f.id]: v }))} />
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

/** Group a view's record fields under headings: name sections, then choose each field's. */
function SectionsEditor({ fields, sections, onChange, onDone }: {
  fields: Field[]; sections: RecordSection[]; onChange: (s: RecordSection[]) => void; onDone: () => void;
}) {
  const [names, setNames] = useState(sections.map((sec) => sec.name));
  const where = (id: string) => sections.findIndex((sec) => sec.fields.includes(id));
  const rename = (i: number, name: string) => {
    setNames((n) => n.map((x, j) => (j === i ? name : x)));
    if (name.trim() && !sections.some((sec, j) => j !== i && sec.name === name.trim())) onChange(sections.map((sec, j) => (j === i ? { ...sec, name: name.trim() } : sec)));
  };
  const add = () => {
    let n = 1, name = "Section";
    while (sections.some((sec) => sec.name === name)) name = `Section ${++n}`;
    onChange([...sections, { name, fields: [] }]);
    setNames((x) => [...x, name]);
  };
  const remove = (i: number) => { onChange(sections.filter((_, j) => j !== i)); setNames((x) => x.filter((_, j) => j !== i)); };
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= sections.length) return;
    const a = [...sections]; [a[i], a[j]] = [a[j], a[i]];
    const b = [...names]; [b[i], b[j]] = [b[j], b[i]];
    onChange(a); setNames(b);
  };
  const place = (id: string, to: number) => onChange(sections.map((sec, j) => ({ ...sec,
    fields: j === to ? [...sec.fields.filter((x) => x !== id), id] : sec.fields.filter((x) => x !== id) })));
  return (
    <div className="sections-editor">
      <p className="muted small">Group this view's fields under headings. Fields not in a section stay at the top. Every record in this view uses this layout.</p>
      {sections.map((sec, i) => (
        <div key={i} className="row section-name-row">
          <input className="input sm" value={names[i] ?? sec.name} aria-label={`Section ${i + 1} name`} onChange={(e) => rename(i, e.target.value)} />
          <button className="icon-btn" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up"><Icon name="up" size={14} /></button>
          <button className="icon-btn" disabled={i === sections.length - 1} onClick={() => move(i, 1)} aria-label="Move down"><Icon name="down" size={14} /></button>
          <button className="icon-btn" onClick={() => remove(i)} aria-label={`Remove section ${sec.name}`} title="Remove (its fields go back to the top)"><Icon name="trash" size={14} /></button>
        </div>
      ))}
      <button className="btn sm" onClick={add}><Icon name="plus" size={14} /> Add a section</button>
      {sections.length > 0 && (
        <div className="section-fields">
          {fields.map((f) => (
            <label key={f.id} className="section-field-row">
              <span><FieldIcon type={f.type} /> {f.name}</span>
              <select className="input sm" value={where(f.id)} aria-label={`Section of ${f.name}`} onChange={(e) => place(f.id, Number(e.target.value))}>
                <option value={-1}>Top</option>
                {sections.map((sec, i) => <option key={i} value={i}>{sec.name}</option>)}
              </select>
            </label>
          ))}
        </div>
      )}
      <div className="row"><span className="spacer" /><button className="btn sm primary" onClick={onDone}>Done</button></div>
    </div>
  );
}

/** A repeating record's next dates: mark one done ahead of time, or skip it. */
function Upcoming({ value, anchor, canEdit, onDone, onSkip }: {
  value: unknown; anchor?: string; canEdit: boolean; onDone?: (d: string) => void; onSkip: (d: string) => void;
}) {
  if (!anchor) return <p className="muted small upcoming-note"><Icon name="repeat" size={13} /> {describe(value)}: give it a date to see its next ones.</p>;
  const from = anchor.slice(0, 10);
  const to = new Date(Date.parse(from) + 3 * 366 * 86400000).toISOString().slice(0, 10);
  const dates = occurrences(value, anchor, from, to, 5);
  return (
    <section className="upcoming">
      <div className="upcoming-head"><Icon name="repeat" size={14} /> {describe(value)} <span className="muted small">· next dates</span></div>
      {!dates.length && <p className="muted small">No more dates: the repeat has ended.</p>}
      {dates.map((d, i) => (
        <div key={d} className="upcoming-row">
          <span>{formatDate(d, d.length > 10)}{i === 0 && <span className="muted small"> · current</span>}</span>
          <span className="spacer" />
          {canEdit && onDone && <button className="btn sm" onClick={() => onDone(d)} title={i === 0 ? "Done: it moves on to the next date" : "Done ahead of time: kept as done, and skipped in the repeat"}>
            <Icon name="check" size={13} /> Done</button>}
          {canEdit && i > 0 && <button className="btn sm ghost" onClick={() => onSkip(d)}>Skip</button>}
        </div>
      ))}
    </section>
  );
}

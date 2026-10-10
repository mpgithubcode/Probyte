import { useState } from "react";
import type { CreateTarget, Dataset, Field, Rec } from "../types";
import { addExdate, anchorOf, findOverride, occurrenceData, overrideKey, seriesDateFields, setOverride } from "../recur";
import { formatDate, recordTitle } from "../util";
import { CellValue, Chip, FieldInput } from "./Cells";
import { CAN_ALL, type Can } from "./viewTypes";
import { FieldIcon, Icon } from "./Icon";
import { Modal } from "./Overlay";

export function RecordModal({ ds, rec, fields, onUpdate, onDelete, onClose, onButton, can = CAN_ALL, occurrence }: {
  ds: Dataset; rec: Rec; fields: Field[];
  onUpdate: (patch: Record<string, any>) => void; onDelete: () => void; onClose: () => void; onButton: (f: Field) => void;
  can?: Can;
  occurrence?: string | null;                              // opened from a later repeat on the calendar/timeline
}) {
  const [confirm, setConfirm] = useState(false);
  const [editOcc, setEditOcc] = useState(false);
  const source = ds.sources?.find((s) => s.table_id === rec.table_id);
  const caldav = ds.kind === "table" ? ds.table?.kind !== "generic" : source?.kind !== "generic";
  const repeatF = fields.find((f) => f.type === "recurrence");
  const shown = fields.filter((f) => f.id !== "__source");
  // one occurrence of a repeating record
  const occ = occurrence && repeatF ? occurrence : null;
  const ov = occ ? findOverride(rec.data[repeatF!.id], occ) : undefined;
  const occData = occ ? occurrenceData(fields, rec, occ) : null;
  const primary = fields.find((f) => f.is_primary);
  const occFields = [...(primary ? [primary] : []), ...seriesDateFields(fields)].filter((f) => f.type !== "recurrence");
  const occStart = occData && seriesDateFields(fields).map((f) => occData[f.id]).find((v) => typeof v === "string");
  const setOcc = (patch: Record<string, any> | null) => onUpdate({ [repeatF!.id]: setOverride(rec.data[repeatF!.id], occ!, patch) });

  return (
    <Modal width={640} onClose={onClose}
      title={<span className="record-title">{source && <Chip name={source.name} color={source.color} />}{recordTitle(fields, rec)}</span>}
      footer={<>
        {!can.delete ? null : confirm ? (
          <>
            <span className="muted">Delete this record{caldav ? " (also from the CalDAV server)" : ""}?</span>
            <button className="btn danger" onClick={onDelete}>Delete</button>
            <button className="btn" onClick={() => setConfirm(false)}>Cancel</button>
          </>
        ) : (
          <button className="btn danger-ghost" onClick={() => setConfirm(true)}><Icon name="trash" size={14} /> Delete</button>
        )}
        <span className="spacer" />
        {caldav && <span className="muted">{rec.sync_status === "dirty" ? "● Pending sync" : "✓ Synced"}</span>}
      </>}>
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
        {shown.map((f) => (
          <div key={f.id} className="form-row">
            <span className="form-label"><FieldIcon type={f.type} /> {f.name}</span>
            <div className="form-input">
              {f.type === "button" ? (
                <button className="cell-button" style={{ background: f.options.color ?? "var(--accent)" }}
                  disabled={!can.edit && f.options.action !== "url"} onClick={() => onButton(f)}>
                  {f.options.label ?? "Click"}
                </button>
              ) : f.mapping && rec.table_id && !f.mapping[rec.table_id] ? (
                <span className="muted">Not mapped for this source</span>
              ) : (
                can.edit ? <FieldInput field={f} value={rec.data[f.id]} onCommit={(v) => onUpdate({ [f.id]: v })}
                    anchor={f.type === "recurrence" ? anchorOf(fields, rec) : undefined} />
                  : <div className="computed-value"><CellValue field={f} value={rec.data[f.id]} /></div>
              )}
            </div>
          </div>
        ))}
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

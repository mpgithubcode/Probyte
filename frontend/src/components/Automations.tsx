import { useEffect, useState } from "react";
import { api } from "../api";
import type { Base, Field, FilterGroup, TableInfo } from "../types";
import { timeAgo } from "../util";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";
import { FilterEditor } from "./ViewControls";

/** A base's automations (backend/app/automations): when something happens in a table, and the record matches the
 * conditions, do the actions. Owners and admins make and change them; everyone in the base sees them. */

type TriggerType = "created" | "updated" | "matches" | "deleted" | "schedule";
interface Trigger { type: TriggerType; fields?: string[]; every?: "hour" | "day" | "week" | "month"; at?: string; weekday?: number; day?: number; once?: boolean }
interface Action { type: "update" | "create" | "webhook" | "email" | "trash"; fields?: Record<string, string>; table_id?: string; url?: string; to?: string; subject?: string; body?: string }
interface Automation {
  id?: string; name: string; enabled: boolean; table_id: string; trigger: Trigger; conditions: FilterGroup; actions: Action[];
  next_run_at?: string | null; last_run_at?: string | null; last_ok?: boolean | null; last_message?: string | null; runs?: number; created_by?: string | null;
}
interface Run { at: string; record_id: string | null; ok: boolean; message: string; ms: number }

const TRIGGERS: { id: TriggerType; label: string }[] = [
  { id: "created", label: "A record is created" },
  { id: "updated", label: "A record is changed" },
  { id: "matches", label: "A record starts matching the conditions" },
  { id: "deleted", label: "A record is deleted" },
  { id: "schedule", label: "On a schedule" },
];
const ACTION_LABELS: Record<Action["type"], string> = {
  update: "Change this record", create: "Create a record", webhook: "Send it to a webhook", email: "Send an email", trash: "Move it to the trash",
};
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const EMPTY: FilterGroup = { conjunction: "and", items: [] } as FilterGroup;

function useFields() {
  const [cache, setCache] = useState<Record<string, Field[]>>({});
  const get = (id?: string) => {
    if (!id) return [];
    if (!cache[id]) {
      setCache((c) => ({ ...c, [id]: c[id] ?? [] }));
      api.get<{ fields: Field[] }>(`/tables/${id}`).then((r) => setCache((c) => ({ ...c, [id]: r.fields }))).catch(() => undefined);
    }
    return cache[id] ?? [];
  };
  return get;
}

export function Automations({ base, tables, onClose }: { base: Base; tables: TableInfo[]; onClose: () => void }) {
  const [list, setList] = useState<Automation[] | null>(null);
  const [email, setEmail] = useState(false);
  const [edit, setEdit] = useState<Automation | null>(null);
  const [err, setErr] = useState("");
  const canEdit = !base.role || base.role === "owner" || base.role === "admin";
  const own = tables.filter((t) => t.base_id === base.id && t.kind !== "remote");
  const load = () => api.get<{ automations: Automation[]; email: boolean }>(`/bases/${base.id}/automations`)
    .then((r) => { setList(r.automations); setEmail(r.email); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [base.id]);
  const blank = (): Automation => ({ name: "", enabled: true, table_id: own[0]?.id ?? "", trigger: { type: "created" }, conditions: EMPTY,
    actions: [{ type: "update", fields: {} }] });

  return (
    <Modal title={`Automations · ${base.name}`} width={900} onClose={onClose}>
      <div className="automations">
        <div className="auto-list">
          {(list ?? []).map((a) => (
            <button key={a.id} className={`auto-item ${edit?.id === a.id ? "on" : ""}`} onClick={() => { setErr(""); setEdit(a); }}>
              <span className={`auto-dot ${!a.enabled ? "off" : a.last_ok === false ? "err" : a.last_ok ? "ok" : ""}`} />
              <span className="auto-name">{a.name}</span>
              <span className="muted small">{own.find((t) => t.id === a.table_id)?.name}</span>
            </button>
          ))}
          {list && !list.length && <p className="muted small">No automations yet.</p>}
          {canEdit && <button className="btn sm" onClick={() => { setErr(""); setEdit(blank()); }} disabled={!own.length}>
            <Icon name="plus" size={14} /> New automation</button>}
        </div>
        <div className="auto-edit">
          {err && <div className="error">{err}</div>}
          {edit ? <Editor key={edit.id ?? "new"} value={edit} tables={own} email={email} canEdit={canEdit}
            onSaved={(a) => { setEdit(a); load(); }} onDeleted={() => { setEdit(null); load(); }} onError={setErr} />
            : <p className="muted">When something happens in one of this base's tables, and the record matches the conditions you set, an
              automation does what you tell it: change the record, create one in another table, send it to a webhook (n8n, Zapier…),
              send an email, or move it to the trash. Changes an automation makes don't set off automations.</p>}
        </div>
      </div>
    </Modal>
  );
}

function Editor({ value, tables, email, canEdit, onSaved, onDeleted, onError }: {
  value: Automation; tables: TableInfo[]; email: boolean; canEdit: boolean;
  onSaved: (a: Automation) => void; onDeleted: () => void; onError: (m: string) => void;
}) {
  const [a, setA] = useState<Automation>(value);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const fieldsOf = useFields();
  const fields = fieldsOf(a.table_id);
  const set = (p: Partial<Automation>) => setA((x) => ({ ...x, ...p }));
  const setTrig = (p: Partial<Trigger>) => setA((x) => ({ ...x, trigger: { ...x.trigger, ...p } }));
  const setAct = (i: number, p: Partial<Action>) => setA((x) => ({ ...x, actions: x.actions.map((y, j) => (j === i ? { ...y, ...p } : y)) }));
  const loadRuns = () => a.id && api.get<{ runs: Run[] }>(`/automations/${a.id}/runs`).then((r) => setRuns(r.runs)).catch(() => undefined);
  useEffect(() => { loadRuns(); }, [a.id]);

  const save = async () => {
    setBusy(true); onError(""); setNote("");
    try {
      const out = a.id ? await api.put<Automation>(`/automations/${a.id}`, a) : await api.post<Automation>(`/bases/${(tables[0] as TableInfo).base_id}/automations`, a);
      setA(out); onSaved(out); setNote("Saved");
    } catch (e) { onError((e as Error).message); }
    setBusy(false);
  };
  const tryIt = async () => {
    if (!a.id) return;
    setBusy(true); setNote("");
    try {
      const r = await api.post<{ ok: boolean; message: string }>(`/automations/${a.id}/test`, {});
      setNote(`${r.ok ? "Ran" : "Failed"}: ${r.message}`); loadRuns();
    } catch (e) { onError((e as Error).message); }
    setBusy(false);
  };
  const t = a.trigger;
  const writable = fields.filter((f) => !f.computed && !f.readonly && f.type !== "attachment" && f.type !== "button");

  return (
    <fieldset className="form auto-form" disabled={!canEdit}>
      <div className="row">
        <input className="input" placeholder="Name it (e.g. “Invoice when done”)" value={a.name} onChange={(e) => set({ name: e.target.value })} />
        <label className="inline-check"><input type="checkbox" checked={a.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> On</label>
      </div>

      <h4>When</h4>
      <div className="row">
        <select className="input" value={t.type} onChange={(e) => setTrig({ type: e.target.value as TriggerType })}>
          {TRIGGERS.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
        </select>
        <span className="muted">in</span>
        <select className="input" value={a.table_id} onChange={(e) => set({ table_id: e.target.value, conditions: EMPTY, trigger: { type: t.type } })}>
          {tables.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
      </div>
      {t.type === "updated" && (
        <div className="auto-fields">
          <span className="muted small">Only when one of these changes (none ticked: any change):</span>
          {fields.filter((f) => !f.computed).map((f) => (
            <label key={f.id} className="inline-check small"><input type="checkbox" checked={!!t.fields?.includes(f.id)}
              onChange={(e) => setTrig({ fields: e.target.checked ? [...(t.fields ?? []), f.id] : (t.fields ?? []).filter((x) => x !== f.id) })} /> {f.name}</label>
          ))}
        </div>
      )}
      {t.type === "schedule" && (
        <div className="row">
          <span className="muted">Every</span>
          <select className="input sm" value={t.every ?? "day"} onChange={(e) => setTrig({ every: e.target.value as Trigger["every"] })}>
            <option value="hour">hour</option><option value="day">day</option><option value="week">week</option><option value="month">month</option>
          </select>
          {t.every === "week" && <select className="input sm" value={t.weekday ?? 0} onChange={(e) => setTrig({ weekday: +e.target.value })}>
            {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select>}
          {t.every === "month" && <input className="input sm" type="number" min={1} max={28} value={t.day ?? 1} onChange={(e) => setTrig({ day: +e.target.value })} />}
          <span className="muted">{t.every === "hour" ? "at minute" : "at"}</span>
          <input className="input sm" value={t.at ?? "08:00"} onChange={(e) => setTrig({ at: e.target.value })} placeholder="08:00" style={{ width: 80 }} />
          <label className="inline-check small"><input type="checkbox" checked={!!t.once} onChange={(e) => setTrig({ once: e.target.checked })} />
            once, not for each record</label>
        </div>
      )}

      {!(t.type === "schedule" && t.once) && (<>
        <h4>{t.type === "schedule" ? "For each record that matches" : t.type === "matches" ? "The conditions" : "Only if"}</h4>
        <FilterEditor fields={fields} filter={a.conditions ?? EMPTY} onChange={(g) => set({ conditions: g })} />
      </>)}

      <h4>Then</h4>
      {a.actions.map((act, i) => (
        <div key={i} className="auto-action">
          <div className="row">
            <select className="input" value={act.type} onChange={(e) => setAct(i, { type: e.target.value as Action["type"], fields: {} })}>
              {(Object.keys(ACTION_LABELS) as Action["type"][]).filter((k) => k !== "email" || email || act.type === "email")
                .filter((k) => t.type !== "deleted" || (k !== "update" && k !== "trash")).map((k) => <option key={k} value={k}>{ACTION_LABELS[k]}</option>)}
            </select>
            {act.type === "create" && <><span className="muted">in</span>
              <select className="input" value={act.table_id ?? ""} onChange={(e) => setAct(i, { table_id: e.target.value, fields: {} })}>
                <option value="" disabled>Choose a table</option>
                {tables.filter((x) => x.kind !== "files").map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select></>}
            <span className="spacer" />
            <button className="icon-btn" title="Remove" onClick={() => setA((x) => ({ ...x, actions: x.actions.filter((_, j) => j !== i) }))}><Icon name="x" size={14} /></button>
          </div>
          {(act.type === "update" || act.type === "create") && (
            <ValueRows fields={act.type === "update" ? writable : fieldsOf(act.table_id).filter((f) => !f.computed && f.type !== "attachment" && f.type !== "button")}
              values={act.fields ?? {}} onChange={(v) => setAct(i, { fields: v })} />
          )}
          {act.type === "webhook" && <input className="input" placeholder="https://… (gets the record as JSON)" value={act.url ?? ""} onChange={(e) => setAct(i, { url: e.target.value })} />}
          {act.type === "email" && (<>
            <input className="input" placeholder="To (an address, or {Email})" value={act.to ?? ""} onChange={(e) => setAct(i, { to: e.target.value })} />
            <input className="input" placeholder="Subject" value={act.subject ?? ""} onChange={(e) => setAct(i, { subject: e.target.value })} />
            <textarea className="input" rows={3} placeholder="Message" value={act.body ?? ""} onChange={(e) => setAct(i, { body: e.target.value })} />
          </>)}
        </div>
      ))}
      <button className="btn sm" onClick={() => setA((x) => ({ ...x, actions: [...x.actions, { type: "update", fields: {} }] }))}><Icon name="plus" size={14} /> Add an action</button>
      <p className="hint">Values are written as you'd type them and can use the record's fields: <code>Invoice for {"{Client}"}</code>,
        <code>{"{today}"}</code>, <code>{"{now}"}</code>. It acts with the rights of whoever saved it last.</p>

      <div className="row">
        <button className="btn primary" disabled={busy} onClick={save}>{a.id ? "Save" : "Create"}</button>
        {a.id && <button className="btn" disabled={busy} onClick={tryIt} title={t.type === "schedule" ? "Run it now, as the schedule would" : "Run its actions now on the table's newest record"}>Try it now</button>}
        {a.id && <button className="btn danger-ghost" disabled={busy} onClick={async () => { if (!window.confirm(`Delete “${a.name}”?`)) return; await api.del(`/automations/${a.id}`); onDeleted(); }}>Delete</button>}
        {note && <span className="muted small">{note}</span>}
      </div>
      {a.id && (
        <div className="auto-runs">
          <h4>Recent runs {a.next_run_at && <span className="muted small">· next {new Date(a.next_run_at).toLocaleString()}</span>}</h4>
          {!runs.length ? <p className="muted small">It hasn't run yet.</p> : (
            <table className="devices-table"><tbody>
              {runs.slice(0, 12).map((r, i) => (
                <tr key={i}><td>{timeAgo(r.at)}</td><td className={r.ok ? "" : "error"}>{r.ok ? "✓" : "✗"} {r.message}</td><td className="muted small">{r.ms} ms</td></tr>
              ))}
            </tbody></table>
          )}
        </div>
      )}
    </fieldset>
  );
}

function ValueRows({ fields, values, onChange }: { fields: Field[]; values: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const keys = Object.keys(values);
  const name = (k: string) => fields.find((f) => f.id === k || f.name === k)?.name ?? k;
  const free = fields.filter((f) => !keys.includes(f.name) && !keys.includes(f.id));
  return (
    <div className="auto-values">
      {keys.map((k) => (
        <div key={k} className="row">
          <span className="auto-field-name">{name(k)}</span>
          <input className="input" value={values[k]} placeholder="value, or {Field}" onChange={(e) => onChange({ ...values, [k]: e.target.value })} />
          <button className="icon-btn" title="Remove" onClick={() => { const v = { ...values }; delete v[k]; onChange(v); }}><Icon name="x" size={13} /></button>
        </div>
      ))}
      {free.length > 0 && (
        <select className="input sm" value="" onChange={(e) => e.target.value && onChange({ ...values, [e.target.value]: "" })}>
          <option value="">+ Set a field…</option>
          {free.map((f) => <option key={f.id} value={f.name}>{f.name}</option>)}
        </select>
      )}
    </div>
  );
}

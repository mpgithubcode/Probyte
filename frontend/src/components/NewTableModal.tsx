import { useEffect, useState } from "react";
import { api } from "../api";
import type { Account, Base, CalendarInfo, TableInfo, TableKind } from "../types";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

const KINDS: { kind: TableKind; title: string; desc: string; icon: string }[] = [
  { kind: "generic", title: "Blank database", desc: "Your own fields, stored in Probyte", icon: "table" },
  // One calendar type: tasks and events together (existing tasks-only / events-only tables keep working)
  { kind: "mixed", title: "Tasks & events", desc: "Synced with a calendar: your phone's Reminders and Calendar apps", icon: "calendar-check" },
  { kind: "contacts", title: "Contacts", desc: "vCards synced with a CardDAV address book", icon: "contacts" },
  { kind: "remote", title: "From a link", desc: "Show a table shared with a Probyte public link — from any server", icon: "link" },
];

interface LinkMeta {
  name: string; needs_password: boolean; local: boolean; origin: string; view_hint: string | null;
  permissions: { add?: boolean; edit?: boolean; delete?: boolean };
  items: { id: string; name: string; kind: string; views: { id: string; name: string; type: string }[] }[];
}

const NEEDS: Record<string, string[]> = { tasks: ["VTODO"], events: ["VEVENT"], mixed: ["VTODO", "VEVENT"], contacts: ["VCARD"] };
// "Tasks & events" takes any calendar with tasks or events; one holding only one of them makes a tasks or events table
const supports = (c: CalendarInfo, kind: TableKind) => kind === "mixed"
  ? ["VEVENT", "VTODO"].some((x) => c.components.includes(x))
  : (NEEDS[kind] ?? []).every((x) => c.components.includes(x));
const kindFor = (c: CalendarInfo | undefined, kind: TableKind): TableKind => {
  if (kind !== "mixed" || !c) return kind;
  const ev = c.components.includes("VEVENT"), td = c.components.includes("VTODO");
  return ev && td ? "mixed" : ev ? "events" : "tasks";
};
const KIND_WORD: Record<string, string> = { tasks: "tasks", events: "events", mixed: "tasks + events", contacts: "contacts" };

export function NewTableModal({ accounts, bases, baseId, onClose, onCreated, onManageAccounts }: {
  accounts: Account[]; bases: Base[]; baseId?: string; onClose: () => void; onCreated: (t: TableInfo) => void; onManageAccounts: () => void;
}) {
  const [base, setBase] = useState(baseId ?? bases[0]?.id ?? "");
  const [kind, setKind] = useState<TableKind>("generic");
  const [name, setName] = useState("");
  // The built-in account (sorted first) is the one that syncs to the user's phone.
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [calendars, setCalendars] = useState<CalendarInfo[] | null>(null);
  const [calendarUrl, setCalendarUrl] = useState("");
  const [newCal, setNewCal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // linked table ("From a link")
  const [url, setUrl] = useState("");
  const [linkPw, setLinkPw] = useState("");
  const [meta, setMeta] = useState<LinkMeta | null>(null);
  const [pick, setPick] = useState<{ item: string; view: string } | null>(null);
  const book = kind === "contacts";

  const checkLink = async () => {
    setBusy(true);
    setError("");
    setMeta(null);
    try {
      const m = await api.post<LinkMeta>("/remote/inspect", { url, password: linkPw || undefined });
      setMeta(m);
      if (!m.needs_password && m.items.length) {
        const item = m.items.find((i) => i.views.some((v) => v.id === m.view_hint)) ?? m.items[0];
        const view = item.views.find((v) => v.id === m.view_hint) ?? item.views[0];
        setPick({ item: item.id, view: view.id });
        if (!name.trim()) setName(item.name);
      }
    } catch (e) { setError((e as Error).message === "password_required" ? "Wrong password" : (e as Error).message); }
    setBusy(false);
  };

  useEffect(() => {
    if (kind === "generic" || kind === "remote" || !accountId) return;
    setCalendars(null);
    setCalendarUrl("");  // never keep another kind's calendar (a contacts table on a calendar) while loading
    setError("");
    api.get<CalendarInfo[]>(`/accounts/${accountId}/${book ? "addressbooks" : "calendars"}`)
      .then((cals) => {
        setCalendars(cals);
        const usable = cals.filter((c) => supports(c, kind));
        setCalendarUrl(usable[0]?.url ?? "__new");
      })
      .catch((e) => { setCalendars([]); setCalendarUrl("__new"); setError(e.message); });
  }, [kind, accountId]);

  const create = async () => {
    if (kind === "remote" && !pick) return checkLink();
    if (!name.trim()) return setError("Give the table a name");
    setBusy(true);
    setError("");
    try {
      const body: Record<string, unknown> = { name: name.trim(), kind: calendarUrl === "__new" ? kind : kindFor(calendars?.find((c) => c.url === calendarUrl), kind), base_id: base };
      if (kind === "remote") {
        body.remote = { url, password: linkPw || undefined, item_id: pick!.item, view_id: pick!.view };
      } else if (kind !== "generic") {
        body.account_id = accountId;
        if (calendarUrl === "__new") body.new_calendar_name = newCal.trim() || name.trim();
        else body.calendar_url = calendarUrl;
      }
      onCreated(await api.post<TableInfo>("/tables", body));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const needsAccount = kind !== "generic" && kind !== "remote" && !accounts.length;
  const loadingCalendars = kind !== "generic" && kind !== "remote" && !needsAccount && (calendars === null || !calendarUrl);
  const coll = book ? "address book" : "calendar";

  return (
    <Modal title="New table" onClose={onClose}
      footer={<>
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || needsAccount || loadingCalendars || (kind === "remote" && !url.trim())} onClick={create}>
          {busy ? "Working…" : kind === "remote" && !pick ? "Check link" : "Create"}
        </button>
      </>}>
      <div className="form">
        <div className="kind-picker">
          {KINDS.map((k) => (
            <button key={k.kind} className={`kind ${kind === k.kind ? "active" : ""}`} onClick={() => setKind(k.kind)}>
              <Icon name={k.icon} size={20} />
              <b>{k.title}</b>
              <span className="muted">{k.desc}</span>
            </button>
          ))}
        </div>
        {bases.length > 1 && (
          <label className="stack">Base
            <select className="input" value={base} onChange={(e) => setBase(e.target.value)}>
              {bases.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
        )}
        <label className="stack">Name
          <input className="input" autoFocus value={name}
            placeholder={kind === "tasks" ? "My Tasks" : kind === "events" ? "My Events" : kind === "mixed" ? "Personal" : book ? "Contacts" : kind === "remote" ? "Name in your base" : "Projects"}
            onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
        </label>

        {needsAccount && (
          <div className="notice">
            Add an account first.
            <button className="btn sm" onClick={onManageAccounts}>Add account</button>
          </div>
        )}

        {kind === "remote" && (
          <>
            <label className="stack">Public link
              <input className="input" value={url} placeholder="https://their-probyte.example.com/s/AbC123"
                onChange={(e) => { setUrl(e.target.value); setMeta(null); setPick(null); }}
                onKeyDown={(e) => e.key === "Enter" && checkLink()} />
              <span className="hint">Any Probyte public link works — from this server or another one. You'll see exactly what the link shares, and can do only what its owner allowed.</span>
            </label>
            {meta?.needs_password && (
              <label className="stack">This link is password protected
                <input className="input" type="password" autoFocus value={linkPw} onChange={(e) => setLinkPw(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && checkLink()} />
              </label>
            )}
            {meta && !meta.needs_password && (
              <div className="link-preview">
                <div className="row">
                  <Icon name="link" size={16} /> <b>{meta.name}</b>
                  <span className="muted small">{meta.local ? "on this server" : new URL(meta.origin).host}</span>
                  <span className="spacer" />
                  <span className="badge">{meta.permissions.add || meta.permissions.edit || meta.permissions.delete
                    ? `can ${[meta.permissions.add && "add", meta.permissions.edit && "edit", meta.permissions.delete && "delete"].filter(Boolean).join(", ")}`
                    : "view only"}</span>
                </div>
                <span className="muted small">Choose the table and view to show:</span>
                {meta.items.map((it) => (
                  <div key={it.id} className="share-item on">
                    <b>{it.name}</b>
                    <div className="share-views">
                      {it.views.map((v) => (
                        <label key={v.id} className="chip-check">
                          <input type="radio" name="link-view" checked={pick?.view === v.id}
                            onChange={() => { setPick({ item: it.id, view: v.id }); if (!name.trim() || meta.items.some((x) => x.name === name)) setName(it.name); }} />
                          {v.name}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {kind !== "generic" && kind !== "remote" && !needsAccount && (
          <>
            <label className="stack">{book ? "Account" : "CalDAV account"}
              <select className="input" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </label>
            <label className="stack">{book ? "Address book" : "Calendar"}
              <select className="input" value={calendarUrl} disabled={!calendars} onChange={(e) => setCalendarUrl(e.target.value)}>
                {!calendars && <option>Loading {coll}s…</option>}
                {calendars?.map((c) => (
                  <option key={c.url} value={c.url} disabled={!supports(c, kind)}>
                    {c.name}{!supports(c, kind) ? ` (no ${KIND_WORD[kind]})` : ""}
                  </option>
                ))}
                {calendars && <option value="__new">+ Create a new {coll}…</option>}
              </select>
            </label>
            {calendarUrl === "__new" && (
              <label className="stack">New {coll} name
                <input className="input" value={newCal} placeholder={name || "Probyte"} onChange={(e) => setNewCal(e.target.value)} />
              </label>
            )}
            <p className="hint">{book
              ? "Existing contacts are imported, and changes sync both ways. Photos, extra numbers and other details Probyte doesn't show are kept."
              : "Existing items in the calendar are imported, and changes sync both ways."}
              {(() => {
                const k = calendarUrl !== "__new" ? kindFor(calendars?.find((c) => c.url === calendarUrl), kind) : kind;
                return k !== kind ? ` This calendar only holds ${k === "events" ? "events" : "tasks"}, so the table will too.` : null;
              })()}</p>
          </>
        )}
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}

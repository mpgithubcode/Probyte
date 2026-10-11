import { createContext, useContext, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Attachment, Field, Refs, User } from "../types";
import {
  choiceColor, displayText, formatDate, formatNumber, fromInputValue, innerField, isDateOnly, isEditable, isErrorValue,
  toInputValue, valueType,
} from "../util";
import { Icon } from "./Icon";
import { Popover } from "./Overlay";
import { RecurrenceEditor } from "./RecurrenceEditor";
import { describe as describeRepeat } from "../recur";
import { attachmentUrl, fileExt, thumbUrl, viewOf } from "../files";
import { FilePreview } from "./FileViewer";

/** Titles of linked records / usernames for the current dataset, plus who is logged in. */
export const RefsContext = createContext<{ refs: Refs; addRefs: (r: Partial<Refs>) => void; me?: User }>({
  refs: { records: {}, users: {} }, addRefs: () => undefined,
});

export function Chip({ name, color }: { name: string; color: string }) {
  return <span className="chip" style={{ background: `${color}26`, color, borderColor: `${color}55` }}>{name}</span>;
}

function RefChip({ label, kind }: { label: string; kind: "record" | "user" }) {
  return (
    <span className={`ref-chip ${kind}`}>
      {kind === "user" && <span className="avatar xs">{(label[0] ?? "?").toUpperCase()}</span>}
      {label || "Untitled"}
    </span>
  );
}

export { attachmentUrl, setAttachmentBase } from "../files";

/** Small previews; a click opens the file in the preview (images, PDFs, video, audio, text) or downloads it. */
export function AttachmentThumbs({ items, size = 24 }: { items: Attachment[]; size?: number }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <span className="thumbs">
      {items.map((a, i) => {
        const src = thumbUrl(a);
        return (
          <a key={a.id} href={attachmentUrl(a, !viewOf(a))} target="_blank" rel="noreferrer" title={a.name}
            onClick={(e) => { e.stopPropagation(); if (viewOf(a)) { e.preventDefault(); setOpen(i); } }}
            className="thumb" style={{ width: size, height: size }}>
            {src ? <img src={src} alt={a.name} loading="lazy" /> : <span className="file-ext">{fileExt(a.name)}</span>}
          </a>
        );
      })}
      {open !== null && <FilePreview items={items} index={open} onClose={() => setOpen(null)} />}
    </span>
  );
}

/** Read-only rendering of a value. */
export function CellValue({ field, value }: { field: Field; value: any }) {
  const { refs } = useContext(RefsContext);
  if (isErrorValue(value)) return <span className="cell-error" title={value.__error}>#ERROR</span>;
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return null;
  switch (field.type) {
    case "checkbox":
      return <span className={`check ${value ? "on" : ""}`}>{value ? "✓" : ""}</span>;
    case "single_select":
      return <Chip name={String(value)} color={choiceColor(field, value)} />;
    case "multi_select":
      return <span className="chips">{(Array.isArray(value) ? value : [value]).map((v: string) => (
        <Chip key={v} name={v} color={choiceColor(field, v)} />))}</span>;
    case "date":
      return <span>{formatDate(value, field.options.includeTime, field.options)}</span>;
    case "created_time": case "last_modified_time":
      return <span>{formatDate(value, true)}</span>;
    case "recurrence":
      return <span className="repeat-value"><Icon name="repeat" size={12} /> {describeRepeat(value)}</span>;
    case "rating":
      return <span className="stars">{"★".repeat(Number(value) || 0)}</span>;
    case "url":
      return <a href={String(value)} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{String(value)}</a>;
    case "email":
      return <a href={`mailto:${value}`} onClick={(e) => e.stopPropagation()}>{String(value)}</a>;
    case "link":
      return <span className="chips">{(value as string[]).map((id) => <RefChip key={id} kind="record" label={refs.records[id] ?? ""} />)}</span>;
    case "user": case "created_by": case "last_modified_by":
      return <span className="chips">{(Array.isArray(value) ? value : [value]).map((id: string) => (
        <RefChip key={id} kind="user" label={refs.users[id] ?? "unknown"} />))}</span>;
    case "attachment":
      return <AttachmentThumbs items={value as Attachment[]} />;
    case "autonumber":
      return <span className="num">{value}</span>;
    case "record_id":
      return <code className="rid" title="Click to copy" onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(String(value)); }}>{String(value)}</code>;
    case "lookup": case "conditional_lookup": {
      const inner = innerField(field);
      const items = Array.isArray(value) ? value : [value];
      if (inner && ["single_select", "multi_select", "link", "user", "attachment", "checkbox", "rating"].includes(inner.type)) {
        return <span className="chips">{items.map((x, i) => (
          <CellValue key={i} field={inner} value={inner.type === "link" || inner.type === "user" || inner.type === "attachment" ? [x] : x} />))}</span>;
      }
      return <span>{items.map((x) => (inner ? displayText(inner, x, refs) : String(x))).join(", ")}</span>;
    }
  }
  const vt = valueType(field);
  if (vt === "boolean") return <span className={`check ${value ? "on" : ""}`}>{value ? "✓" : ""}</span>;
  if (vt === "number" && typeof value === "number") return <span className="num">{formatNumber(value, field.options)}</span>;
  if (vt === "date" || (typeof value === "string" && /^\d{4}-\d{2}-\d{2}(T[\d:.]+Z)?$/.test(value) && field.type === "formula")) {
    return <span>{formatDate(value, !isDateOnly(value))}</span>;
  }
  if (Array.isArray(value)) return <span>{value.join(", ")}</span>;
  return <span>{String(value)}</span>;
}

interface InputProps {
  field: Field;
  value: any;
  onCommit: (v: any) => void;
  onDone?: () => void;       // editing finished (blur / enter / escape / popover closed)
  autoFocus?: boolean;
  inline?: boolean;          // grid cell editor (pickers open immediately)
  anchor?: string;           // repeat fields: the record's start, to suggest sensible days
  initial?: string;          // grid: editing started by typing this character (replaces the value)
  takeTyped?: () => string;  // grid: everything typed until the editor appeared (read once it has)
}

/** Editable input for a value. Commits on blur / Enter; Escape cancels. */
export function FieldInput(props: InputProps) {
  const { field, value, onCommit, onDone, autoFocus } = props;
  if (!isEditable(field)) return <div className="computed-value"><CellValue field={field} value={value} /></div>;
  switch (field.type) {
    case "checkbox":
      return <input type="checkbox" className="checkbox" checked={!!value} autoFocus={autoFocus}
        onChange={(e) => onCommit(e.target.checked)} onBlur={onDone} />;
    case "single_select":
    case "multi_select":
      return <ChoicePicker {...props} />;
    case "rating":
      return <RatingInput max={field.options.max ?? 5} value={Number(value) || 0} onChange={(v) => { onCommit(v || null); onDone?.(); }} />;
    case "date":
      return <DateInput {...props} />;
    case "recurrence":
      return <RecurrenceInput {...props} />;
    case "link":
      return <LinkInput {...props} />;
    case "user":
      return <UserInput {...props} />;
    case "attachment":
      return <AttachmentInput {...props} />;
    default:
      return <TextInput {...props} />;
  }
}

function TextInput({ field, value, onCommit, onDone, autoFocus, inline, initial, takeTyped }: InputProps) {
  const original = value ?? "";
  const [draft, setDraft] = useState<string>(initial ?? String(original));
  // started by typing: the caret goes after that first character
  const inputRef = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = inputRef.current;
    if (el && initial !== undefined) {
      const typed = takeTyped?.();  // keys pressed while the editor was appearing
      if (typed && typed !== el.value) { setDraft(typed); el.value = typed; }
      el.focus();
      try { el.setSelectionRange(el.value.length, el.value.length); } catch { /* number inputs have no caret position */ }
    }
  }, []);  // once, when editing starts
  const cancelled = useRef(false);
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(String(value ?? "")); }, [value]);

  const commit = () => {
    focused.current = false;
    if (cancelled.current) { cancelled.current = false; setDraft(String(original)); onDone?.(); return; }
    let v: any = draft;
    if (field.type === "number") v = draft.trim() === "" ? null : Number(draft);
    if (v === "") v = null;
    if (v !== (value ?? null) && !(field.type === "number" && Number.isNaN(v))) onCommit(v);
    onDone?.();
  };
  const common = {
    value: draft,
    autoFocus,
    className: inline ? "cell-input" : "input",
    onFocus: () => { focused.current = true; },
    onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
    onBlur: commit,
    ref: inputRef,
  };
  if (field.type === "long_text") {
    return <textarea {...common} rows={inline ? 4 : 3}
      onKeyDown={(e) => {
        if (e.key === "Escape") { cancelled.current = true; e.currentTarget.blur(); }
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.blur();
        // in the grid, Enter saves like a spreadsheet (Shift+Enter for a new line); in the record panel it's a new line
        if (e.key === "Enter" && inline && !e.shiftKey && !e.altKey) { e.preventDefault(); e.currentTarget.blur(); }
      }} />;
  }
  return <input {...common} type={field.type === "number" ? "number" : field.type === "email" ? "email" : field.type === "url" ? "url" : "text"}
    onKeyDown={(e) => {
      if (e.key === "Escape") { cancelled.current = true; e.currentTarget.blur(); }
      if (e.key === "Enter") e.currentTarget.blur();
    }} />;
}

function DateInput({ field, value, onCommit, onDone, autoFocus, inline }: InputProps) {
  const includeTime = !!field.options.includeTime;
  const [draft, setDraft] = useState(toInputValue(value, includeTime));
  useEffect(() => setDraft(toInputValue(value, includeTime)), [value, includeTime]);
  const commit = () => {
    const v = fromInputValue(draft, includeTime, value);
    if (v !== (value ?? null)) onCommit(v);
    onDone?.();
  };
  return (
    <input type={includeTime ? "datetime-local" : "date"} className={inline ? "cell-input" : "input"} value={draft}
      autoFocus={autoFocus} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") { setDraft(toInputValue(value, includeTime)); onDone?.(); }
      }} />
  );
}

function RecurrenceInput({ value, onCommit, onDone, inline, anchor }: InputProps) {
  const shell = usePickerShell(inline, onDone);
  return (
    <div ref={shell.setAnchorEl} className={inline ? "choice-inline" : "choice-field input"} onClick={() => !inline && shell.setOpen(true)}>
      {value ? <span className="repeat-value"><Icon name="repeat" size={12} /> {describeRepeat(value)}</span>
        : !inline && <span className="muted">Doesn't repeat</span>}
      {shell.open && shell.anchorEl && (
        <Popover anchor={shell.anchorEl} onClose={shell.close} width={300}>
          <RecurrenceEditor value={value} anchor={anchor} onChange={(v) => { if (v !== (value ?? null)) onCommit(v); }} onDone={shell.close} />
        </Popover>
      )}
    </div>
  );
}

function RatingInput({ max, value, onChange }: { max: number; value: number; onChange: (v: number) => void }) {
  const [hover, setHover] = useState(0);
  return (
    <span className="rating" onMouseLeave={() => setHover(0)}>
      {Array.from({ length: max }, (_, i) => i + 1).map((n) => (
        <button key={n} type="button" className={(hover || value) >= n ? "on" : ""}
          onMouseEnter={() => setHover(n)} onClick={() => onChange(n === value ? 0 : n)}>★</button>
      ))}
    </span>
  );
}

/** Shared shell: a field box (or the grid cell) that opens a popover editor. */
function usePickerShell(inline?: boolean, onDone?: () => void) {
  const [anchorEl, setAnchorEl] = useState<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(!!inline);
  const close = () => { setOpen(false); onDone?.(); };
  return { anchorEl, setAnchorEl, open, setOpen, close };
}

function ChoicePicker({ field, value, onCommit, onDone, inline }: InputProps) {
  const multi = field.type === "multi_select";
  const selected: string[] = multi ? (Array.isArray(value) ? value : []) : value ? [value] : [];
  const shell = usePickerShell(inline, onDone);
  const [q, setQ] = useState("");
  const choices = field.options.choices ?? [];
  const shown = choices.filter((c) => c.name.toLowerCase().includes(q.trim().toLowerCase()));
  const canCreate = !field.readonly && field.options.allowNew !== false && q.trim() && !choices.some((c) => c.name.toLowerCase() === q.trim().toLowerCase());

  const close = () => { setQ(""); shell.close(); };
  const pick = (name: string) => {
    if (multi) {
      onCommit(selected.includes(name) ? selected.filter((s) => s !== name) : [...selected, name]);
      setQ("");
    } else {
      onCommit(selected[0] === name ? null : name);
      close();
    }
  };

  return (
    <div ref={shell.setAnchorEl} className={inline ? "choice-inline" : "choice-field input"} onClick={() => !inline && shell.setOpen(true)}>
      <span className="chips">
        {selected.map((s) => <Chip key={s} name={s} color={choiceColor(field, s)} />)}
        {!selected.length && !inline && <span className="muted">Select…</span>}
      </span>
      {shell.open && shell.anchorEl && (
        <Popover anchor={shell.anchorEl} onClose={close} width={240}>
          <input className="input" autoFocus placeholder={field.readonly || field.options.allowNew === false ? "Search" : "Search or create…"} value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                if (shown[0] && (!canCreate || shown[0].name.toLowerCase() === q.trim().toLowerCase())) pick(shown[0].name);
                else if (canCreate) pick(q.trim());
              }
            }} />
          <div className="menu-list">
            {shown.map((c) => (
              <button key={c.name} className={`menu-item ${selected.includes(c.name) ? "active" : ""}`} onClick={() => pick(c.name)}>
                <Chip name={c.name} color={c.color} />
                {selected.includes(c.name) && <span className="tick">✓</span>}
              </button>
            ))}
            {canCreate && <button className="menu-item" onClick={() => pick(q.trim())}>+ Create <b>{q.trim()}</b></button>}
            {!shown.length && !canCreate && <div className="muted pad">No options</div>}
          </div>
        </Popover>
      )}
    </div>
  );
}

const titleCache = new Map<string, Promise<{ id: string; title: string }[]>>();
export const invalidateTitles = (tableId?: string) => (tableId ? titleCache.delete(tableId) : titleCache.clear());

function LinkInput({ field, value, onCommit, onDone, inline }: InputProps) {
  const { refs, addRefs } = useContext(RefsContext);
  const ids: string[] = Array.isArray(value) ? value : [];
  const single = field.options.relationship === "one";
  const shell = usePickerShell(inline, onDone);
  const [q, setQ] = useState("");
  const [options, setOptions] = useState<{ id: string; title: string }[] | null>(null);
  const tid = field.options.foreignTableId ?? "";

  useEffect(() => {
    if (!shell.open || !tid) return;
    if (!titleCache.has(tid)) titleCache.set(tid, api.get(`/tables/${tid}/titles`));
    titleCache.get(tid)!.then(setOptions).catch(() => { titleCache.delete(tid); setOptions([]); });
  }, [shell.open, tid]);

  const toggle = (o: { id: string; title: string }) => {
    addRefs({ records: { [o.id]: o.title } });
    if (single) { onCommit(ids[0] === o.id ? [] : [o.id]); shell.close(); return; }
    onCommit(ids.includes(o.id) ? ids.filter((x) => x !== o.id) : [...ids, o.id]);
  };
  const shown = (options ?? []).filter((o) => o.title.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 200);
  // linked to a files table: upload files into it and link them
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [upErr, setUpErr] = useState("");
  const uploadAndLink = async (list: FileList | null) => {
    if (!list?.length) return;
    setUploading(true); setUpErr("");
    const added: string[] = [];
    for (const f of Array.from(list).slice(0, single ? 1 : undefined)) {
      const body = new FormData();
      body.append("files", f);
      try {
        const r = await api.upload<{ records: { id: string }[] }>(`/tables/${tid}/files`, body);
        r.records.forEach((x) => { added.push(x.id); addRefs({ records: { [x.id]: f.name } }); });
      } catch (e) { setUpErr(`${f.name}: ${(e as Error).message}`); }
    }
    titleCache.set(tid, api.get(`/tables/${tid}/titles`));
    titleCache.get(tid)!.then(setOptions).catch(() => undefined);
    if (added.length) onCommit(single ? added.slice(0, 1) : [...ids, ...added]);
    setUploading(false);
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div ref={shell.setAnchorEl} className={inline ? "choice-inline" : "choice-field input"} onClick={() => !inline && shell.setOpen(true)}>
      <span className="chips">
        {ids.map((id) => <RefChip key={id} kind="record" label={refs.records[id] ?? ""} />)}
        {!ids.length && !inline && <span className="muted">{single ? "Link a record…" : "Link records…"}</span>}
      </span>
      {shell.open && shell.anchorEl && (
        <Popover anchor={shell.anchorEl} onClose={() => { setQ(""); shell.close(); }} width={280}>
          <input className="input" autoFocus placeholder="Search records…" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && shown[0]) toggle(shown[0]); }} />
          <div className="menu-list">
            {options === null && <div className="muted pad">Loading…</div>}
            {shown.map((o) => (
              <button key={o.id} className={`menu-item ${ids.includes(o.id) ? "active" : ""}`} onClick={() => toggle(o)}>
                <RefChip kind="record" label={o.title} />
                {ids.includes(o.id) && <span className="tick">✓</span>}
              </button>
            ))}
            {options !== null && !shown.length && <div className="muted pad">No matching records</div>}
          </div>
          {field.options._filesTable && !field.readonly && (
            <>
              <input ref={fileRef} type="file" multiple={!single} hidden onChange={(e) => uploadAndLink(e.target.files)} />
              <button className="btn sm link-upload" disabled={uploading} onClick={() => fileRef.current?.click()}>
                <Icon name="upload" size={14} /> {uploading ? "Uploading…" : single ? "Upload a file…" : "Upload files…"}
              </button>
              {upErr && <div className="error small">{upErr}</div>}
            </>
          )}
        </Popover>
      )}
    </div>
  );
}

function UserInput({ field, value, onCommit, onDone, inline }: InputProps) {
  const { refs, addRefs, me } = useContext(RefsContext);
  const ids: string[] = Array.isArray(value) ? value : value ? [value] : [];
  const multi = !!field.options.multiple;
  const shell = usePickerShell(inline, onDone);
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");

  const set = (id: string, name: string) => {
    addRefs({ users: { [id]: name } });
    const next = ids.includes(id) ? ids.filter((x) => x !== id) : multi ? [...ids, id] : [id];
    onCommit(next);
    if (!multi) shell.close();
  };
  const add = async () => {
    setErr("");
    try {
      const u = await api.get<User>(`/users/lookup?username=${encodeURIComponent(q.trim())}`);
      set(u.id, u.username);
      setQ("");
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <div ref={shell.setAnchorEl} className={inline ? "choice-inline" : "choice-field input"} onClick={() => !inline && shell.setOpen(true)}>
      <span className="chips">
        {ids.map((id) => <RefChip key={id} kind="user" label={refs.users[id] ?? "unknown"} />)}
        {!ids.length && !inline && <span className="muted">Add a user…</span>}
      </span>
      {shell.open && shell.anchorEl && (
        <Popover anchor={shell.anchorEl} onClose={() => { setQ(""); setErr(""); shell.close(); }} width={260}>
          <div className="menu-list">
            {me && (
              <button className={`menu-item ${ids.includes(me.id) ? "active" : ""}`} onClick={() => set(me.id, me.username)}>
                <RefChip kind="user" label={me.username} /> <span className="muted small">(you)</span>
                {ids.includes(me.id) && <span className="tick">✓</span>}
              </button>
            )}
            {ids.filter((id) => id !== me?.id).map((id) => (
              <button key={id} className="menu-item active" onClick={() => set(id, refs.users[id] ?? "")}>
                <RefChip kind="user" label={refs.users[id] ?? "unknown"} /><span className="tick">✓</span>
              </button>
            ))}
          </div>
          <div className="row" style={{ marginTop: 6 }}>
            <input className="input" placeholder="Add by exact username" value={q} onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && q.trim()) add(); }} />
            <button className="btn sm" disabled={!q.trim()} onClick={add}>Add</button>
          </div>
          {err && <div className="error small" style={{ marginTop: 6 }}>{err}</div>}
        </Popover>
      )}
    </div>
  );
}

function AttachmentInput({ value, onCommit, onDone, inline }: InputProps) {
  const items: Attachment[] = Array.isArray(value) ? value : [];
  const shell = usePickerShell(inline, onDone);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setErr("");
    const added: Attachment[] = [];
    for (const f of Array.from(files)) {
      const body = new FormData();
      body.append("file", f);
      const res = await fetch("/api/attachments", { method: "POST", body });
      if (res.ok) added.push(await res.json());
      else setErr(`${f.name}: ${(await res.json().catch(() => ({ detail: res.statusText }))).detail}`);
    }
    if (added.length) onCommit([...items, ...added]);
    setBusy(false);
    if (fileRef.current) fileRef.current.value = "";
  };

  const editor = (
    <div className="attach-editor">
      {items.map((a) => (
        <div key={a.id} className="attach-row">
          <AttachmentThumbs items={[a]} size={32} />
          <a className="attach-name" href={attachmentUrl(a, !viewOf(a))} target="_blank" rel="noreferrer">{a.name}</a>
          <span className="muted small">{a.size > 1048576 ? `${(a.size / 1048576).toFixed(1)} MB` : `${Math.ceil(a.size / 1024)} KB`}</span>
          <button className="icon-btn" title="Remove" onClick={() => onCommit(items.filter((x) => x.id !== a.id))}><Icon name="x" size={14} /></button>
        </div>
      ))}
      <input ref={fileRef} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
      <button className="btn sm" disabled={busy} onClick={() => fileRef.current?.click()}>
        <Icon name="upload" size={14} /> {busy ? "Uploading…" : "Upload files"}
      </button>
      {err && <div className="error small">{err}</div>}
    </div>
  );

  if (!inline) return editor;
  return (
    <div ref={shell.setAnchorEl} className="choice-inline">
      <AttachmentThumbs items={items} />
      {shell.open && shell.anchorEl && <Popover anchor={shell.anchorEl} onClose={shell.close} width={320}>{editor}</Popover>}
    </div>
  );
}

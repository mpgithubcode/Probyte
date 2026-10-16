import { createContext, useContext, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { api } from "../api";
import type { Attachment, Field, FieldView, Refs, User } from "../types";
import {
  choiceColor, cleanFormatted, displayText, formatDate, formatNumber, fromInputValue, innerField, isDateOnly, isEditable, isErrorValue,
  FORMAT_TYPES, formatPhone, shortUrl, telHref,
  toInputValue, valueType,
} from "../util";
import { Icon } from "./Icon";
import { Popover } from "./Overlay";
import { RecurrenceEditor } from "./RecurrenceEditor";
import { describe as describeRepeat } from "../recur";
import { displayOf, relativeDate } from "../display";
import { attachmentUrl, fileExt, thumbUrl, viewOf, previewStatus } from "../files";
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

/** Files tables: opens one of their files (by record id) in the full-screen viewer with its details; false = not ours. */
export const FileOpenContext = createContext<((item: Attachment) => boolean) | null>(null);

/** Small previews; a click opens the full-screen viewer (or downloads what can't be shown). */
const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

export function AttachmentThumbs({ items, size = 24 }: { items: Attachment[]; size?: number }) {
  const [open, setOpen] = useState<number | null>(null);
  const openFile = useContext(FileOpenContext);
  return (
    <span className="thumbs">
      {items.map((a, i) => {
        const src = thumbUrl(a);
        return (
          <a key={a.id} href={attachmentUrl(a, !viewOf(a))} target="_blank" rel="noreferrer" title={src ? a.name : [a.name, previewStatus(a)?.text].filter(Boolean).join(" · ")}
            onClick={(e) => {
              e.stopPropagation();
              if (openFile?.(a)) { e.preventDefault(); return; }
              if (viewOf(a) || a.file) { e.preventDefault(); setOpen(i); }
            }}
            className="thumb" style={{ width: size, height: size }}>
            {src ? <img src={src} alt={a.name} loading="lazy" /> : <span className="file-ext">{fileExt(a.name)}</span>}
            {!src && previewStatus(a) && <span className={`thumb-status ${previewStatus(a)!.state}`} />}
          </a>
        );
      })}
      {open !== null && (
        // (React passes events from the viewer, drawn over the page, up to this cell: a click on Edit or an arrow
        // there would select or edit the cell, which closes the viewer)
        <span className="viewer-host" onClick={stop} onMouseDown={stop} onPointerDown={stop} onDoubleClick={stop} onKeyDown={stop}
          onContextMenu={stop} onPaste={stop} onCopy={stop}>
          <FilePreview items={items} index={open} onClose={() => setOpen(null)} />
        </span>
      )}
    </span>
  );
}

/** The files in a value (an attachment field's, a files table's File, or lists of them, as lookups give). */
export function fileItems(v: any): Attachment[] {
  if (!v) return [];
  if (Array.isArray(v)) return v.flatMap(fileItems);
  return typeof v === "object" && v.id && v.name ? [v as Attachment] : [];
}

/** A link field that shows another field of its records than their title: that field's value for each, its files as
 * preview thumbnails (a click opens them) unless the field says to name them. */
function LinkedValues({ ids, shown, files }: { ids: string[]; shown: { field: Field; values: Record<string, any> }; files: boolean }) {
  const { refs } = useContext(RefsContext);
  const all = ids.map((id) => shown.values[id]);
  const items = fileItems(all);
  if (items.length && files) return <AttachmentThumbs items={items} />;
  return (
    <span className="chips">
      {ids.map((id, i) => {
        const v = all[i];
        const empty = v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length);
        return <span key={id} className="ref-chip record" title={refs.records[id]}>
          {empty ? <span className="muted">{refs.records[id] ?? ""}</span> : <CellValue field={shown.field} value={v} />}</span>;
      })}
    </span>
  );
}

/** A link's records as its field's chips look (options.chip): a chip with the title (and other fields of the record),
 * compact, two lines, a small card with a picture, or plain text; tinted by a select of the record when set. The same
 * everywhere the field shows (cells, the record panel, pages, boards, shared links, the picker's field box). */
export function LinkChips({ field, ids }: { field: Field; ids: string[] }) {
  const { refs } = useContext(RefsContext);
  const look = field.options.chip ?? {};
  const style = look.style ?? "chip";
  const extra = refs.chips?.[field.id];
  const def = (fid?: string | null) => (fid ? extra?.fields.find((f) => f.id === fid) : undefined);
  const shown = (look.fields ?? []).map((fid) => def(fid)).filter(Boolean) as Field[];
  const colorF = def(look.colorField), imageF = def(look.imageField);
  const title = (id: string) => refs.records[id] || "Untitled";
  if (style === "plain") return <span className="plain-values">{ids.map(title).join(", ")}</span>;
  return (
    <span className={`chips link-chips ${style}`}>
      {ids.map((id) => {
        const vals = extra?.values[id] ?? {};
        const tint = colorF && typeof vals[colorF.id] === "string" ? choiceColor(colorF, vals[colorF.id]) : null;
        const img = imageF ? fileItems(vals[imageF.id])[0] : undefined;
        const src = img ? thumbUrl(img) : null;
        const css = tint ? { background: `${tint}22`, borderColor: `${tint}66`, color: "var(--text)" } : undefined;
        const fieldsShown = style === "compact" ? [] : shown.filter((f) => !(vals[f.id] === null || vals[f.id] === undefined || vals[f.id] === "" ||
          (Array.isArray(vals[f.id]) && !vals[f.id].length)));
        if (style === "card") {
          return (
            <span key={id} className="link-card" style={css} title={title(id)}>
              {src && <img className="link-card-img" src={src} alt="" loading="lazy" />}
              <span className="link-card-body">
                <b className="link-card-title">{tint && <i className="link-dot" style={{ background: tint }} />}{title(id)}</b>
                {fieldsShown.map((f) => (
                  <span key={f.id} className="link-card-row"><span className="muted">{f.name}</span> <CellValue field={f} value={vals[f.id]} /></span>
                ))}
              </span>
            </span>
          );
        }
        return (
          <span key={id} className={`ref-chip record link-chip ${style}`} style={css} title={title(id)}>
            {src && <img className="link-chip-img" src={src} alt="" loading="lazy" />}
            {tint && !css && <i className="link-dot" style={{ background: tint }} />}
            <span className="link-chip-title">{title(id)}</span>
            {fieldsShown.length > 0 && (
              <span className="link-chip-fields">
                {fieldsShown.map((f) => <span key={f.id} className="link-chip-field" title={f.name}><CellValue field={f} value={vals[f.id]} /></span>)}
              </span>
            )}
          </span>
        );
      })}
    </span>
  );
}

const hasLook = (f: Field) => !!(f.options.chip && (f.options.chip.style && f.options.chip.style !== "chip" || f.options.chip.fields?.length
  || f.options.chip.colorField || f.options.chip.imageField));

/** A rollup's value as a bar: how full against `max` (its views.max; else 1 for fractions, 100 for the rest). */
export function ValueBar({ value, field }: { value: unknown; field: Field }) {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return <span>{String(value)}</span>;
  const max = field.options.views?.max ?? (Math.abs(n) <= 1 ? 1 : 100);
  const pct = Math.max(0, Math.min(100, (n / max) * 100));
  return (
    <span className="value-bar" title={`${formatNumber(n, field.options)} of ${formatNumber(max, field.options)}`}>
      <span className="value-bar-track"><span className="value-bar-fill" style={{ width: `${pct}%` }} /></span>
      <span className="value-bar-label">{max === 1 || max === 100 ? `${Math.round(pct)}%` : formatNumber(n, field.options)}</span>
    </span>
  );
}

/** "3 records" / "2 files": a field holding several, shown as how many. */
export function CountValue({ field, n }: { field: Field; n: number }) {
  const [one, many] = field.type === "link" ? ["record", "records"] : field.type === "attachment" ? ["file", "files"]
    : ["user", "created_by", "last_modified_by"].includes(field.type) ? ["person", "people"] : ["value", "values"];
  return <span className="count-value">{n} {n === 1 ? one : many}</span>;
}

/** A value shown one of its other ways (display.ts); null: its ordinary look. */
function shownAs(field: Field, value: any, how: FieldView, refs: Refs): ReactNode | null {
  const items: any[] = Array.isArray(value) ? value : [value];
  const one = (x: any, i: number) => <CellValue key={i} field={field} value={field.type === "single_select" || field.type === "checkbox" ? x : [x]}
    view={field.type === "attachment" ? "thumbs" : field.type === "checkbox" ? "check" : field.type === "rating" ? "stars" : "chips"} />;
  const name = (x: any): string => field.type === "link" ? refs.records[x] || "Untitled"
    : ["user", "created_by", "last_modified_by"].includes(field.type) ? refs.users[x] ?? "unknown"
    : field.type === "attachment" ? (x as Attachment).name
    : field.type === "lookup" || field.type === "conditional_lookup" ? (innerField(field) ? displayText(innerField(field)!, x, refs) : String(x))
    : String(x);
  switch (how) {
    case "count": return <CountValue field={field} n={items.length} />;
    case "text": case "names": return <span className="plain-values">{items.map(name).join(", ")}</span>;
    case "list": return <ul className="value-list">{items.map((x, i) => <li key={i}>{one(x, i)}</li>)}</ul>;
    case "hcards": return <span className="value-hcards">{items.map((x, i) => <span key={i} className="value-hcard">{one(x, i)}</span>)}</span>;
    case "bar": return <ValueBar value={value} field={field} />;
    case "stat": return <span className="stat-value">{typeof value === "number" ? formatNumber(value, field.options) : String(value)}</span>;
    case "number": return <span className="num">{Number(value) || 0} / {field.options.max ?? 5}</span>;
    case "yesno": return <span className={`yesno ${value ? "yes" : "no"}`}>{value ? "Yes" : "No"}</span>;
    case "relative": return typeof value === "string" ? <span title={formatDate(value, !isDateOnly(value), field.options)}>{relativeDate(value)}</span> : null;
    case "gallery": return <span className="value-gallery"><AttachmentThumbs items={fileItems(value)} size={104} /></span>;
    case "large": return <span className="large-text">{field.type === "phone" ? formatPhone(value) : String(value)}</span>;
    default: return null;
  }
}

/** Long text with fields placed in it as a page's body ({{field:<id>}} lines): those lines read as the fields' names. */
export const embedLabel = (text: string, fields?: Field[]) =>
  text.replace(/^\{\{field:([\w-]+)\}\}$/gm, (_, id) => `[${fields?.find((f) => f.id === id)?.name ?? "field"}]`);
/** Long text as a cell shows it: without the Markdown marks a page body may have (#, list bullets, **, `). */
const plainText = (text: string) => text.replace(/^#{1,6}\s+/gm, "").replace(/^\s*[-*+]\s+/gm, "• ").replace(/(\*\*|__|`)/g, "");
export const FieldsContext = createContext<Field[] | undefined>(undefined);

/** Read-only rendering of a value. `view`: how (see FieldViews); unset = the field's own choice for the table's cells. */
export function CellValue({ field, value, view }: { field: Field; value: any; view?: FieldView }) {
  const { refs } = useContext(RefsContext);
  const siblings = useContext(FieldsContext);
  if (isErrorValue(value)) return <span className="cell-error" title={value.__error}>#ERROR</span>;
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return null;
  const how = view ?? displayOf(field, "cell");
  const special = how ? shownAs(field, value, how, refs) : null;
  if (special) return special;
  switch (field.type) {
    case "long_text":
      return <span>{plainText(embedLabel(String(value), siblings))}</span>;
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
      return <span>{formatDate(value, true, field.options)}</span>;
    case "recurrence":
      return <span className="repeat-value"><Icon name="repeat" size={12} /> {describeRepeat(value)}</span>;
    case "rating":
      return <span className="stars">{"★".repeat(Number(value) || 0)}</span>;
    case "url": {
      // only web addresses become links (anything else, e.g. synced in, is shown as text)
      const u = String(value);
      return /^https?:\/\//i.test(u)
        ? <a className="cell-link" href={u} target="_blank" rel="noopener noreferrer nofollow" title={u} onClick={(e) => e.stopPropagation()}>{shortUrl(u)}</a>
        : <span>{u}</span>;
    }
    case "email":
      return <a className="cell-link" href={`mailto:${value}`} title={`Write to ${value}`} onClick={(e) => e.stopPropagation()}>{String(value)}</a>;
    case "phone":
      return <a className="cell-link" href={telHref(String(value))} title={`Call ${formatPhone(value)}`} onClick={(e) => e.stopPropagation()}>{formatPhone(value)}</a>;
    case "link": {
      const shown = refs.display?.[field.id];
      if (shown) return <LinkedValues ids={value as string[]} shown={shown} files={field.options.displayFiles !== false} />;
      if (hasLook(field)) return <LinkChips field={field} ids={value as string[]} />;
      return <span className="chips">{(value as string[]).map((id) => <RefChip key={id} kind="record" label={refs.records[id] ?? ""} />)}</span>;
    }
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
      if (inner?.type === "attachment" && field.options.displayFiles === false) {
        return <span>{fileItems(items).map((a) => a.name).join(", ")}</span>;
      }
      if (inner && ["single_select", "multi_select", "link", "user", "attachment", "checkbox", "rating"].includes(inner.type)) {
        return <span className="chips">{items.map((x, i) => (
          <CellValue key={i} field={inner} value={inner.type === "link" || inner.type === "user" || inner.type === "attachment" ? [x] : x} />))}</span>;
      }
      // a lookup's own number / date format, when it has one; else as the looked-up field shows them
      const own = field.options.numberFormat || field.options.precision != null || field.options.dateFormat || field.options.timeFormat;
      const show = (x: unknown) => own && typeof x === "number" ? formatNumber(x, field.options)
        : own && inner && valueType(inner) === "date" && typeof x === "string" ? formatDate(x, !isDateOnly(x), field.options)
        : inner ? displayText(inner, x, refs) : String(x);
      return <span>{items.map(show).join(", ")}</span>;
    }
  }
  const vt = valueType(field);
  if (vt === "boolean") return <span className={`check ${value ? "on" : ""}`}>{value ? "✓" : ""}</span>;
  if (vt === "number" && typeof value === "number") return <span className="num">{formatNumber(value, field.options)}</span>;
  if (vt === "date" || (typeof value === "string" && /^\d{4}-\d{2}-\d{2}(T[\d:.]+Z)?$/.test(value) && field.type === "formula")) {
    return <span>{formatDate(value, !isDateOnly(value), field.options)}</span>;
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
  asButton?: boolean;        // links: only a "Link records" button that opens the picker (the records are shown elsewhere)
}

/** A link's picker behind a button (a page or record shows the linked records in a list, table or cards). */
export const LinkPicker = (props: InputProps) => <LinkInput {...props} asButton />;

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
  const show = (x: unknown) => (field.type === "phone" && x ? formatPhone(x) : String(x ?? ""));  // phone numbers: as they're shown
  const original = show(value);
  const [draft, setDraft] = useState<string>(initial ?? original);
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
  const [invalid, setInvalid] = useState("");
  useEffect(() => { if (!focused.current) setDraft(show(value)); }, [value]);

  const commit = () => {
    focused.current = false;
    if (cancelled.current) { cancelled.current = false; setInvalid(""); setDraft(String(original)); onDone?.(); return; }
    let v: any = draft;
    if (FORMAT_TYPES.has(field.type)) {  // email / web address / phone: the same rules as the server
      const c = cleanFormatted(field.type, draft);
      if ("error" in c) {
        setInvalid(c.error);
        setTimeout(() => inputRef.current?.focus(), 0);  // stay in the field to fix it (Escape cancels)
        return;
      }
      setInvalid("");
      v = c.value;
      if (v !== null && show(v) !== draft) setDraft(show(v));
    }
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
    onChange: (e: { target: { value: string } }) => { setDraft(e.target.value); if (invalid) setInvalid(""); },
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
  const input = <input {...common} type={field.type === "number" ? "number" : field.type === "email" ? "email" : field.type === "url" ? "url" : field.type === "phone" ? "tel" : "text"}
    aria-invalid={invalid ? true : undefined} title={invalid || undefined}
    className={`${common.className}${invalid ? " invalid" : ""}`}
    placeholder={inline ? undefined : field.type === "url" ? "example.com" : field.type === "email" ? "name@example.com" : field.type === "phone" ? "+1 555 0100" : undefined}
    onKeyDown={(e) => {
      if (e.key === "Escape") { cancelled.current = true; setInvalid(""); e.currentTarget.blur(); }
      if (e.key === "Enter") e.currentTarget.blur();
    }} />;
  // record panel: a button to call / write / open what's in the field
  const action = !inline && FORMAT_TYPES.has(field.type) && typeof value === "string" && value
    ? field.type === "phone" ? { href: telHref(value), icon: "phone", label: `Call ${formatPhone(value)}` }
      : field.type === "email" ? { href: `mailto:${value}`, icon: "external", label: `Write to ${value}` }
        : /^https?:\/\//i.test(value) ? { href: value, icon: "external", label: `Open ${value}` } : null
    : null;
  if (!invalid && !action) return input;
  if (!invalid) {
    return (
      <span className="input-with-action">{input}
        <a className="icon-btn field-action" href={action!.href} title={action!.label} aria-label={action!.label}
          {...(field.type === "url" ? { target: "_blank", rel: "noopener noreferrer nofollow" } : {})}><Icon name={action!.icon} size={14} /></a>
      </span>
    );
  }
  return <span className={`input-wrap${inline ? " inline" : ""}`}>{input}<span className="input-error" role="alert">{invalid}</span></span>;
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
export const invalidateTitles = (tableId?: string) => {
  if (!tableId) { titleCache.clear(); return; }
  for (const k of [...titleCache.keys()]) if (k === tableId || k.startsWith(`${tableId}:`)) titleCache.delete(k);  // (and links' own lists)
};

interface PickData { fields: Field[]; records: { id: string; title: string; data: Record<string, any> }[]; total: number; refs: Refs;
  can_add: boolean; primary: Field | null }

function LinkInput({ field, value, onCommit, onDone, inline, asButton }: InputProps) {
  const { refs, addRefs, me } = useContext(RefsContext);
  const ids: string[] = Array.isArray(value) ? value : [];
  const single = field.options.relationship === "one";
  const shell = usePickerShell(inline, onDone);
  const [q, setQ] = useState("");
  const [data, setData] = useState<PickData | null>(null);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const tid = field.options.foreignTableId ?? "";
  const [reloadKey, setReloadKey] = useState(0);

  // the records it offers, as a table (the columns the field picks, else the first few), searched on the server
  useEffect(() => {
    if (!shell.open || !tid) return;
    let live = true;
    const t = setTimeout(() => {
      api.get<PickData>(`/tables/${tid}/pick?field=${encodeURIComponent(field.id)}&q=${encodeURIComponent(q.trim())}&limit=100`)
        .then((d) => { if (live) { setData(d); setActive(0); } })
        .catch((e) => { if (live) { setErr((e as Error).message); setData({ fields: [], records: [], total: 0, refs: { records: {}, users: {} }, can_add: false, primary: null }); } });
    }, data ? 150 : 0);
    return () => { live = false; clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shell.open, tid, q, reloadKey]);

  const toggle = (o: { id: string; title: string }) => {
    addRefs({ records: { [o.id]: o.title } });
    if (single) { onCommit(ids[0] === o.id ? [] : [o.id]); setQ(""); shell.close(); return; }
    onCommit(ids.includes(o.id) ? ids.filter((x) => x !== o.id) : [...ids, o.id]);
  };
  const rows = data?.records ?? [];
  const typed = q.trim();
  const canCreate = !!data?.can_add && !field.readonly && !!typed && !!data.primary && isEditable(data.primary)
    && !rows.some((r) => r.title.trim().toLowerCase() === typed.toLowerCase());
  // a new record in the linked table, named as typed, linked at once
  const create = async () => {
    if (!canCreate || busy || !data?.primary) return;
    setBusy(true); setErr("");
    try {
      const rec = await api.post<{ id: string }>(`/tables/${tid}/records`, { data: { [data.primary.id]: typed } });
      invalidateTitles(tid);
      toggle({ id: rec.id, title: typed });
      setQ("");
      setReloadKey((k) => k + 1);
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    const n = rows.length + (canCreate ? 1 : 0);
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(n - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (active < rows.length && rows[active]) toggle(rows[active]);
      else if (canCreate) create();
    }
  };
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
    invalidateTitles(tid);
    setReloadKey((k) => k + 1);
    if (added.length) onCommit(single ? added.slice(0, 1) : [...ids, ...added]);
    setUploading(false);
    if (fileRef.current) fileRef.current.value = "";
  };
  const cols = data?.fields ?? [];
  const pickRefs = data ? { records: { ...refs.records, ...data.refs.records }, users: { ...refs.users, ...data.refs.users } } : refs;

  return (
    <div ref={shell.setAnchorEl} className={asButton ? "link-picker-btn" : inline ? "choice-inline" : "choice-field input"} onClick={() => !inline && shell.setOpen(true)}>
      {asButton ? (
        <button type="button" className="btn sm"><Icon name="plus" size={14} /> {single ? (ids.length ? "Change record" : "Link a record") : "Link records"}</button>
      ) : (
      <span className="chips">
        {hasLook(field) ? <LinkChips field={field} ids={ids} /> : ids.map((id) => <RefChip key={id} kind="record" label={refs.records[id] ?? ""} />)}
        {!ids.length && !inline && <span className="muted">{single ? "Link a record…" : "Link records…"}</span>}
      </span>
      )}
      {shell.open && shell.anchorEl && (
        <Popover anchor={shell.anchorEl} onClose={() => { setQ(""); shell.close(); }} width={Math.min(cols.length ? 420 + cols.length * 110 : 380, 760)}
          className="link-pick-popover">
          <input className="input" autoFocus placeholder={data?.can_add && !field.readonly ? "Search or create…" : "Search records…"} value={q}
            onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
          <div className="link-pick-table-wrap" onClick={(e) => e.stopPropagation()}>
            {data === null ? <div className="muted pad">Loading…</div> : (
              <table className="link-pick-table">
                {rows.length > 0 && (
                  <thead><tr><th className="pick-check" /><th>{data.primary?.name ?? "Name"}</th>{cols.map((c) => <th key={c.id}>{c.name}</th>)}</tr></thead>
                )}
                <RefsContext.Provider value={{ refs: pickRefs, addRefs, me }}>
                  <tbody>
                    {rows.map((r, i) => {
                      const on = ids.includes(r.id);
                      return (
                        <tr key={r.id} className={`${on ? "on" : ""} ${i === active ? "active" : ""}`} onMouseEnter={() => setActive(i)}
                          onClick={() => toggle(r)} aria-selected={on}>
                          <td className="pick-check">{on ? "✓" : ""}</td>
                          <td className="pick-title">{r.title}</td>
                          {cols.map((c) => <td key={c.id}><CellValue field={c} value={r.data[c.id]} /></td>)}
                        </tr>
                      );
                    })}
                  </tbody>
                </RefsContext.Provider>
              </table>
            )}
            {data !== null && !rows.length && !canCreate && <div className="muted pad">{typed ? "No matching records" : "No records yet"}</div>}
            {canCreate && (
              <button type="button" className={`menu-item link-pick-create ${active === rows.length ? "active" : ""}`} disabled={busy}
                onMouseEnter={() => setActive(rows.length)} onClick={create}>
                <Icon name="plus" size={14} /> {busy ? "Creating…" : <>Create <b>{typed}</b>{single ? "" : " and link it"}</>}
              </button>
            )}
            {data !== null && data.total > rows.length && <div className="muted small pad">Showing {rows.length} of {data.total}: type to narrow down</div>}
            {err && <div className="error small">{err}</div>}
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

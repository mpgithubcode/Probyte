import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api";
import { FN_CATEGORIES, FORMULA_FUNCTIONS, OPERATORS_DOC, type FnDoc } from "../formulaDocs";
import type { Field } from "../types";
import { FIELD_TYPES } from "../util";
import { FieldIcon } from "./Icon";

interface Check { ok: boolean; error?: string; resultType?: string; preview?: { title: string; value: unknown }[] }

const TOKEN = /("(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?)|(\{(?:[^}\\]|\\.)*\}?)|([A-Za-z_][A-Za-z0-9_]*)(?=\s*\()|(\d+\.?\d*)|(&&|\|\||<=|>=|!=|<>|[-+*/%&=<>!])/g;

function highlight(src: string, names: Set<string>) {
  const out: (string | JSX.Element)[] = [];
  let last = 0, k = 0;
  for (const m of src.matchAll(TOKEN)) {
    if (m.index! > last) out.push(src.slice(last, m.index));
    const [text, str, field, fn, num, op] = m;
    const cls = str ? "hl-str" : field ? (names.has(field.slice(1, -1).replace(/\\(.)/g, "$1")) ? "hl-field" : "hl-field bad")
      : fn ? (FORMULA_FUNCTIONS.some((f) => f.name === fn.toUpperCase()) ? "hl-fn" : "hl-fn bad") : num ? "hl-num" : op ? "hl-op" : "";
    out.push(<span key={k++} className={cls}>{text}</span>);
    last = m.index! + text.length;
  }
  out.push(src.slice(last) + "\n");
  return out;
}

const typeLabel = (t: string) => FIELD_TYPES.find((x) => x.type === t)?.label ?? t;
const RESULT_LABEL: Record<string, string> = { number: "a number", text: "text", boolean: "true / false", date: "a date", array: "a list", any: "a value" };

function previewText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "(empty)";
  if (typeof v === "object" && v && "__error" in v) return `#ERROR: ${(v as { __error: string }).__error}`;
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

export function FormulaEditor({ tableId, fieldId, fields, value, onChange }: {
  tableId: string; fieldId?: string; fields: Field[]; value: string; onChange: (v: string) => void;
}) {
  const ta = useRef<HTMLTextAreaElement>(null);
  const pre = useRef<HTMLPreElement>(null);
  const [caret, setCaret] = useState(value.length);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const [check, setCheck] = useState<Check | null>(null);
  const [doc, setDoc] = useState<FnDoc | Field | null>(null);
  const [q, setQ] = useState("");

  const usable = fields.filter((f) => f.id !== fieldId && f.type !== "button");
  const names = useMemo(() => new Set(usable.map((f) => f.name)), [usable]);

  // Validate + preview on the server, debounced.
  useEffect(() => {
    if (!value.trim()) { setCheck(null); return; }
    const t = setTimeout(() => {
      api.post<Check>(`/tables/${tableId}/formula`, { expression: value, field_id: fieldId })
        .then(setCheck).catch((e) => setCheck({ ok: false, error: e.message }));
    }, 350);
    return () => clearTimeout(t);
  }, [value, tableId, fieldId]);

  // ---- autocomplete for the token at the caret
  const before = value.slice(0, caret);
  const inString = (before.match(/(?<!\\)"/g)?.length ?? 0) % 2 === 1;
  const fieldPart = !inString ? /\{([^}]*)$/.exec(before) : null;
  const fnPart = !inString && !fieldPart ? /(?:^|[^A-Za-z0-9_{])([A-Za-z_][A-Za-z0-9_]*)$/.exec(before) : null;
  const suggestions: ({ kind: "field"; f: Field } | { kind: "fn"; d: FnDoc })[] = dismissed ? [] : fieldPart
    ? usable.filter((f) => f.name.toLowerCase().includes(fieldPart[1].toLowerCase())).slice(0, 8).map((f) => ({ kind: "field" as const, f }))
    : fnPart
      ? [
          ...usable.filter((f) => f.name.toLowerCase().startsWith(fnPart[1].toLowerCase())).slice(0, 3).map((f) => ({ kind: "field" as const, f })),
          ...FORMULA_FUNCTIONS.filter((d) => d.name.startsWith(fnPart[1].toUpperCase())).slice(0, 8).map((d) => ({ kind: "fn" as const, d })),
        ]
      : [];

  useEffect(() => { setActive(0); }, [fieldPart?.[1], fnPart?.[1]]);

  const replaceRange = (start: number, end: number, text: string, caretOffset = text.length) => {
    const next = value.slice(0, start) + text + value.slice(end);
    onChange(next);
    const pos = start + caretOffset;
    requestAnimationFrame(() => {
      ta.current?.focus();
      ta.current?.setSelectionRange(pos, pos);
      setCaret(pos);
    });
  };

  const accept = (s: (typeof suggestions)[number]) => {
    if (s.kind === "field") {
      const start = fieldPart ? caret - fieldPart[1].length - 1 : caret - (fnPart?.[1].length ?? 0);
      const end = value[caret] === "}" ? caret + 1 : caret;
      replaceRange(start, end, `{${s.f.name}}`);
      setDoc(s.f);
    } else {
      const start = caret - (fnPart?.[1].length ?? 0);
      const hasParen = value[caret] === "(";
      replaceRange(start, hasParen ? caret + 1 : caret, `${s.d.name}()`, s.d.name.length + 1);
      setDoc(s.d);
    }
  };

  const insert = (text: string, caretOffset?: number) => {
    const el = ta.current;
    const s = el?.selectionStart ?? value.length, e = el?.selectionEnd ?? value.length;
    replaceRange(s, e, text, caretOffset);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!suggestions.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % suggestions.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a - 1 + suggestions.length) % suggestions.length); }
    else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); accept(suggestions[active]); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setDismissed(true); }
  };

  const ql = q.trim().toLowerCase();
  const shownFields = usable.filter((f) => f.name.toLowerCase().includes(ql));
  const shownFns = FORMULA_FUNCTIONS.filter((d) => d.name.toLowerCase().includes(ql) || d.cat.toLowerCase().includes(ql));

  return (
    <div className="formula-editor">
      <div className="formula-main">
        <div className="formula-input">
          <pre ref={pre} className="formula-highlight" aria-hidden>{highlight(value, names)}</pre>
          <textarea ref={ta} value={value} spellCheck={false} autoFocus placeholder={'e.g. IF({Status} = "Done", "✓", "")'}
            onChange={(e) => { onChange(e.target.value); setCaret(e.target.selectionStart); setDismissed(false); }}
            onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
            onScroll={(e) => { if (pre.current) { pre.current.scrollTop = e.currentTarget.scrollTop; pre.current.scrollLeft = e.currentTarget.scrollLeft; } }}
            onKeyDown={onKeyDown} />
        </div>
        {suggestions.length > 0 && (
          <div className="formula-suggest">
            {suggestions.map((s, i) => (
              <button key={s.kind === "field" ? s.f.id : s.d.name} className={`menu-item ${i === active ? "active" : ""}`}
                onMouseDown={(e) => { e.preventDefault(); accept(s); }} onMouseEnter={() => setActive(i)}>
                {s.kind === "field"
                  ? <><FieldIcon type={s.f.type} /> <span>{s.f.name}</span><span className="muted small">{typeLabel(s.f.type)}</span></>
                  : <><span className="fn-badge">ƒ</span> <span className="mono">{s.d.name}</span><span className="muted small">{s.d.cat}</span></>}
              </button>
            ))}
          </div>
        )}
        <div className={`formula-status ${check ? (check.ok ? "ok" : "bad") : ""}`}>
          {!check && <span className="muted">{OPERATORS_DOC}</span>}
          {check && !check.ok && <span>✗ {check.error}</span>}
          {check?.ok && (
            <>
              <span>✓ Returns {RESULT_LABEL[check.resultType ?? "any"]}</span>
              {!!check.preview?.length && (
                <div className="formula-preview">
                  {check.preview.map((p, i) => (
                    <div key={i}><span className="muted">{p.title}</span> → <b>{previewText(p.value)}</b></div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
        <div className="formula-doc">
          {doc && "sig" in doc ? (
            <>
              <div className="mono doc-sig">{doc.sig}</div>
              <div>{doc.desc}</div>
              <div className="doc-ex"><span className="muted">Example</span> <code>{doc.ex}</code></div>
            </>
          ) : doc ? (
            <>
              <div className="doc-sig"><FieldIcon type={doc.type} /> {doc.name}</div>
              <div className="muted">{typeLabel(doc.type)} field. Insert it as <code>{`{${doc.name}}`}</code>.</div>
            </>
          ) : (
            <div className="muted">Pick a field or function from the list to see how to use it.</div>
          )}
        </div>
      </div>

      <div className="formula-side">
        <input className="input sm" placeholder="Search fields & functions" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="formula-list">
          {shownFields.length > 0 && <div className="menu-heading">Fields</div>}
          {shownFields.map((f) => (
            <button key={f.id} className="menu-item" onMouseEnter={() => setDoc(f)} onClick={() => { insert(`{${f.name}}`); setDoc(f); }}>
              <FieldIcon type={f.type} /> {f.name}
            </button>
          ))}
          {FN_CATEGORIES.map((cat) => {
            const list = shownFns.filter((d) => d.cat === cat);
            return list.length ? (
              <Fragment key={cat}>
                <div className="menu-heading">{cat}</div>
                {list.map((d) => (
                  <button key={d.name} className="menu-item mono" onMouseEnter={() => setDoc(d)}
                    onClick={() => { insert(`${d.name}()`, d.name.length + 1); setDoc(d); }}>{d.name}</button>
                ))}
              </Fragment>
            ) : null;
          })}
        </div>
      </div>
    </div>
  );
}

import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { formatJson, highlight, isMinifiedJson, languageOf, lineNumbers } from "../code";

const MAX_COLOR = 1024 * 1024;        // larger files are shown without colors (still with line numbers)
const MAX_COLOR_EDIT = 256 * 1024;    // …and edited without them (colored again on every key)

/** A text or code file as in a code editor: line numbers, colors for its language, and JSON written on one line laid
 * out in lines for reading (the file itself is unchanged; "As saved" shows it as it is). */
export function CodeView({ name, text }: { name: string; text: string }) {
  const lang = languageOf(name);
  const minified = isMinifiedJson(text, lang);
  const [formatted, setFormatted] = useState(true);
  const shown = useMemo(() => (minified && formatted ? formatJson(text) : text), [text, minified, formatted]);
  const html = useMemo(() => highlight(shown, shown.length <= MAX_COLOR ? lang : null), [shown, lang]);
  const numbers = useMemo(() => lineNumbers(shown), [shown]);
  if (!text) return <pre className="viewer-code"><span className="muted">(empty file)</span></pre>;
  return (
    <div className="code-file">
      {(lang || minified) && (
        <div className="code-bar">
          {lang && <span className="code-lang">{lang.name}</span>}
          {minified && <>
            <span className="muted">{formatted ? "Laid out for reading; the file is unchanged" : "As saved (on one line)"}</span>
            <button className="code-btn" onClick={() => setFormatted(!formatted)}>{formatted ? "As saved" : "Laid out"}</button>
          </>}
        </div>
      )}
      <div className="code-scroll">
        <pre className="code-gutter" aria-hidden>{numbers}</pre>
        <pre className="code-text" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    </div>
  );
}

/** Editing a text or code file: the same colors and line numbers as you type; Tab indents; JSON can be laid out. */
export function CodeEditor({ name, value, onChange }: { name: string; value: string; onChange: (s: string) => void }) {
  const lang = languageOf(name);
  const ta = useRef<HTMLTextAreaElement>(null);
  const pre = useRef<HTMLPreElement>(null);
  const gutter = useRef<HTMLPreElement>(null);
  const [err, setErr] = useState("");
  const html = useMemo(() => highlight(value, value.length <= MAX_COLOR_EDIT ? lang : null), [value, lang]);
  const numbers = useMemo(() => lineNumbers(value), [value]);
  const indent = lang?.name === "Python" ? "    " : "  ";

  const sync = () => {
    const t = ta.current;
    if (!t) return;
    if (pre.current) { pre.current.scrollTop = t.scrollTop; pre.current.scrollLeft = t.scrollLeft; }
    if (gutter.current) gutter.current.scrollTop = t.scrollTop;
  };
  // type through the browser, so Ctrl/⌘ Z still undoes it
  const insert = (s: string) => {
    if (!document.execCommand("insertText", false, s)) {
      const t = ta.current!, a = t.selectionStart;
      onChange(value.slice(0, a) + s + value.slice(t.selectionEnd));
      requestAnimationFrame(() => t.setSelectionRange(a + s.length, a + s.length));
    }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    const t = e.currentTarget;
    if (e.key === "Tab" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      insert(indent);
    } else if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      // keep the line's indent (one more after an opening bracket or a colon in Python)
      const start = value.lastIndexOf("\n", t.selectionStart - 1) + 1;
      const line = value.slice(start, t.selectionStart);
      const lead = /^[ \t]*/.exec(line)![0];
      const more = /[{[(]\s*$/.test(line) || (lang?.name === "Python" && /:\s*$/.test(line)) ? indent : "";
      e.preventDefault();
      insert("\n" + lead + more);
    }
  };
  const layOut = () => {
    try {
      onChange(JSON.stringify(JSON.parse(value), null, 2));
      setErr("");
    } catch (x) { setErr(`Not valid JSON: ${(x as Error).message}`); }
    ta.current?.focus();   // back to typing (and Ctrl/⌘ S)
  };

  return (
    <div className="code-file">
      {lang && (
        <div className="code-bar">
          <span className="code-lang">{lang.name}</span>
          {lang.name === "JSON" && <button className="code-btn" onClick={layOut} title="Lay the JSON out in indented lines">Format</button>}
          {err && <span className="code-err">{err}</span>}
        </div>
      )}
      <div className="code-edit">
        <pre ref={gutter} className="code-gutter" aria-hidden>{numbers + "\n"}</pre>
        <div className="code-edit-main">
          <pre ref={pre} className="code-text code-hl" aria-hidden dangerouslySetInnerHTML={{ __html: html + "\n " }} />
          <textarea ref={ta} className="code-input" autoFocus spellCheck={false} wrap="off" autoCapitalize="off" autoCorrect="off"
            value={value} onChange={(e) => { onChange(e.target.value); setErr(""); }} onScroll={sync} onKeyDown={onKey} />
        </div>
      </div>
    </div>
  );
}

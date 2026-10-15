/** Code files in the viewer: which language a file is, its text colored (as HTML), and JSON laid out in lines.
 * Keep the languages in step with backend/app/files/render.py, which draws the same colors on covers. */

const C_KW = ("if else for while do switch case break continue return function var let const class extends new this super import " +
  "export from default try catch finally throw typeof instanceof in of void delete yield async await static public private " +
  "protected interface enum type implements package namespace using struct union typedef sizeof goto int long short char " +
  "float double bool boolean unsigned signed auto fn impl trait mut pub use mod match where loop move ref self Self " +
  "func go defer chan map range select fallthrough val fun object when is as override open data sealed lateinit " +
  "echo foreach endif elseif endforeach endwhile include require_once require extern inline virtual template typename " +
  "true false null nil undefined NaN None").split(" ");
const PY_KW = ("def class if elif else for while try except finally with as return yield import from pass break continue raise " +
  "lambda global nonlocal assert del in is not and or async await match case True False None self").split(" ");
const SH_KW = ("if then else elif fi for while until do done case esac function return in local export readonly unset shift exit " +
  "echo set source alias cd true false").split(" ");
const RB_KW = "def end class module if elsif else unless while until for do return yield begin rescue ensure raise require self nil true false and or not then".split(" ");
const SQL_KW = ("select from where insert into values update set delete create table drop alter add column index view join left right " +
  "inner outer full on group by order having limit offset union all distinct as and or not null is in like between case " +
  "when then else end primary key foreign references default unique begin commit rollback with returning exists true false").split(" ");
const LUA_KW = "function end local if then else elseif for while do repeat until return break in and or not nil true false".split(" ");

interface Lang { name: string; line?: string; block?: [string, string]; kw: string[]; nocase?: boolean; triple?: boolean; markup?: boolean }

const LANGS: Record<string, Lang> = {};
const def = (exts: string, lang: Lang) => exts.split(" ").forEach((e) => { LANGS[e] = lang; });
def("js mjs cjs jsx", { name: "JavaScript", line: "//", block: ["/*", "*/"], kw: C_KW });
def("ts tsx", { name: "TypeScript", line: "//", block: ["/*", "*/"], kw: C_KW });
def("java", { name: "Java", line: "//", block: ["/*", "*/"], kw: C_KW });
def("kt kts", { name: "Kotlin", line: "//", block: ["/*", "*/"], kw: C_KW });
def("c h", { name: "C", line: "//", block: ["/*", "*/"], kw: C_KW });
def("cpp hpp cc", { name: "C++", line: "//", block: ["/*", "*/"], kw: C_KW });
def("cs", { name: "C#", line: "//", block: ["/*", "*/"], kw: C_KW });
def("go", { name: "Go", line: "//", block: ["/*", "*/"], kw: C_KW });
def("rs", { name: "Rust", line: "//", block: ["/*", "*/"], kw: C_KW });
def("php", { name: "PHP", line: "//", block: ["/*", "*/"], kw: C_KW });
def("swift", { name: "Swift", line: "//", block: ["/*", "*/"], kw: C_KW });
def("dart", { name: "Dart", line: "//", block: ["/*", "*/"], kw: C_KW });
def("scala groovy", { name: "Scala", line: "//", block: ["/*", "*/"], kw: C_KW });
def("vue svelte", { name: "Component", line: "//", block: ["/*", "*/"], kw: C_KW });
def("css scss less", { name: "CSS", line: "//", block: ["/*", "*/"], kw: [] });
def("json", { name: "JSON", kw: ["true", "false", "null"] });
def("py pyw", { name: "Python", line: "#", kw: PY_KW, triple: true });
def("sh bash zsh", { name: "Shell", line: "#", kw: SH_KW });
def("ps1", { name: "PowerShell", line: "#", kw: SH_KW });
def("env gitignore dockerfile makefile conf cfg ini toml", { name: "Config", line: "#", kw: SH_KW });
def("pl r", { name: "Script", line: "#", kw: SH_KW });
def("rb", { name: "Ruby", line: "#", kw: RB_KW });
def("yaml yml", { name: "YAML", line: "#", kw: "true false null yes no on off".split(" ") });
def("sql", { name: "SQL", line: "--", block: ["/*", "*/"], kw: SQL_KW, nocase: true });
def("lua", { name: "Lua", line: "--", kw: LUA_KW });
def("xml svg", { name: "XML", block: ["<!--", "-->"], kw: [], markup: true });
def("html htm", { name: "HTML", block: ["<!--", "-->"], kw: [], markup: true });
def("bat cmd", { name: "Batch", line: "REM ", kw: "echo set if else goto call exit for in do not exist".split(" "), nocase: true });

export const codeExt = (name: string) => {
  const base = name.split("/").pop()!.toLowerCase();
  return base.includes(".") ? base.split(".").pop()! : base;
};
/** The file's language (null: not code, shown as plain text). */
export const languageOf = (name: string): Lang | null => LANGS[codeExt(name)] ?? null;

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const reEsc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const END = "$(?![\\s\\S])";   // the end of the text (with the m flag, $ alone is the end of a line)
const patterns = new Map<Lang, RegExp>();

function pattern(lang: Lang): RegExp {
  let re = patterns.get(lang);
  if (re) return re;
  const parts: string[] = [];
  if (lang.block) parts.push(`(?<comment>${reEsc(lang.block[0])}[\\s\\S]*?(?:${reEsc(lang.block[1])}|${END}))`);
  if (lang.line) parts.push(`(?<lcomment>${reEsc(lang.line)}.*)`);
  if (lang.markup) parts.push(`(?<tag><\\/?[A-Za-z][\\w:.-]*|\\/?>)`);
  if (lang.triple) parts.push(`(?<string3>"""[\\s\\S]*?(?:"""|${END})|'''[\\s\\S]*?(?:'''|${END}))`);
  parts.push(`(?<string>"(?:[^"\\\\\\n]|\\\\.)*"?|'(?:[^'\\\\\\n]|\\\\.)*'?|\`(?:[^\`\\\\]|\\\\[\\s\\S])*\`?)`);
  parts.push(`(?<number>\\b-?(?:0x[0-9a-fA-F]+|\\d+(?:\\.\\d+)?(?:[eE][-+]?\\d+)?)\\b)`);
  parts.push(`(?<word>[A-Za-z_$][\\w$]*)`);
  re = new RegExp(parts.join("|"), "gm");
  patterns.set(lang, re);
  return re;
}

/** The text as HTML with colored spans (escaped; safe to set as innerHTML). Plain (escaped) when it isn't code. */
export function highlight(text: string, lang: Lang | null): string {
  if (!lang) return esc(text);
  const kw = new Set(lang.nocase ? lang.kw.map((k) => k.toLowerCase()) : lang.kw);
  const keyed = lang.name === "JSON" || lang.name === "YAML";
  let out = "", last = 0;
  for (const m of text.matchAll(pattern(lang))) {
    const g = m.groups!, s = m[0], at = m.index!;
    if (at > last) out += esc(text.slice(last, at));
    let cls = "";
    if (g.comment || g.lcomment) cls = "comment";
    else if (g.tag) cls = "tag";
    else if (g.string3) cls = "string";
    else if (g.string) cls = keyed && /^\s*:/.test(text.slice(at + s.length, at + s.length + 40)) ? "key" : "string";
    else if (g.number) cls = "number";
    else if (g.word && kw.has(lang.nocase ? s.toLowerCase() : s)) cls = "keyword";
    out += cls ? `<span class="tok-${cls}">${esc(s)}</span>` : esc(s);
    last = at + s.length;
  }
  return out + esc(text.slice(last));
}

/** JSON written on one line (or a few very long ones): worth laying out for reading. */
export const isMinifiedJson = (text: string, lang: Lang | null) =>
  lang?.name === "JSON" && text.split("\n", 5).some((l) => l.length > 200);

/** JSON laid out in lines (2 spaces), as JSON.stringify does; for JSON that doesn't parse (cut off, a stray comma…)
 * the same layout made from its characters, so it's still readable. */
export function formatJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch { /* laid out by hand below */ }
  const out: string[] = [];
  let line = "", depth = 0;
  const nl = (d: number) => { out.push(line.trimEnd()); line = "  ".repeat(Math.max(0, d)); };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      let j = i + 1;
      while (j < text.length && text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
      line += text.slice(i, j + 1);
      i = j;
    } else if (c === "{" || c === "[") {
      const close = c === "{" ? "}" : "]";
      let k = i + 1;
      while (k < text.length && /\s/.test(text[k])) k++;
      if (text[k] === close) { line += c + close; i = k; continue; }
      depth++; line += c; nl(depth);
    } else if (c === "}" || c === "]") { depth--; nl(depth); line += c; }
    else if (c === ",") { line += c; nl(depth); }
    else if (c === ":") line += ": ";
    else if (!/\s/.test(c)) line += c;
  }
  if (line.trim()) out.push(line.trimEnd());
  return out.join("\n");
}

/** Line numbers for a text, one per line. */
export const lineNumbers = (text: string) => {
  let n = 1;
  for (let i = text.indexOf("\n"); i !== -1; i = text.indexOf("\n", i + 1)) n++;
  return Array.from({ length: n }, (_, i) => i + 1).join("\n");
};

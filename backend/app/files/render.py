"""Covers of files, drawn in a process of their own: the worker runs `python -m app.files.render` for each one with a time
limit (previews.render), so a file that takes long to draw (a huge picture, a minified file of one endless line) is
stopped there and never holds up the web app or the worker's other jobs (one Python process shares one CPU between its
threads). Only Pillow and the standard library: no database, no storage, quick to start.

    python -m app.files.render '{"what": "text", "src": "/tmp/x", "out": "/data/attachments/thumbs/<id>.webp", "name": "a.json"}'

prints {"thumb": true|false, "text": "…"|null} (text: what goes in the Text field, for search).

Text files get their first lines on a page; code files (.py, .json, .js…) look like they do in an editor: line numbers,
colors, and JSON written on one line laid out in lines first. Each line is cut at the page's edge, never measured whole.
"""
import io
import json
import os
import re
import sys
import zipfile

THUMB_PX = 480
TEXT_CHARS = 20_000           # how much of a text file goes into the Text field (for search)
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
PAGE_LINES = 80               # more never fit on a cover

# ---------------------------------------------------------------- code: languages and colors (keep in step with
# frontend/src/code.ts, which colors the same way in the viewer)

_C_KW = ("if else for while do switch case break continue return function var let const class extends new this super import "
         "export from default try catch finally throw typeof instanceof in of void delete yield async await static public private "
         "protected interface enum type implements package namespace using struct union typedef sizeof goto int long short char "
         "float double bool boolean void unsigned signed auto fn impl trait mut pub use mod match where loop move ref self Self "
         "func go defer chan map range select fallthrough val fun object when is as override open data sealed lateinit "
         "echo foreach endif elseif endforeach endwhile include require_once require extern inline virtual template typename "
         "true false null nil undefined NaN None").split()
_PY_KW = ("def class if elif else for while try except finally with as return yield import from pass break continue raise "
          "lambda global nonlocal assert del in is not and or async await match case True False None self").split()
_SH_KW = ("if then else elif fi for while until do done case esac function return in local export readonly unset shift exit "
          "echo set source alias cd true false").split()
_RB_KW = "def end class module if elsif else unless while until for do return yield begin rescue ensure raise require self nil true false and or not then".split()
_SQL_KW = ("select from where insert into values update set delete create table drop alter add column index view join left right "
           "inner outer full on group by order having limit offset union all distinct as and or not null is in like between case "
           "when then else end primary key foreign references default unique begin commit rollback with returning exists true false").split()
_LUA_KW = "function end local if then else elseif for while do repeat until return break in and or not nil true false".split()
_YAML_KW = "true false null yes no on off".split()

# extension -> (line comment, block comment (start, end), keywords, case-insensitive keywords)
_LANGS: dict[str, tuple] = {}
for _exts, _spec in (
    ("js mjs cjs ts tsx jsx java kt kts c h cpp hpp cc cs go rs php swift dart scala groovy vue svelte css scss less",
     ("//", ("/*", "*/"), _C_KW, False)),
    ("json", (None, None, ["true", "false", "null"], False)),
    ("py pyw", ("#", None, _PY_KW, False)),
    ("sh bash zsh env gitignore dockerfile makefile conf cfg ini toml ps1 pl r", ("#", None, _SH_KW, False)),
    ("rb", ("#", None, _RB_KW, False)),
    ("yaml yml", ("#", None, _YAML_KW, False)),
    ("sql", ("--", ("/*", "*/"), _SQL_KW, True)),
    ("lua", ("--", None, _LUA_KW, False)),
    ("xml html htm svg", (None, ("<!--", "-->"), [], False)),
    ("bat cmd", ("REM ", None, "echo set if else goto call exit for in do not exist".split(), True)),
):
    for _e in _exts.split():
        _LANGS[_e] = _spec

# colors on the cover (a light page, like GitHub's)
_COLORS = {"comment": (106, 115, 125), "string": (3, 47, 98), "key": (0, 92, 197), "number": (0, 92, 197),
           "keyword": (215, 58, 73), "tag": (34, 134, 58), "plain": (36, 41, 46)}


def ext_of(name: str) -> str:
    base = name.rsplit("/", 1)[-1].lower()
    return base.rsplit(".", 1)[-1] if "." in base else base


def is_code(name: str) -> bool:
    return ext_of(name) in _LANGS


def _token_re(ext: str):
    line, block, _, _ = _LANGS[ext]
    parts = []
    if block:
        parts.append(rf"(?P<comment>{re.escape(block[0])}.*?(?:{re.escape(block[1])}|$))")
    if line:
        parts.append(rf"(?P<lcomment>{re.escape(line)}.*$)")
    if ext in ("xml", "html", "htm", "svg"):
        parts.append(r"(?P<tag></?[A-Za-z][\w:.-]*|/?>)")
    parts.append(r"(?P<string>\"(?:[^\"\\]|\\.)*\"?|'(?:[^'\\]|\\.)*'?|`(?:[^`\\]|\\.)*`?)")
    parts.append(r"(?P<number>\b-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\b)")
    parts.append(r"(?P<word>[A-Za-z_$][\w$]*)")
    return re.compile("|".join(parts))


def tokens(line: str, ext: str) -> list[tuple[str, str]]:
    """A line of code as (kind, text) pieces: comment, string, key (a JSON / YAML key), number, keyword, tag or plain.
    (Per line: a block comment that spans lines is colored on its first line only, which is plenty for a cover.)"""
    _, _, kws, nocase = _LANGS.get(ext, (None, None, [], False))
    kw = {k.lower() for k in kws} if nocase else set(kws)
    out, last = [], 0
    for m in _token_re(ext).finditer(line):
        if m.start() > last:
            out.append(("plain", line[last:m.start()]))
        g, text = m.lastgroup, m.group()
        if g == "lcomment":
            g = "comment"
        elif g == "word":
            g = "keyword" if (text.lower() if nocase else text) in kw else "plain"
        elif g == "string" and ext in ("json", "yaml", "yml") and line[m.end():].lstrip().startswith(":"):
            g = "key"
        out.append((g, text))
        last = m.end()
    if last < len(line):
        out.append(("plain", line[last:]))
    return out


def indent_json(text: str, max_lines: int = PAGE_LINES) -> str:
    """JSON laid out in indented lines, like JSON.stringify(…, null, 2). Works on the start of a file too (only that is
    read for a cover) and on JSON that isn't quite valid; stops once max_lines lines are made."""
    out, line, depth, i, n = [], [], 0, 0, len(text)
    def newline(d):
        out.append("".join(line).rstrip())
        line.clear()
        line.append("  " * max(0, d))
    while i < n and len(out) < max_lines:
        c = text[i]
        if c == '"':                                  # a string: copied whole
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            line.append(text[i:j + 1])
            i = j + 1
            continue
        if c in "{[":
            close = "}" if c == "{" else "]"
            k = i + 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            if k < n and text[k] == close:            # {} and [] stay as they are
                line.append(c + close)
                i = k + 1
                continue
            depth += 1
            line.append(c)
            newline(depth)
        elif c in "}]":
            depth -= 1
            newline(depth)
            line.append(c)
        elif c == ",":
            line.append(c)
            newline(depth)
        elif c == ":":
            line.append(": ")
        elif c not in " \t\r\n":
            line.append(c)
        i += 1
    if line and len(out) < max_lines:
        out.append("".join(line).rstrip())
    return "\n".join(out)


def readable(text: str, ext: str) -> str:
    """JSON written on one line (or a few very long ones) laid out in indented lines; anything else as it is."""
    if ext == "json" and any(len(x) > 200 for x in text.splitlines()[:5]):
        return indent_json(text)
    return text


# ---------------------------------------------------------------- drawing

def _font(size: int, bold=False, mono=False):
    from PIL import ImageFont
    name = "DejaVuSansMono" if mono else "DejaVuSans"
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}{'-Bold' if bold else ''}.ttf", size)
    except OSError:
        return ImageFont.load_default()


def save_thumb(img, out: str):
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img)
    img.thumbnail((THUMB_PX, THUMB_PX))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = os.path.join(os.path.dirname(out), "." + os.path.basename(out))
    img.save(tmp, "WEBP", quality=78)
    os.replace(tmp, out)


def _fit(d, line: str, font, width: int) -> int:
    """How many characters of line fit in width: by bisection, never trying more characters than the width has pixels
    (a minified file is one line of thousands of characters)."""
    lo, hi = 1, min(len(line), width)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if d.textlength(line[:mid], font=font) <= width:
            lo = mid
        else:
            hi = mid - 1
    return lo


def code_thumb(text: str, name: str, out: str) -> bool:
    """A code file's cover: its first lines as in an editor (line numbers, colors), each cut at the page's edge."""
    from PIL import Image, ImageDraw
    ext = ext_of(name)
    w, h, pad, size = THUMB_PX, int(THUMB_PX * 1.3), 18, 12
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    f = _font(size, mono=True)
    cw = d.textlength("M", font=f)           # a monospace font: every character this wide
    lines = readable(text, ext).replace("\t", "    ").splitlines()[:PAGE_LINES]
    gutter = pad + cw * max(2, len(str(len(lines)))) + 10
    cols = max(1, int((w - gutter - pad) // cw))
    d.rectangle((0, 0, gutter - 6, h), fill=(246, 248, 250))
    y, step = pad, int(size * 1.5)
    for n, line in enumerate(lines, 1):
        if y > h - pad:
            break
        d.text((gutter - 10 - d.textlength(str(n), font=f), y), str(n), font=f, fill=(175, 184, 193))
        x, left = gutter, cols
        for kind, piece in tokens(line[:cols], ext):
            piece = piece[:left]
            d.text((x, y), piece, font=f, fill=_COLORS.get(kind, _COLORS["plain"]))
            x += cw * len(piece)
            left -= len(piece)
            if left <= 0:
                break
        y += step
    save_thumb(img, out)
    return True


def text_thumb(text: str, name: str, out: str) -> bool:
    """A text file's cover: its first lines on a page (Markdown headings larger), like a document preview."""
    from PIL import Image, ImageDraw
    w, h, pad = THUMB_PX, int(THUMB_PX * 1.3), 28
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    md = ext_of(name) in ("md", "markdown")
    mono = not md and ext_of(name) not in ("txt", "log", "rst")
    y = pad
    for raw in text.replace("\t", "    ").splitlines()[:PAGE_LINES]:
        line = raw.rstrip()
        size, bold, color = (15, False, (32, 33, 36)) if not mono else (13, False, (55, 65, 81))
        if md and line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            line = line.lstrip("#").strip()
            size, bold, color = {1: (26, True, (17, 24, 39)), 2: (21, True, (17, 24, 39))}.get(level, (17, True, (31, 41, 55)))
        elif md:
            line = re.sub(r"[*_`]", "", line)
            if line.lstrip().startswith(("- ", "* ", "+ ")):
                line = "•  " + line.lstrip()[2:]
        f = _font(size, bold, mono)
        while True:  # wrap long lines to the page width
            cut = _fit(d, line, f, w - 2 * pad)
            d.text((pad, y), line[:cut], font=f, fill=color)
            y += int(size * 1.45)
            line = line[cut:]
            if not line or y > h - pad:
                break
        if y > h - pad:
            break
    save_thumb(img, out)
    return True


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def render(job: dict) -> dict:
    """Draw one cover. job: what (image | zip | text), src (the file's bytes, or its start for text), out, name, svg."""
    from PIL import Image
    what, src, out, name = job["what"], job["src"], job["out"], job.get("name") or ""
    thumb, text = False, None
    if what == "image":
        with Image.open(src) as img:
            img.seek(0)
            img.draft("RGB", (THUMB_PX * 2, THUMB_PX * 2))   # JPEGs: decoded at a smaller size, much faster
            save_thumb(img.copy(), out)
        thumb = True
    elif what == "zip":   # OpenDocument / Office files: the thumbnail saved inside them; .docx: its text too
        data = open(src, "rb").read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            inner = next((n for n in ("Thumbnails/thumbnail.png", "docProps/thumbnail.jpeg", "docProps/thumbnail.png") if n in z.namelist()), None)
            if inner:
                with Image.open(io.BytesIO(z.read(inner))) as img:
                    save_thumb(img.copy(), out)
                thumb = True
        if ext_of(name) == "docx":
            text = docx_text(data)[:TEXT_CHARS]
    elif what == "docx_text":
        text = docx_text(open(src, "rb").read())[:TEXT_CHARS]
    elif what == "text":
        whole = open(src, "rb").read().decode("utf-8", "replace")   # (the file's start: see previews.py)
        text = whole[:TEXT_CHARS]
        if text.strip() and "\x00" not in text[:2000] and not job.get("svg"):
            thumb = code_thumb(whole, name, out) if is_code(name) else text_thumb(text, name, out)
    return {"thumb": thumb, "text": text}


if __name__ == "__main__":
    print(json.dumps(render(json.loads(sys.argv[1]))))

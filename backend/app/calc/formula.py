"""Teable-style formula language.

    {Field name} references, numbers, "strings", TRUE/FALSE, operators
    + - * / %   & (concatenate)   = != <> < > <= >=   && ||   unary - and !
    and functions such as IF(), SUM(), DATETIME_DIFF(), ARRAY_JOIN() ...

Expressions are stored with {field_id} references (so renaming a field never breaks a formula)
and shown to the user with {Field name}; see rewrite_refs().
"""
import math
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from app.formats.ical import DEFAULT_TZ


class FormulaError(Exception):
    pass


# ======================================================================== tokenizer

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<field>\{(?:[^}\\]|\\.)*\})
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>&&|\|\||<=|>=|!=|<>|[-+*/%&=<>(),!])
""", re.VERBOSE)


def tokenize(src: str):
    pos, out = 0, []
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise FormulaError(f"Unexpected character '{src[pos]}' at position {pos + 1}")
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group(), m.start(), m.end()))
        pos = m.end()
    return out


def _unescape(s: str) -> str:
    # Only quotes, braces and backslashes are escapes, so regex patterns like "\d+" work as typed.
    return re.sub(r"\\([\"'}\\])", r"\1", s)


def rewrite_refs(src: str, mapper) -> str:
    """Rewrite every {ref} via mapper(ref) -> new ref (names <-> ids). Raises FormulaError from mapper."""
    out, last = [], 0
    for kind, text, start, end in tokenize(src):
        if kind == "field":
            out.append(src[last:start])
            new = mapper(_unescape(text[1:-1]))
            out.append("{" + new.replace("\\", "\\\\").replace("}", "\\}") + "}")
            last = end
    out.append(src[last:])
    return "".join(out)


# ======================================================================== parser
# AST: ("num", v) ("str", v) ("bool", v) ("field", ref) ("call", NAME, [args]) ("bin", op, l, r) ("un", op, x)

_LEVELS = [("||",), ("&&",), ("=", "!=", "<>", "<", ">", "<=", ">="), ("&",), ("+", "-"), ("*", "/", "%")]


def parse(src: str):
    if not src or not src.strip():
        raise FormulaError("The formula is empty")
    toks = tokenize(src)
    i = 0

    def peek():
        return toks[i] if i < len(toks) else (None, None, len(src), len(src))

    def take(expected=None):
        nonlocal i
        t = peek()
        if t[0] is None:
            raise FormulaError("The formula ends unexpectedly" + (f" (expected '{expected}')" if expected else ""))
        if expected and t[1] != expected:
            raise FormulaError(f"Expected '{expected}' but found '{t[1]}' at position {t[2] + 1}")
        i += 1
        return t

    def level(n):
        if n == len(_LEVELS):
            return unary()
        node = level(n + 1)
        while peek()[0] == "op" and peek()[1] in _LEVELS[n]:
            op = take()[1]
            node = ("bin", "!=" if op == "<>" else op, node, level(n + 1))
        return node

    def unary():
        t = peek()
        if t[0] == "op" and t[1] in ("-", "+", "!"):
            take()
            return ("un", t[1], unary())
        return primary()

    def primary():
        t = take()
        kind, text = t[0], t[1]
        if kind == "num":
            return ("num", float(text) if any(c in text for c in ".eE") else int(text))
        if kind == "str":
            return ("str", _unescape(text[1:-1]))
        if kind == "field":
            return ("field", _unescape(text[1:-1]))
        if kind == "ident":
            up = text.upper()
            if peek()[1] == "(":
                take("(")
                args = []
                if peek()[1] != ")":
                    args.append(level(0))
                    while peek()[1] == ",":
                        take(",")
                        args.append(level(0))
                take(")")
                if up not in FUNCTIONS:
                    raise FormulaError(f"Unknown function {text}()")
                lo, hi = FUNCTIONS[up][1]
                if len(args) < lo or (hi is not None and len(args) > hi):
                    want = f"{lo}" if lo == hi else f"{lo}–{hi}" if hi is not None else f"at least {lo}"
                    raise FormulaError(f"{up}() takes {want} argument{'s' if hi != 1 else ''}, got {len(args)}")
                return ("call", up, args)
            if up in ("TRUE", "FALSE"):
                return ("bool", up == "TRUE")
            raise FormulaError(f"Unknown name '{text}'. Field names go in braces, like {{{text}}}")
        if text == "(":
            node = level(0)
            take(")")
            return node
        raise FormulaError(f"Unexpected '{text}' at position {t[2] + 1}")

    node = level(0)
    if i < len(toks):
        raise FormulaError(f"Unexpected '{toks[i][1]}' at position {toks[i][2] + 1}")
    return node


def field_refs(ast) -> set:
    out = set()

    def walk(n):
        if n[0] == "field":
            out.add(n[1])
        elif n[0] == "call":
            for a in n[2]:
                walk(a)
        elif n[0] == "bin":
            walk(n[2]); walk(n[3])
        elif n[0] == "un":
            walk(n[2])
    walk(ast)
    return out


# ======================================================================== values & coercion

def is_blank(v):
    return v is None or v == "" or (isinstance(v, list) and len(v) == 0)


def flat(args):
    for a in args:
        if isinstance(a, list):
            yield from flat(a)
        else:
            yield a


def to_num(v):
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, list):
        nums = [to_num(x) for x in flat(v) if not is_blank(x)]
        if len(nums) == 1:
            return nums[0]
        raise FormulaError("Expected a single number but got a list")
    if isinstance(v, str):
        try:
            return float(v.replace(",", "")) if "." in v or "e" in v.lower() else int(v.replace(",", ""))
        except ValueError:
            raise FormulaError(f"'{v}' is not a number (use VALUE() to convert text)")
    raise FormulaError("Expected a number")


def fmt_num(n) -> str:
    if isinstance(n, float):
        if n.is_integer():
            return str(int(n))
        return f"{round(n, 10):.10f}".rstrip("0").rstrip(".")
    return str(n)


def to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return fmt_num(v)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, list):
        return ", ".join(to_str(x) for x in flat(v) if not is_blank(x))
    return str(v)


def to_bool(v) -> bool:
    if isinstance(v, list):
        return any(to_bool(x) for x in v)
    if isinstance(v, str):
        return v != "" and v.lower() != "false"
    return bool(v)


def parse_dt(v):
    """Value -> aware datetime (dates become midnight in DEFAULT_TZ), or None for blanks."""
    if is_blank(v):
        return None
    if isinstance(v, list):
        items = [x for x in flat(v) if not is_blank(x)]
        if not items:
            return None
        v = items[0]
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=DEFAULT_TZ)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=DEFAULT_TZ)
    if isinstance(v, str):
        s = v.strip()
        try:
            if len(s) == 10:
                d = date.fromisoformat(s)
                return datetime(d.year, d.month, d.day, tzinfo=DEFAULT_TZ)
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=DEFAULT_TZ)
        except ValueError:
            pass
    raise FormulaError(f"'{to_str(v)}' is not a date")


def local(dt: datetime) -> datetime:
    return dt.astimezone(DEFAULT_TZ)


# ======================================================================== date helpers

_UNITS = {
    "ms": "millisecond", "millisecond": "millisecond", "milliseconds": "millisecond",
    "s": "second", "sec": "second", "secs": "second", "second": "second", "seconds": "second",
    "m": "minute", "min": "minute", "mins": "minute", "minute": "minute", "minutes": "minute",
    "h": "hour", "hr": "hour", "hrs": "hour", "hour": "hour", "hours": "hour",
    "d": "day", "day": "day", "days": "day",
    "w": "week", "week": "week", "weeks": "week",
    "M": "month", "month": "month", "months": "month",
    "Q": "quarter", "quarter": "quarter", "quarters": "quarter",
    "y": "year", "year": "year", "years": "year",
}
_SECONDS = {"millisecond": 0.001, "second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def unit_of(u) -> str:
    u = to_str(u).strip() or "day"
    key = u if u in ("M", "m") else u.lower()
    if key not in _UNITS:
        raise FormulaError(f"Unknown time unit '{u}' (use day, week, month, year, hour, minute, second…)")
    return _UNITS[key]


def add_months(dt: datetime, n: int) -> datetime:
    lt = local(dt)
    m = lt.month - 1 + n
    y, m = lt.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return lt.replace(year=y, month=m, day=min(lt.day, last))


def is_date_only(v) -> bool:
    if isinstance(v, list):
        v = next((x for x in flat(v) if not is_blank(x)), None)
    return (isinstance(v, date) and not isinstance(v, datetime)) or (isinstance(v, str) and len(v.strip()) == 10)


def date_add(value, count, unit):
    dt, n, u = parse_dt(value), to_num(count), unit_of(unit)
    if dt is None:
        return None
    if u in ("month", "quarter", "year"):
        out = add_months(dt, int(n) * {"month": 1, "quarter": 3, "year": 12}[u])
    elif u in ("day", "week"):  # calendar days keep the wall-clock time across DST changes
        out = local(dt) + timedelta(days=n * (7 if u == "week" else 1))
    else:
        return dt + timedelta(seconds=n * _SECONDS[u])
    return out.date() if is_date_only(value) and float(n).is_integer() else out


def date_diff(a, b, unit="second"):
    """a - b in the given unit, exactly like Teable: fractional for second..week, whole months,
    quarters as months / 3, whole years."""
    a, b, u = parse_dt(a), parse_dt(b), unit_of(unit or "second")
    if a is None or b is None:
        return None
    if u in ("month", "quarter", "year"):
        la, lb = local(a), local(b)
        months = (la.year - lb.year) * 12 + (la.month - lb.month)
        if months > 0 and la.day < lb.day:
            months -= 1
        elif months < 0 and la.day > lb.day:
            months += 1
        return months if u == "month" else months / 3.0 if u == "quarter" else int(months / 12)
    secs = (a - b).total_seconds()
    if u == "day" and abs(secs) < 1:
        return 0
    return secs * 1000 if u == "millisecond" else secs / _SECONDS[u]


def same(a, b, unit):
    a, b, u = parse_dt(a), parse_dt(b), unit_of(unit or "day")
    if a is None or b is None:
        return False
    la, lb = local(a), local(b)
    keys = {
        "year": lambda d: (d.year,), "quarter": lambda d: (d.year, (d.month - 1) // 3),
        "month": lambda d: (d.year, d.month), "week": lambda d: d.isocalendar()[:2],
        "day": lambda d: d.date(), "hour": lambda d: (d.date(), d.hour),
        "minute": lambda d: (d.date(), d.hour, d.minute), "second": lambda d: (d.date(), d.hour, d.minute, d.second),
        "millisecond": lambda d: d,
    }
    return keys[u](la) == keys[u](lb)


_FMT_TOKENS = re.compile(r"\[([^\]]*)\]|YYYY|YY|MMMM|MMM|MM|M|DDDD|DD|Do|D|dddd|ddd|dd|d|HH|H|hh|h|mm|m|SSS|ss|s|A|a|ZZ|Z|X|x")


def _ordinal(d):
    return f"{d}{'th' if 11 <= d % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')}"


# token -> formatter(local datetime, aware datetime); only the tokens a format uses are computed.
_FMT = {
    "YYYY": lambda l, d: f"{l.year:04d}", "YY": lambda l, d: f"{l.year % 100:02d}",
    "MMMM": lambda l, d: l.strftime("%B"), "MMM": lambda l, d: l.strftime("%b"),
    "MM": lambda l, d: f"{l.month:02d}", "M": lambda l, d: str(l.month),
    "DDDD": lambda l, d: f"{l.timetuple().tm_yday:03d}", "DD": lambda l, d: f"{l.day:02d}", "D": lambda l, d: str(l.day),
    "Do": lambda l, d: _ordinal(l.day),
    "dddd": lambda l, d: l.strftime("%A"), "ddd": lambda l, d: l.strftime("%a"), "dd": lambda l, d: l.strftime("%a")[:2],
    "d": lambda l, d: str((l.weekday() + 1) % 7),
    "HH": lambda l, d: f"{l.hour:02d}", "H": lambda l, d: str(l.hour),
    "hh": lambda l, d: f"{l.hour % 12 or 12:02d}", "h": lambda l, d: str(l.hour % 12 or 12),
    "mm": lambda l, d: f"{l.minute:02d}", "m": lambda l, d: str(l.minute),
    "ss": lambda l, d: f"{l.second:02d}", "s": lambda l, d: str(l.second), "SSS": lambda l, d: f"{l.microsecond // 1000:03d}",
    "A": lambda l, d: "AM" if l.hour < 12 else "PM", "a": lambda l, d: "am" if l.hour < 12 else "pm",
    "Z": lambda l, d: (lambda o: f"{o[:3]}:{o[3:]}")(l.strftime("%z")), "ZZ": lambda l, d: l.strftime("%z"),
    "X": lambda l, d: str(int(d.timestamp())), "x": lambda l, d: str(int(d.timestamp() * 1000)),
}


def datetime_format(dt, fmt):
    dt = parse_dt(dt)
    if dt is None:
        return None
    lt = local(dt)
    return _FMT_TOKENS.sub(lambda m: m.group(1) if m.group(1) is not None else _FMT[m.group()](lt, dt),
                           to_str(fmt) or "YYYY-MM-DD HH:mm")


_PARSE_TOKENS = {"YYYY": "%Y", "YY": "%y", "MMMM": "%B", "MMM": "%b", "MM": "%m", "M": "%m", "DD": "%d", "D": "%d",
                 "HH": "%H", "H": "%H", "hh": "%I", "h": "%I", "mm": "%M", "m": "%M", "ss": "%S", "s": "%S", "A": "%p", "a": "%p"}


def datetime_parse(text, fmt=None):
    s = to_str(text).strip()
    if not s:
        return None
    if fmt is None or to_str(fmt) == "":
        return parse_dt(s)
    pattern = re.sub(r"YYYY|YY|MMMM|MMM|MM|M|DD|D|HH|H|hh|h|mm|m|ss|s|A|a", lambda m: _PARSE_TOKENS[m.group()], to_str(fmt))
    try:
        return datetime.strptime(s, pattern).replace(tzinfo=DEFAULT_TZ)
    except ValueError:
        raise FormulaError(f"'{s}' doesn't match the format '{to_str(fmt)}'")


def workday(start, days):
    dt, n = parse_dt(start), int(to_num(days))
    if dt is None:
        return None
    d, step = local(dt), 1 if n >= 0 else -1
    while n:
        d += timedelta(days=step)
        if d.weekday() < 5:
            n -= step
    return d.date() if is_date_only(start) else d


def workday_diff(a, b):
    a, b = parse_dt(a), parse_dt(b)
    if a is None or b is None:
        return None
    da, db = local(a).date(), local(b).date()
    sign = 1 if db >= da else -1
    lo, hi = min(da, db), max(da, db)
    full_weeks, rest = divmod((hi - lo).days + 1, 7)
    count = full_weeks * 5 + sum(1 for i in range(rest) if (lo + timedelta(days=full_weeks * 7 + i)).weekday() < 5)
    return sign * count


# ======================================================================== functions
# name -> (impl, (min_args, max_args or None), return type). Lazy functions get unevaluated thunks.

def _round(v, p=0, mode="half"):
    v, p = to_num(v), int(to_num(p))
    f = 10 ** p
    if mode == "up":
        return math.copysign(math.ceil(abs(v) * f - 1e-9) / f, v)
    if mode == "down":
        return math.copysign(math.floor(abs(v) * f + 1e-9) / f, v)
    return math.copysign(math.floor(abs(v) * f + 0.5) / f, v)


def _avg(*a):
    nums = [to_num(x) for x in flat(a) if not is_blank(x)]
    return sum(nums) / len(nums) if nums else 0


def _div_safe_mod(a, b):
    a, b = to_num(a), to_num(b)
    if b == 0:
        raise FormulaError("Division by zero")
    return math.fmod(a, b)


def _find(needle, hay, start=1, ci=False):
    n, h = to_str(needle), to_str(hay)
    if ci:
        n, h = n.lower(), h.lower()
    return h.find(n, max(int(to_num(start)) - 1, 0)) + 1


def _search(needle, hay, start=1):
    r = _find(needle, hay, start, ci=True)
    return r or None


def _substitute(text, old, new, index=None):
    t, o, n = to_str(text), to_str(old), to_str(new)
    if not o:
        return t
    if index is None:
        return t.replace(o, n)
    k, pos = int(to_num(index)), -1
    for _ in range(k):
        pos = t.find(o, pos + 1)
        if pos < 0:
            return t
    return t[:pos] + n + t[pos + len(o):]


def _regexp_replace(text, pattern, repl):
    try:
        return re.sub(to_str(pattern), re.sub(r"\$(\d)", r"\\\1", to_str(repl)), to_str(text))
    except re.error as e:
        raise FormulaError(f"Invalid regular expression: {e}")


def _switch(expr, *rest):
    v = expr()
    pairs, default = rest, None
    if len(rest) % 2 == 1:
        pairs, default = rest[:-1], rest[-1]
    for i in range(0, len(pairs), 2):
        if _eq(v, pairs[i]()):
            return pairs[i + 1]()
    return default() if default else None


def _is_error(thunk):
    try:
        v = thunk()
        return isinstance(v, dict) and "__error" in v
    except FormulaError:
        return True


def _error(msg="#ERROR"):
    raise FormulaError(to_str(msg))


def _unique(*a):
    out = []
    for x in flat(a):
        if x not in out:
            out.append(x)
    return out


def _weeknum(d, start="Sunday"):
    dt = parse_dt(d)
    if dt is None:
        return None
    lt = local(dt)
    jan1 = lt.replace(month=1, day=1)
    monday_start = to_str(start).lower().startswith("mon")
    offset = (jan1.weekday() if monday_start else (jan1.weekday() + 1) % 7)
    return (lt.timetuple().tm_yday + offset - 1) // 7 + 1


def _part(attr):
    def f(d):
        dt = parse_dt(d)
        return None if dt is None else getattr(local(dt), attr)
    return f


FUNCTIONS = {
    # numeric
    "SUM": (lambda *a: sum(to_num(x) for x in flat(a) if not is_blank(x)), (1, None), "number"),
    "AVERAGE": (_avg, (1, None), "number"),
    "MAX": (lambda *a: max((to_num(x) for x in flat(a) if not is_blank(x)), default=0), (1, None), "number"),
    "MIN": (lambda *a: min((to_num(x) for x in flat(a) if not is_blank(x)), default=0), (1, None), "number"),
    "ROUND": (lambda v, p=0: _round(v, p), (1, 2), "number"),
    "ROUNDUP": (lambda v, p=0: _round(v, p, "up"), (1, 2), "number"),
    "ROUNDDOWN": (lambda v, p=0: _round(v, p, "down"), (1, 2), "number"),
    "CEILING": (lambda v, s=1: math.ceil(to_num(v) / (to_num(s) or 1)) * (to_num(s) or 1), (1, 2), "number"),
    "FLOOR": (lambda v, s=1: math.floor(to_num(v) / (to_num(s) or 1)) * (to_num(s) or 1), (1, 2), "number"),
    "EVEN": (lambda v: (lambda n: math.copysign(math.ceil(abs(n) / 2) * 2, n))(to_num(v)), (1, 1), "number"),
    "ODD": (lambda v: (lambda n: math.copysign(math.ceil((abs(n) - 1) / 2) * 2 + 1, n))(to_num(v)), (1, 1), "number"),
    "INT": (lambda v: math.floor(to_num(v)), (1, 1), "number"),
    "ABS": (lambda v: abs(to_num(v)), (1, 1), "number"),
    "SQRT": (lambda v: math.sqrt(to_num(v)) if to_num(v) >= 0 else _error("SQRT of a negative number"), (1, 1), "number"),
    "POWER": (lambda b, e: to_num(b) ** to_num(e), (2, 2), "number"),
    "EXP": (lambda v: math.exp(to_num(v)), (1, 1), "number"),
    "LOG": (lambda v, base=10: math.log(to_num(v), to_num(base)) if to_num(v) > 0 else _error("LOG of a non-positive number"), (1, 2), "number"),
    "MOD": (_div_safe_mod, (2, 2), "number"),
    "VALUE": (lambda t: to_num(re.sub(r"[^0-9.eE+-]", "", to_str(t)) or 0), (1, 1), "number"),
    # text
    "CONCATENATE": (lambda *a: "".join(to_str(x) for x in a), (1, None), "text"),
    "FIND": (lambda n, h, s=1: _find(n, h, s), (2, 3), "number"),
    "SEARCH": (_search, (2, 3), "number"),
    "MID": (lambda t, s, c: to_str(t)[max(int(to_num(s)) - 1, 0):max(int(to_num(s)) - 1, 0) + int(to_num(c))], (3, 3), "text"),
    "LEFT": (lambda t, c=1: to_str(t)[:int(to_num(c))], (1, 2), "text"),
    "RIGHT": (lambda t, c=1: to_str(t)[-int(to_num(c)):] if int(to_num(c)) else "", (1, 2), "text"),
    "REPLACE": (lambda t, s, c, r: (lambda x, i: x[:i] + to_str(r) + x[i + int(to_num(c)):])(to_str(t), max(int(to_num(s)) - 1, 0)), (4, 4), "text"),
    "REGEXP_REPLACE": (_regexp_replace, (3, 3), "text"),
    "SUBSTITUTE": (_substitute, (3, 4), "text"),
    "LOWER": (lambda t: to_str(t).lower(), (1, 1), "text"),
    "UPPER": (lambda t: to_str(t).upper(), (1, 1), "text"),
    "REPT": (lambda t, n: to_str(t) * max(int(to_num(n)), 0), (2, 2), "text"),
    "TRIM": (lambda t: re.sub(r"\s+", " ", to_str(t)).strip(), (1, 1), "text"),
    "LEN": (lambda t: len(to_str(t)), (1, 1), "number"),
    "T": (lambda v: v if isinstance(v, str) else "", (1, 1), "text"),
    "ENCODE_URL_COMPONENT": (lambda t: urllib.parse.quote(to_str(t), safe="-_.!~*'()"), (1, 1), "text"),
    # logical (IF / SWITCH / AND / OR / IS_ERROR are lazy, see evaluate())
    "IF": (None, (2, 3), "any"),
    "SWITCH": (_switch, (3, None), "any"),
    "AND": (lambda *a: all(to_bool(x) for x in flat(a)), (1, None), "boolean"),
    "OR": (lambda *a: any(to_bool(x) for x in flat(a)), (1, None), "boolean"),
    "XOR": (lambda *a: sum(1 for x in flat(a) if to_bool(x)) % 2 == 1, (1, None), "boolean"),
    "NOT": (lambda v: not to_bool(v), (1, 1), "boolean"),
    "BLANK": (lambda: None, (0, 0), "any"),
    "ERROR": (_error, (0, 1), "any"),
    "IS_ERROR": (_is_error, (1, 1), "boolean"),
    # date & time
    "TODAY": (lambda: datetime.now(DEFAULT_TZ).date(), (0, 0), "date"),
    "NOW": (lambda: datetime.now(timezone.utc), (0, 0), "date"),
    "YEAR": (_part("year"), (1, 1), "number"),
    "MONTH": (_part("month"), (1, 1), "number"),
    "DAY": (_part("day"), (1, 1), "number"),
    "HOUR": (_part("hour"), (1, 1), "number"),
    "MINUTE": (_part("minute"), (1, 1), "number"),
    "SECOND": (_part("second"), (1, 1), "number"),
    "WEEKDAY": (lambda d, start="Sunday": None if parse_dt(d) is None else
                ((local(parse_dt(d)).weekday() + (0 if to_str(start).lower().startswith("mon") else 1)) % 7), (1, 2), "number"),
    "WEEKNUM": (_weeknum, (1, 2), "number"),
    "DATE_ADD": (date_add, (3, 3), "date"),
    "DATETIME_DIFF": (lambda a, b, u="second": date_diff(a, b, u), (2, 3), "number"),
    # Teable: FROMNOW and TONOW are the same function, now - date (positive for past dates).
    "FROMNOW": (lambda d, u="second": date_diff(datetime.now(timezone.utc), d, u), (1, 2), "number"),
    "TONOW": (lambda d, u="second": date_diff(datetime.now(timezone.utc), d, u), (1, 2), "number"),
    "WORKDAY": (workday, (2, 2), "date"),
    "WORKDAY_DIFF": (workday_diff, (2, 2), "number"),
    "IS_SAME": (lambda a, b, u="day": same(a, b, u), (2, 3), "boolean"),
    "IS_AFTER": (lambda a, b: (parse_dt(a) is not None and parse_dt(b) is not None and parse_dt(a) > parse_dt(b)), (2, 2), "boolean"),
    "IS_BEFORE": (lambda a, b: (parse_dt(a) is not None and parse_dt(b) is not None and parse_dt(a) < parse_dt(b)), (2, 2), "boolean"),
    "DATESTR": (lambda d: None if parse_dt(d) is None else local(parse_dt(d)).strftime("%Y-%m-%d"), (1, 1), "text"),
    "TIMESTR": (lambda d: None if parse_dt(d) is None else local(parse_dt(d)).strftime("%H:%M:%S"), (1, 1), "text"),
    "DATETIME_FORMAT": (lambda d, f="YYYY-MM-DD HH:mm": datetime_format(d, f), (1, 2), "text"),
    "DATETIME_PARSE": (datetime_parse, (1, 2), "date"),
    "CREATED_TIME": (None, (0, 0), "date"),
    "LAST_MODIFIED_TIME": (None, (0, 0), "date"),
    # arrays
    "COUNTALL": (lambda *a: len(list(flat(a))), (1, None), "number"),
    "COUNTA": (lambda *a: sum(1 for x in flat(a) if not is_blank(x)), (1, None), "number"),
    "COUNT": (lambda *a: sum(1 for x in flat(a) if isinstance(x, (int, float)) and not isinstance(x, bool)), (1, None), "number"),
    "ARRAY_JOIN": (lambda arr, sep=", ": to_str(sep).join(to_str(x) for x in flat([arr]) if not is_blank(x)), (1, 2), "text"),
    "ARRAY_UNIQUE": (_unique, (1, None), "array"),
    "ARRAY_FLATTEN": (lambda *a: list(flat(a)), (1, None), "array"),
    "ARRAY_COMPACT": (lambda *a: [x for x in flat(a) if not is_blank(x)], (1, None), "array"),
    # record
    "RECORD_ID": (None, (0, 0), "text"),
    "AUTO_NUMBER": (None, (0, 0), "number"),
}
LAZY = {"IF", "SWITCH", "IS_ERROR"}


# ======================================================================== evaluation

def _eq(a, b):
    if is_blank(a) and is_blank(b):
        return True
    if isinstance(a, (datetime, date)) or isinstance(b, (datetime, date)):
        try:
            return parse_dt(a) == parse_dt(b)
        except FormulaError:
            return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, (int, float)) and not isinstance(a, bool) or isinstance(b, (int, float)) and not isinstance(b, bool):
        try:
            return to_num(a) == to_num(b)
        except FormulaError:
            return False
    return to_str(a) == to_str(b)


def _cmp(a, b):
    if isinstance(a, (datetime, date)) or isinstance(b, (datetime, date)):
        a, b = parse_dt(a), parse_dt(b)
        if a is None or b is None:
            raise FormulaError("Can't compare an empty date")
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    x, y = to_num(a), to_num(b)
    return (x > y) - (x < y)


class Context:
    """What a formula can see of the current record."""

    def __init__(self, get_field, record_id=None, auto_number=None, created=None, modified=None):
        self.get_field = get_field
        self.record_id = record_id
        self.auto_number = auto_number
        self.created = created
        self.modified = modified


def evaluate(node, ctx: Context):
    kind = node[0]
    if kind in ("num", "str", "bool"):
        return node[1]
    if kind == "field":
        v = ctx.get_field(node[1])
        if isinstance(v, dict) and "__error" in v:
            raise FormulaError(v["__error"])
        return v
    if kind == "un":
        v = evaluate(node[2], ctx)
        if node[1] == "!":
            return not to_bool(v)
        return -to_num(v) if node[1] == "-" else to_num(v)
    if kind == "bin":
        op = node[1]
        if op == "&&":
            return to_bool(evaluate(node[2], ctx)) and to_bool(evaluate(node[3], ctx))
        if op == "||":
            return to_bool(evaluate(node[2], ctx)) or to_bool(evaluate(node[3], ctx))
        a, b = evaluate(node[2], ctx), evaluate(node[3], ctx)
        if op == "&":
            return to_str(a) + to_str(b)
        if op == "=":
            return _eq(a, b)
        if op == "!=":
            return not _eq(a, b)
        if op in ("<", ">", "<=", ">="):
            c = _cmp(a, b)
            return {"<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]
        if op == "+" and (isinstance(a, str) and not _numeric(a) or isinstance(b, str) and not _numeric(b)):
            return to_str(a) + to_str(b)
        x, y = to_num(a), to_num(b)
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            if y == 0:
                raise FormulaError("Division by zero")
            return x / y
        if op == "%":
            return _div_safe_mod(x, y)
    if kind == "call":
        name, args = node[1], node[2]
        if name == "IF":
            cond = to_bool(evaluate(args[0], ctx))
            return evaluate(args[1], ctx) if cond else (evaluate(args[2], ctx) if len(args) > 2 else None)
        if name in LAZY:
            return FUNCTIONS[name][0](*[(lambda a=a: evaluate(a, ctx)) for a in args])
        if name == "RECORD_ID":
            return ctx.record_id
        if name == "AUTO_NUMBER":
            return ctx.auto_number
        if name == "CREATED_TIME":
            return ctx.created
        if name == "LAST_MODIFIED_TIME":
            return ctx.modified
        vals = [evaluate(a, ctx) for a in args]
        try:
            return FUNCTIONS[name][0](*vals)
        except FormulaError:
            raise
        except (ValueError, OverflowError, TypeError, ZeroDivisionError) as e:
            raise FormulaError(f"{name}(): {e}")
    raise FormulaError("Invalid formula")


def _numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def to_json(v):
    """Formula result -> JSON-able record value."""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return {"__error": "Result is not a finite number"}
        r = round(v, 10)
        return int(r) if r.is_integer() else r
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, list):
        return [to_json(x) for x in v]
    return v


# ======================================================================== static result type

def infer(node, field_type) -> str:
    """Best-effort result type: number | text | boolean | date | array | any."""
    kind = node[0]
    if kind == "num":
        return "number"
    if kind == "str":
        return "text"
    if kind == "bool":
        return "boolean"
    if kind == "field":
        return field_type(node[1])
    if kind == "un":
        return "boolean" if node[1] == "!" else "number"
    if kind == "bin":
        op = node[1]
        if op in ("&&", "||", "=", "!=", "<", ">", "<=", ">="):
            return "boolean"
        if op == "&":
            return "text"
        if op == "+":
            l, r = infer(node[2], field_type), infer(node[3], field_type)
            return "text" if "text" in (l, r) else "number"
        return "number"
    if kind == "call":
        name, args = node[1], node[2]
        if name == "IF":
            types = [infer(a, field_type) for a in args[1:]]
            known = [t for t in types if t != "any"]
            return known[0] if known else "any"
        if name == "SWITCH":
            results = [infer(a, field_type) for i, a in enumerate(args[1:]) if i % 2 == 1 or i == len(args) - 2]
            known = [t for t in results if t != "any"]
            return known[0] if known else "any"
        if name in ("MAX", "MIN") and args and all(infer(a, field_type) == "date" for a in args):
            return "date"
        return FUNCTIONS[name][2]
    return "any"


# ======================================================================== rollups

ROLLUPS = {
    "countall": ("Count all", "number", lambda vals: len(vals)),
    "counta": ("Count values", "number", lambda vals: FUNCTIONS["COUNTA"][0](vals)),
    "count": ("Count numbers", "number", lambda vals: FUNCTIONS["COUNT"][0](vals)),
    "sum": ("Sum", "number", lambda vals: FUNCTIONS["SUM"][0](vals) if vals else 0),
    "average": ("Average", "number", lambda vals: _avg(vals) if vals else None),
    "max": ("Max", "number", lambda vals: max((to_num(v) for v in vals if not is_blank(v)), default=None)),
    "min": ("Min", "number", lambda vals: min((to_num(v) for v in vals if not is_blank(v)), default=None)),
    "and": ("All true (AND)", "boolean", lambda vals: all(to_bool(v) for v in vals) if vals else False),
    "or": ("Any true (OR)", "boolean", lambda vals: any(to_bool(v) for v in vals)),
    "xor": ("Odd number true (XOR)", "boolean", lambda vals: FUNCTIONS["XOR"][0](vals) if vals else False),
    "array_join": ("Join with commas", "text", lambda vals: ", ".join(to_str(v) for v in vals if not is_blank(v))),
    "concatenate": ("Concatenate", "text", lambda vals: "".join(to_str(v) for v in vals)),
    "array_unique": ("Unique values", "array", lambda vals: _unique(vals)),
    "array_compact": ("Non-empty values", "array", lambda vals: [v for v in vals if not is_blank(v)]),
}


def rollup(func: str, values: list, inner_type: str):
    _, _, fn = ROLLUPS[func]
    if func in ("max", "min") and inner_type == "date":
        dts = [parse_dt(v) for v in values if not is_blank(v)]
        return to_json((max if func == "max" else min)(dts)) if dts else None
    return to_json(fn(values))


def rollup_type(func: str, inner_type: str) -> str:
    t = ROLLUPS[func][1]
    if func in ("max", "min") and inner_type == "date":
        return "date"
    return t

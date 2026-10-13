"""Checks that components only use the ones below them (each line may import from the lines above it):

    python -m app.tools.layers        prints what breaks the rule; exits 1 if anything does

Programs (main, worker, admin, wiring, tools) compose everything and may import anything.
"""
import ast
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent

# component: what it may import (besides itself)
ALLOWED = {
    "core":       set(),
    "formats":    {"core"},
    "calc":       {"core", "formats"},
    "identity":   {"core"},
    "data":       {"core", "formats", "calc", "identity"},
    "blobs":      {"core"},
    "files":      {"core", "formats", "calc", "identity", "data", "blobs"},
    "dav":        {"core", "formats", "calc", "identity", "data", "files"},
    "sharing":    {"core", "formats", "calc", "identity", "data", "blobs", "files"},
    "office":     {"core", "formats", "calc", "identity", "data", "blobs", "files", "sharing"},
    "ops":        {"core", "blobs"},
    "adapters":   {"core", "formats", "calc", "identity", "data", "blobs", "files", "dav", "sharing", "extensions"},
    "extensions": {"core", "formats", "calc", "identity", "data", "blobs", "files", "adapters"},
    "automations": {"core", "formats", "calc", "identity", "data", "blobs", "files", "dav", "sharing", "adapters"},
    "web":        {"core", "formats", "calc", "identity", "data", "blobs", "files", "dav", "sharing", "office", "ops", "adapters",
                   "automations"},
}
PROGRAMS = {"main", "worker", "admin", "wiring", "tools", "__init__"}
# Allowed exceptions, with the reason
EXCEPTIONS = {
    ("core", "formats"): "old migrations convert data with the formats of their time",
    ("core", "extensions"): "migrate.py runs each extension's migrations",
}


def check() -> list[str]:
    problems = []
    for p in sorted(APP.rglob("*.py")):
        rel = p.relative_to(APP)
        comp = rel.parts[0].removesuffix(".py")
        if comp in PROGRAMS or "__pycache__" in rel.parts:
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            if not (isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "app"):
                continue
            mods = node.module.split(".")
            targets = [mods[1]] if len(mods) > 1 else [a.name for a in node.names]
            for t in targets:
                if t == comp or t in ALLOWED.get(comp, set()) or (comp, t) in EXCEPTIONS:
                    continue
                problems.append(f"{rel}:{node.lineno}: {comp} imports {t}")
    return problems


def main():
    problems = check()
    for line in problems:
        print(line)
    if problems:
        sys.exit(1)
    print(f"ok: {len(ALLOWED)} components keep to their layers")


if __name__ == "__main__":
    main()

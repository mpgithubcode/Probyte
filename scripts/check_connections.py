"""Find uses of a database connection after the `with connection() as conn:` block that opened it has ended (the
connection is back in the pool by then: a query on it leaves a transaction open that nothing closes).

    python3 scripts/check_connections.py backend/app
"""
import ast, sys, pathlib
def withs(body):
    for i, st in enumerate(body):
        yield i, st
for path in pathlib.Path(sys.argv[1]).rglob("*.py"):
    tree = ast.parse(path.read_text())
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        def scan(body):
            for i, st in enumerate(body):
                if isinstance(st, ast.With):
                    for item in st.items:
                        call = item.context_expr
                        if isinstance(call, ast.Call) and getattr(call.func, "id", getattr(call.func, "attr", "")) in ("connection",) and isinstance(item.optional_vars, ast.Name):
                            name = item.optional_vars.id
                            for later in body[i+1:]:
                                # rebinding by another with: stop
                                rebinds = any(isinstance(n, ast.With) and any(isinstance(it.optional_vars, ast.Name) and it.optional_vars.id == name for it in n.items) for n in ast.walk(later))
                                uses = [n for n in ast.walk(later) if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)]
                                if uses and not rebinds:
                                    print(f"{path}:{uses[0].lineno}: '{name}' used after its with-block (line {st.lineno}) in {fn.name}")
                                    break
                                if rebinds or any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store) for n in ast.walk(later)):
                                    break
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    sub = getattr(st, attr, None)
                    if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                        scan(sub)
                    elif isinstance(sub, list):
                        for h in sub:
                            if hasattr(h, "body"):
                                scan(h.body)
        scan(fn.body)

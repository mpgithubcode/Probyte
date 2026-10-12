"""Evaluate a Probyte formula against one record, without a server: the formula language on its own.

    echo '{"Price": 4.5, "Qty": 3}' | python -m app.tools.formula '{Price} * {Qty}'
    13.5

Fields are referred to by name, as in the app. Prints the result as JSON; exits 1 with the error on a bad formula.
"""
import json
import sys

from app.calc import formula as F


def evaluate(expression: str, record: dict):
    ast = F.parse(F.rewrite_refs(expression, lambda ref: ref))
    ctx = F.Context(lambda ref: record.get(ref))
    return F.to_json(F.evaluate(ast, ctx))


def main(argv):
    if len(argv) != 1:
        raise SystemExit(__doc__)
    record = json.loads(sys.stdin.read() or "{}")
    try:
        print(json.dumps(evaluate(argv[0], record), default=str))
    except F.FormulaError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])

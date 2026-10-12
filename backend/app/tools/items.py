"""Convert between a calendar or contacts item and the values Probyte keeps for it, without a server.

    python -m app.tools.items read tasks < reminder.ics           -> {"uid": ..., "values": {"summary": ..., "due": ...}}
    python -m app.tools.items write events < values.json         -> an iCalendar item
    python -m app.tools.items write contacts --over card.vcf < values.json   (keeps what card.vcf had that isn't in the values)

Kinds: tasks, events, mixed, contacts. Values are keyed by Probyte's system fields (summary, dtstart, due, status…).
"""
import json
import sys

from app.dav.items import build_body, parse_item


def main(argv):
    if len(argv) < 2 or argv[0] not in ("read", "write") or argv[1] not in ("tasks", "events", "mixed", "contacts"):
        raise SystemExit(__doc__)
    cmd, kind = argv[0], argv[1]
    text = sys.stdin.read()
    if cmd == "read":
        uid, vals = parse_item(kind, "item", text)
        if uid is None:
            print("error: not a " + kind + " item", file=sys.stderr)
            sys.exit(1)
        print(json.dumps({"uid": uid, "values": vals}, default=str, indent=2))
        return
    data = json.loads(text)
    over = open(argv[argv.index("--over") + 1]).read() if "--over" in argv else None
    vals = data.get("values", data)
    uid = data.get("uid") or (parse_item(kind, "item", over)[0] if over else None)
    if not uid:
        from app.formats import ical, vcard
        uid = vcard.new_uid() if kind == "contacts" else ical.new_uid()
    sys.stdout.write(build_body(kind, uid, vals, over))


if __name__ == "__main__":
    main(sys.argv[1:])

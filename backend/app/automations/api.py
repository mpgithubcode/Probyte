"""The routes of automations: a base's automations, their runs, and running one now (to try it)."""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.automations import engine
from app.core.db import connection, Jsonb
from app.data import access, store
from app.identity.auth import User

router = APIRouter()


class AutomationIn(BaseModel):
    name: str = ""
    enabled: bool = True
    table_id: str
    trigger: dict[str, Any]
    conditions: dict[str, Any] | None = None
    actions: list[dict[str, Any]]


class TestIn(BaseModel):
    record_id: str | None = None


def _own(conn, automation_id, user, need="admin"):
    a = conn.execute("SELECT * FROM automations WHERE id = %s", (automation_id,)).fetchone()
    if not a or not store.base_allowed(a["base_id"]):
        raise HTTPException(404, "Automation not found")
    access.check(conn, a["base_id"], user["id"], need, "Automation")
    return a


def _out(conn, a) -> dict:
    who = conn.execute("SELECT username FROM users WHERE id = %s", (a["created_by"],)).fetchone() if a["created_by"] else None
    return {**{k: a[k] for k in ("id", "base_id", "table_id", "name", "enabled", "trigger", "conditions", "actions", "next_run_at",
                                   "last_run_at", "last_ok", "last_message", "runs", "created_at")},
            "created_by": who["username"] if who else None}


@router.get("/api/bases/{base_id}/automations")
def list_automations(base_id: str, user=User):
    with connection() as conn:
        store.get_base(conn, base_id, user["id"])
        rows = conn.execute("SELECT * FROM automations WHERE base_id = %s ORDER BY created_at", (base_id,)).fetchall()
        return {"automations": [_out(conn, a) for a in rows], "email": engine.email_enabled()}


@router.post("/api/bases/{base_id}/automations")
def create_automation(base_id: str, body: AutomationIn, user=User):
    with connection() as conn:
        store.get_base(conn, base_id, user["id"], need="admin")
        v = engine.validate(conn, base_id, body.model_dump())
        a = conn.execute("INSERT INTO automations (base_id, table_id, created_by, name, enabled, trigger, conditions, actions, next_run_at) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                         (base_id, v["table_id"], user["id"], v["name"], v["enabled"], Jsonb(v["trigger"]), Jsonb(v["conditions"]),
                          Jsonb(v["actions"]), v["next_run_at"])).fetchone()
        engine.invalidate()
        return _out(conn, a)


@router.put("/api/automations/{automation_id}")
def update_automation(automation_id: str, body: AutomationIn, user=User):
    """Change an automation; it acts with the rights of whoever changed it last."""
    with connection() as conn:
        a = _own(conn, automation_id, user)
        v = engine.validate(conn, a["base_id"], body.model_dump())
        a = conn.execute("UPDATE automations SET table_id = %s, name = %s, enabled = %s, trigger = %s, conditions = %s, actions = %s, "
                         "next_run_at = %s, created_by = %s WHERE id = %s RETURNING *",
                         (v["table_id"], v["name"], v["enabled"], Jsonb(v["trigger"]), Jsonb(v["conditions"]), Jsonb(v["actions"]),
                          v["next_run_at"], user["id"], automation_id)).fetchone()
        engine.invalidate()
        return _out(conn, a)


@router.delete("/api/automations/{automation_id}")
def delete_automation(automation_id: str, user=User):
    with connection() as conn:
        _own(conn, automation_id, user)
        conn.execute("DELETE FROM automations WHERE id = %s", (automation_id,))
        engine.invalidate()
    return {"ok": True}


@router.get("/api/automations/{automation_id}/runs")
def automation_runs(automation_id: str, user=User):
    with connection() as conn:
        _own(conn, automation_id, user, need="viewer")
        return {"runs": conn.execute("SELECT at, record_id, ok, message, ms FROM automation_runs WHERE automation_id = %s "
                                     "ORDER BY at DESC LIMIT 50", (automation_id,)).fetchall()}


@router.post("/api/automations/{automation_id}/test")
def test_automation(automation_id: str, body: TestIn, user=User):
    """Run it now: for a record (its actions happen, whatever the trigger), or as its schedule would."""
    with connection() as conn:
        a = _own(conn, automation_id, user)
        rid = body.record_id
        if rid is None and (a["trigger"] or {}).get("type") != "schedule":
            row = conn.execute("SELECT id FROM records WHERE table_id = %s AND NOT deleted ORDER BY created_at DESC LIMIT 1",
                               (a["table_id"],)).fetchone()
            if not row:
                raise HTTPException(400, "The table has no records to try it on")
            rid = str(row["id"])
    return engine.run(automation_id, rid, "record.updated" if rid else None, test=True)

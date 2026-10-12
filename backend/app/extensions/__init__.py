"""Extensions: optional parts of Probyte with their own API, database tables and screens.

An extension is a package in this folder that defines EXTENSION (an Extension): its id, name, an APIRouter (served
under /api/x/<id>/) and a migrations folder (NNNN_name.sql / .py, applied with Probyte's own, as "<id>:NNNN_name").
Its screens are in frontend/src/extensions/<id>/ (loaded only when opened).

The server's admin switches extensions on and off (Settings → Extensions). Off, an extension's API answers 404 and
its screens aren't shown; its data stays.
"""
import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import connection
from ..routes.common import User

log = logging.getLogger("probyte.extensions")


@dataclass
class Extension:
    id: str
    name: str
    description: str
    router: APIRouter
    icon: str = "layers"
    migrations: Path | None = None
    default_on: bool = False
    extra: dict = field(default_factory=dict)


def discover() -> list[Extension]:
    out = []
    for m in pkgutil.iter_modules(__path__):
        if not m.ispkg:
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{m.name}")
        except Exception:  # a broken extension doesn't stop Probyte
            log.exception("extension %s didn't load", m.name)
            continue
        ext = getattr(mod, "EXTENSION", None)
        if isinstance(ext, Extension):
            out.append(ext)
    return sorted(out, key=lambda e: e.name.lower())


EXTENSIONS = discover()
BY_ID = {e.id: e for e in EXTENSIONS}


def is_on(conn, ext_id: str) -> bool:
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (f"ext.{ext_id}",)).fetchone()
    return (row["value"] == "1") if row else BY_ID[ext_id].default_on


def _require(ext_id: str):
    def check():
        with connection() as conn:
            if not is_on(conn, ext_id):
                raise HTTPException(404, "This extension is switched off (Settings → Extensions)")
    return check


def mount(app):
    for e in EXTENSIONS:
        app.include_router(e.router, prefix=f"/api/x/{e.id}", dependencies=[Depends(_require(e.id))])
    app.include_router(router)


def migration_dirs() -> list[tuple[str, Path]]:
    return [(e.id, e.migrations) for e in EXTENSIONS if e.migrations and e.migrations.is_dir()]


# ---------------------------------------------------------------- the list, and switching them (admin)

router = APIRouter()


@router.get("/api/extensions")
def list_extensions(user=User):
    with connection() as conn:
        return [{"id": e.id, "name": e.name, "description": e.description, "icon": e.icon, "enabled": is_on(conn, e.id)}
                for e in EXTENSIONS]


class Switch(BaseModel):
    enabled: bool


@router.put("/api/extensions/{ext_id}")
def switch(ext_id: str, body: Switch, user=User):
    if not user.get("is_admin") or user.get("api_key"):
        raise HTTPException(403, "Only the server's admin account can do this")
    if ext_id not in BY_ID:
        raise HTTPException(404, "No such extension")
    with connection() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                     (f"ext.{ext_id}", "1" if body.enabled else ""))
    return list_extensions(user)

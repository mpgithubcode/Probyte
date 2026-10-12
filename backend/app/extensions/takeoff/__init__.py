"""Blueprint takeoff: count, measure and list what's on a drawing set (PDF), for estimates and takeoff sheets.

The sheets are read (sheet numbers, titles, scales, legends, schedules) and items found on them (their symbols or
labels), always as suggestions that a person reviews; only what's confirmed is in the takeoff. Exports: CSV, the
drawing set marked up, or a Probyte table.
"""
from pathlib import Path

from app.extensions import Extension
from app.extensions.takeoff import mcp
from app.extensions.takeoff.api import router

EXTENSION = Extension(
    id="takeoff",
    name="Blueprint takeoff",
    description="Count symbols and measure runs and areas on PDF drawing sets, using their legends and scales, "
                "and turn them into a takeoff list (CSV, marked-up PDF or a Probyte table).",
    icon="ruler",
    router=router,
    migrations=Path(__file__).parent / "migrations",
    tools=mcp.TOOLS,
    call=mcp.call,
)

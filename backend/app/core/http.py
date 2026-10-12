"""Small HTTP helpers shared by every component with routes."""
import json
import uuid
from datetime import date, datetime

import orjson
from fastapi.responses import JSONResponse


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"Not JSON serialisable: {type(o).__name__}")


class FastJSON(JSONResponse):
    """orjson with UUID/datetime support: many times quicker than FastAPI's generic encoder for big tables."""

    def render(self, content) -> bytes:
        try:
            return orjson.dumps(content, default=_json_default, option=orjson.OPT_NON_STR_KEYS)
        except TypeError:  # e.g. integers beyond 64 bits
            return json.dumps(content, default=_json_default, ensure_ascii=False, separators=(",", ":")).encode()

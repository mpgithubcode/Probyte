"""Who is acting in this request or job, as context variables: set once at the edge (sign-in, API key, a public
link, a phone's login, a sync) and read wherever it matters, so the record store doesn't depend on identity.

    actor      the label record history shows ("mike", "API key “Zapier”", "iPhone (calendar sync)"), and the user
    api_bases  the bases an API key is limited to (None: all)
"""
from contextvars import ContextVar

_actor: ContextVar[tuple[str, str | None]] = ContextVar("probyte_actor", default=("Probyte", None))
api_bases: ContextVar[set | None] = ContextVar("api_bases", default=None)


def set_actor(label: str, user_id=None):
    _actor.set((label, str(user_id) if user_id else None))


def actor() -> tuple[str, str | None]:
    return _actor.get()

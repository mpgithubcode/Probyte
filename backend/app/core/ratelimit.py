"""Slowing down password guessing: after a few wrong passwords for one account (or from one address), further
attempts are refused for a while. Used by sign-in, the OAuth consent page, password changes and
password-protected public links. Kept in memory (one web process); a restart forgets it.
"""
import threading
import time

from fastapi import HTTPException, Request

WINDOW = 15 * 60       # seconds a failure counts for
PER_ACCOUNT = 5        # failures per account (or public link) in the window before it's locked
PER_ADDRESS = 20       # failures from one IP address in the window (many accounts)

_fails: dict[str, list[float]] = {}
_lock = threading.Lock()


def _recent(key: str, now: float) -> list[float]:
    hits = [t for t in _fails.get(key, []) if now - t < WINDOW]
    if hits:
        _fails[key] = hits
    else:
        _fails.pop(key, None)
    return hits


def address(request: Request | None) -> str:
    return (request.client.host if request and request.client else None) or "unknown"


def check(account: str, request: Request | None):
    """Raise 429 when this account or address has failed too often lately."""
    now = time.time()
    with _lock:
        for key, limit in ((f"a:{account}", PER_ACCOUNT), (f"ip:{address(request)}", PER_ADDRESS)):
            hits = _recent(key, now)
            if len(hits) >= limit:
                wait = int(WINDOW - (now - hits[-limit])) + 1
                raise HTTPException(429, f"Too many wrong passwords. Try again in {max(1, round(wait / 60))} minutes.",
                                    headers={"Retry-After": str(wait)})


def failed(account: str, request: Request | None):
    now = time.time()
    with _lock:
        for key in (f"a:{account}", f"ip:{address(request)}"):
            _fails.setdefault(key, []).append(now)
        if len(_fails) > 50000:  # don't grow without bound under a flood of made-up names
            for k in [k for k in _fails if not _recent(k, now)]:
                _fails.pop(k, None)


def succeeded(account: str):
    with _lock:
        _fails.pop(f"a:{account}", None)

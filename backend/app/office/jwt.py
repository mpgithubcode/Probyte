"""JWT (HS256), as the Document Server signs and checks what it sends and gets. Pure functions."""
import base64
import hashlib
import hmac
import json
import time


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(payload: dict, secret: str) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def verify(token: str, secret: str) -> dict:
    """The claims of a token signed with `secret`; ValueError when it isn't (or has expired)."""
    try:
        head, body, sig = token.split(".")
        good = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(good, sig):
            raise ValueError("bad signature")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if claims.get("exp") and claims["exp"] < time.time():
            raise ValueError("expired")
        return claims
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(str(e))

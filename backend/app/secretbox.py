"""Passwords Probyte has to be able to use again (calendar accounts, phone-sync device passwords, webhook signing
secrets) are stored encrypted, so a copy of the database (or a backup) doesn't give them away.

The key is outside the database: PROBYTE_SECRET_KEY, or else a key file (SECRET_KEY_FILE, created on first start
on its own volume). It is not in backups: keep it with your .env. Without it, stored account passwords can't be
read; they show as missing and have to be entered again.
"""
import base64
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("probyte.secretbox")
PREFIX = "enc1:"
KEY_FILE = Path(os.environ.get("SECRET_KEY_FILE", "/keys/probyte.key"))
_fernet: Fernet | None = None


def _key() -> bytes:
    env = os.environ.get("PROBYTE_SECRET_KEY", "").strip()
    if env:
        return env.encode()
    try:
        return KEY_FILE.read_bytes().strip()
    except FileNotFoundError:
        pass
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:  # O_EXCL: when the app and the worker start together, one of them writes it and both use that one
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        log.warning("created a new encryption key for stored passwords at %s; keep a copy with your .env", KEY_FILE)
        return key
    except FileExistsError:
        return KEY_FILE.read_bytes().strip()


def _box() -> Fernet:
    global _fernet
    if _fernet is None:
        key = _key()
        try:
            _fernet = Fernet(key)
        except ValueError:  # any passphrase: derive a proper key from it
            import hashlib
            _fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))
    return _fernet


def seal(value: str | None) -> str | None:
    """Encrypt for storing (empty stays empty; already sealed values are left as they are)."""
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _box().encrypt(value.encode()).decode()


def unseal(value: str | None) -> str | None:
    """The stored value in clear. Values from before encryption pass through; ones sealed with another key read
    as missing (the password has to be entered again)."""
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _box().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        log.warning("a stored password can't be decrypted with this server's key (SECRET_KEY_FILE / PROBYTE_SECRET_KEY)")
        return None

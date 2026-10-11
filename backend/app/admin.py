"""Admin tasks on the command line, for whoever runs the server.

    docker compose exec app python -m app.admin reset-password <username>

Login passwords are stored only as scrypt hashes, so a forgotten one can't be looked up; this sets a new random
password, prints it once, and signs that user out everywhere. Change it afterwards in Settings → Account.
"""
import secrets
import sys

from . import auth
from .db import connection, init_db


def reset_password(username: str) -> str:
    password = secrets.token_urlsafe(12)
    with connection() as conn:
        row = conn.execute("UPDATE users SET password_hash = %s WHERE username = %s RETURNING id",
                           (auth.hash_password(password), username.strip().lower())).fetchone()
        if not row:
            raise SystemExit(f"No user called {username!r}")
        conn.execute("DELETE FROM sessions WHERE user_id = %s", (row["id"],))
    return password


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[0] != "reset-password":
        raise SystemExit(__doc__)
    init_db()
    print(f"New password for {argv[1].strip().lower()}: {reset_password(argv[1])}")


if __name__ == "__main__":
    main(sys.argv[1:])

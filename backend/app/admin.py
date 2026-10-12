"""Admin tasks on the command line, for whoever runs the server.

    docker compose exec app python -m app.admin reset-password <username>
    docker compose exec app python -m app.admin create-user <username> [--admin]   (when sign-up is off)
    docker compose exec worker python -m app.admin backup
    docker compose exec worker python -m app.admin backups
    docker compose run --rm worker python -m app.admin restore <name>     (with the app and worker stopped)

Login passwords are stored only as scrypt hashes, so a forgotten one can't be looked up; this sets a new random
password, prints it once, and signs that user out everywhere. Change it afterwards in Settings → Account.
"""
import secrets
import sys

from fastapi import HTTPException

from app import wiring  # noqa: F401  (connects the components: before anything writes)
from app.core.db import connection, init_db
from app.identity import auth


def reset_password(username: str) -> str:
    password = secrets.token_urlsafe(12)
    with connection() as conn:
        row = conn.execute("UPDATE users SET password_hash = %s WHERE username = %s RETURNING id",
                           (auth.hash_password(password), username.strip().lower())).fetchone()
        if not row:
            raise SystemExit(f"No user called {username!r}")
        conn.execute("DELETE FROM sessions WHERE user_id = %s", (row["id"],))
    return password


def create_user(username: str, admin: bool, password: str | None = None) -> str:
    password = password or secrets.token_urlsafe(12)
    try:
        username = auth.validate_credentials(username, password)
    except HTTPException as e:
        raise SystemExit(e.detail)
    with connection() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = %s", (username,)).fetchone():
            raise SystemExit(f"{username} already exists")
        conn.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (%s,%s,%s) RETURNING id",
                         (username, auth.hash_password(password), admin)).fetchone()
    return password


def main(argv: list[str]) -> None:
    from app.ops import backup
    cmd = argv[0] if argv else ""
    if cmd == "reset-password" and len(argv) == 2:
        init_db()
        print(f"New password for {argv[1].strip().lower()}: {reset_password(argv[1])}")
    elif cmd == "create-user" and len(argv) in (2, 3, 4):
        init_db()
        pw = next((a.split("=", 1)[1] for a in argv if a.startswith("--password=")), None)
        password = create_user(argv[1], "--admin" in argv, pw)
        print(f"Created {argv[1].strip().lower()}{' (admin)' if '--admin' in argv else ''}: password {password}")
    elif cmd == "backup" and len(argv) == 1:
        init_db()
        b = backup.run_backup()
        print(f"Saved {b['name']} ({b['total'] / 1e6:.1f} MB) in {backup.BACKUP_DIR}")
    elif cmd == "backups" and len(argv) == 1:
        for b in backup.list_backups():
            print(f"{b['name']}  {b['total'] / 1e6:8.1f} MB  {b['counts']['records']} records")
    elif cmd == "restore" and len(argv) in (2, 3):
        if "--yes" not in argv:
            print(f"This replaces ALL current data with the backup {argv[1]}. Stop the app and worker first.")
            if input("Type RESTORE to continue: ").strip() != "RESTORE":
                raise SystemExit("Cancelled")
        backup.restore(argv[1])
        print("Restored. Start everything again: docker compose up -d")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

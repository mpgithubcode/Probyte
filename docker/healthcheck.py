"""Docker's health check: the web app answers /api/health (the database is reachable and jobs aren't stuck). A worker
container (python -m app.worker: no web server) checks the database itself."""
import sys
import urllib.request

try:
    cmd = open("/proc/1/cmdline", "rb").read().replace(b"\0", b" ").decode()
except OSError:
    cmd = ""
try:
    if "app.worker" in cmd:
        sys.path.insert(0, "/app")
        import psycopg
        from app.core.db import DATABASE_URL
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as c:
            c.execute("SELECT 1")
    else:
        urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=8)
except Exception as e:
    print(e)
    sys.exit(1)

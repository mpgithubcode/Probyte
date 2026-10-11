"""A tiny HTTP service: POST /convert?name=<file name> with the file as the body -> the file as PDF.

LibreOffice runs one conversion at a time (one profile); a conversion that takes longer than TIMEOUT is stopped.
Macros never run (LibreOffice's default for files opened this way)."""
import os
import signal
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TIMEOUT = int(os.environ.get("CONVERT_TIMEOUT", "120"))
MAX_BYTES = int(os.environ.get("CONVERT_MAX_MB", "100")) * 1024 * 1024
PROFILE = "file:///tmp/lo-profile"
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, b"ok") if self.path == "/health" else self._send(404, b"not found")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/convert":
            return self._send(404, b"not found")
        name = os.path.basename((parse_qs(u.query).get("name") or ["file"])[0]) or "file"
        size = int(self.headers.get("Content-Length") or 0)
        if not size or size > MAX_BYTES:
            return self._send(413, b"file too large or empty")
        data = self.rfile.read(size)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in" + (os.path.splitext(name)[1].lower()[:10] or ".bin"))
            with open(src, "wb") as f:
                f.write(data)
            with lock:
                p = subprocess.Popen(["soffice", f"-env:UserInstallation={PROFILE}", "--headless", "--norestore", "--nologo",
                                      "--convert-to", "pdf", "--outdir", d, src],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                try:
                    out_, err_ = p.communicate(timeout=TIMEOUT)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)  # soffice and the soffice.bin it starts
                    p.communicate()
                    return self._send(504, b"conversion took too long")
                r = subprocess.CompletedProcess(p.args, p.returncode, out_, err_)
            out = os.path.join(d, "in.pdf")
            if r.returncode or not os.path.exists(out):
                return self._send(422, b"could not convert: " + (r.stderr or r.stdout)[-300:])
            with open(out, "rb") as f:
                self._send(200, f.read(), "application/pdf")

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 3000), Handler).serve_forever()

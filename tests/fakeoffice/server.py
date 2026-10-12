"""A stand-in for an ONLYOFFICE Document Server, for the tests (stdlib only). It speaks the parts of the Docs API
Probyte uses, with JWT signing (secret FAKE_SECRET):

- /healthcheck, the command service (version, forcesave), and the editor script (api.js)
- the "editor" is a textarea: it downloads the file from document.url, sends status 1 when someone opens it,
  status 6 (forcesave) on request, and status 2 / 4 when the last person closes it. Saved versions are the
  textarea's text, at /cache/<key>.
- the conversion service (old formats → new): the "converted" file is the same text, kept at /cache/<key>/0.
- /fake/log: every callback sent and Probyte's answer, for the tests to check.
"""
import base64
import hashlib
import hmac
import json
import os
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ.get("FAKE_SECRET", "")
docs = {}      # key -> {"text", "saved_text", "users": {id: callbackUrl}, "callback", "changed_by": set}
failed_keys = set()   # like the real one: a key whose download failed keeps failing
fail_next = [0]
finished = {}  # closed documents (their last version can still be downloaded)
log = []
lock = threading.Lock()


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(payload):
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(payload).encode())
    return f"{head}.{body}.{b64(hmac.new(SECRET.encode(), f'{head}.{body}'.encode(), hashlib.sha256).digest())}"


def verify(token):
    head, body, sig = token.split(".")
    if not hmac.compare_digest(sig, b64(hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())):
        raise ValueError("bad signature")
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def callback(doc, key, status, users, url=None):
    body = {"key": key, "status": status, "users": sorted(users)}
    if url:
        body["url"] = url
        body["filetype"] = doc["filetype"]
    if SECRET:
        body["token"] = sign(dict(body))
    req = urllib.request.Request(doc["callback"], data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            answer = json.loads(r.read() or b"{}")
            code = r.status
    except urllib.error.HTTPError as e:
        answer, code = {"http": e.code}, e.code
    except Exception as e:  # noqa: BLE001
        answer, code = {"exception": str(e)}, 0
    log.append({"key": key, "status": status, "users": sorted(users), "answer": answer, "http": code})
    return answer


def save_url(key, n):
    return f"{os.environ.get('FAKE_PUBLIC', 'http://fakeoffice:8000')}/cache/{key}/{n}"


API_JS = r"""
var fakeBase = (document.currentScript && document.currentScript.src.replace(/\/web-apps\/apps\/api\/documents\/api\.js.*$/, "")) || %(base)s;
window.DocsAPI = { DocEditor: function (id, config) {
  var base = fakeBase, key = config.document.key, user = config.editorConfig.user.id, ev = config.events || {};
  var host = document.getElementById(id), box = document.createElement("div");
  box.className = "fake-office"; box.setAttribute("data-mode", config.editorConfig.mode);
  var ta = document.createElement("textarea"); ta.className = "fake-office-text"; ta.style.width = "100%%"; ta.style.height = "300px";
  ta.readOnly = config.editorConfig.mode !== "edit"; box.appendChild(ta); host.appendChild(box);
  var post = function (path, body) { return fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(function (r) { return r.json(); }); };
  post("/fake/open", { config: config }).then(function (r) {
    if (r.error) { ev.onError && ev.onError({ data: { errorCode: r.error, errorDescription: r.message } }); return; }
    ta.value = r.text; ev.onDocumentReady && ev.onDocumentReady();
  });
  var timer = null;
  ta.addEventListener("input", function () {
    ev.onDocumentStateChange && ev.onDocumentStateChange({ data: true });
    clearTimeout(timer);  // edits reach the server a moment later, as the real one sends them
    timer = setTimeout(function () { post("/fake/edit", { key: key, user: user, text: ta.value }).then(function () {
      ev.onDocumentStateChange && ev.onDocumentStateChange({ data: false }); }); }, 700);
  });
  this.destroyEditor = function () { clearTimeout(timer); post("/fake/close", { key: key, user: user }); box.remove(); };
} };
"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path == "/healthcheck":
            return self._send(200, "true", "text/plain")
        if self.path.startswith("/web-apps/apps/api/documents/api.js"):
            base = os.environ.get("FAKE_PUBLIC", "http://fakeoffice:8000")
            return self._send(200, API_JS % {"base": json.dumps(base)}, "application/javascript")
        if self.path.startswith("/cache/"):
            _, _, key, n = self.path.split("/")
            with lock:
                d = docs.get(key) or finished.get(key)
            return self._send(200, d["versions"][int(n)].encode(), "application/octet-stream") if d else self._send(404, {})
        if self.path == "/fake/log":
            return self._send(200, log)
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path in ("/ConvertService.ashx", "/converter"):
            if SECRET:
                try:
                    body = verify(body.get("token") or "")
                except Exception:  # noqa: BLE001
                    return self._send(200, {"error": -8})
            try:
                with urllib.request.urlopen(body["url"], timeout=10) as r:
                    text = r.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return self._send(200, {"error": -4})  # as the real one: couldn't download the file
            with lock:
                finished[body["key"]] = {"versions": [text]}
            log.append({"key": body["key"], "convert": f"{body['filetype']}->{body['outputtype']}"})
            return self._send(200, {"endConvert": True, "percent": 100, "fileType": body["outputtype"],
                                    "fileUrl": save_url(body["key"], 0)})
        if self.path in ("/coauthoring/CommandService.ashx", "/command"):
            if SECRET:
                try:
                    body = verify(body.get("token") or "")
                except Exception:  # noqa: BLE001
                    return self._send(200, {"error": 6})
            if body.get("c") == "version":
                return self._send(200, {"error": 0, "version": "fake-8.2"})
            if body.get("c") == "forcesave":
                with lock:
                    d = docs.get(body.get("key"))
                    if not d:
                        return self._send(200, {"error": 1})
                    if d["text"] == d["saved_text"]:
                        return self._send(200, {"error": 4})
                    d["versions"].append(d["text"])
                    d["saved_text"] = d["text"]
                    n = len(d["versions"]) - 1
                threading.Thread(target=callback, args=(d, body["key"], 6, d["changed_by"], save_url(body["key"], n))).start()
                return self._send(200, {"error": 0, "key": body["key"]})
            return self._send(200, {"error": 5})
        if self.path == "/fake/fail-next":
            fail_next[0] = int(body.get("count", 1))
            return self._send(200, {"ok": True})
        if self.path == "/fake/open":
            cfg = body["config"]
            key = cfg["document"]["key"]
            if fail_next[0] > 0 or key in failed_keys:
                if fail_next[0] > 0:
                    fail_next[0] -= 1
                failed_keys.add(key)
                return self._send(200, {"error": -4, "message": "Download failed."})
            if SECRET:
                try:
                    signed = verify(cfg.get("token") or "")
                    assert signed["document"]["url"] == cfg["document"]["url"]
                except Exception:  # noqa: BLE001
                    return self._send(200, {"error": -20, "message": "The document security token is not correctly formed"})
            key = cfg["document"]["key"]
            with lock:
                d = docs.get(key)
                if not d:
                    with urllib.request.urlopen(cfg["document"]["url"], timeout=30) as r:
                        text = r.read().decode("utf-8", "replace")
                    d = docs[key] = {"text": text, "saved_text": text, "versions": [text], "users": {}, "changed_by": set(),
                                     "callback": cfg["editorConfig"]["callbackUrl"], "filetype": cfg["document"]["fileType"]}
                d["users"][cfg["editorConfig"]["user"]["id"]] = cfg["editorConfig"]["callbackUrl"]
                d["callback"] = cfg["editorConfig"]["callbackUrl"]
                text, users = d["text"], set(d["users"])
            threading.Thread(target=callback, args=(d, key, 1, users)).start()
            return self._send(200, {"text": text})
        if self.path == "/fake/edit":
            with lock:
                d = docs[body["key"]]
                d["text"] = body["text"]
                d["changed_by"].add(body["user"])
            return self._send(200, {"ok": True})
        if self.path == "/fake/close":  # the last person out: status 2 with the new version, or 4 (nothing changed)
            key = body["key"]
            with lock:
                d = docs.get(key)
                if not d:
                    return self._send(200, {})
                d["users"].pop(body["user"], None)
                if d["users"]:
                    return self._send(200, {"ok": True})
                finished[key] = docs.pop(key)
                url = None
                if d["text"] != d["saved_text"]:
                    d["versions"].append(d["text"])
                    url = save_url(key, len(d["versions"]) - 1)
            threading.Timer(1.0, callback, args=(d, key, 2 if url else 4, d["changed_by"] if url else set(), url)).start()
            return self._send(200, {"ok": True})
        self._send(404, {})


ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()

"""VI Clips app server: serves the app + runs the Research / Knowledge / Live / Edit / Feedback jobs. Binds 127.0.0.1 only.

  GET  /                      -> redirect to /app/index.html
  GET  /app/...               -> the app (static files from app/)
  GET  /config.js             -> window.VI_CONFIG = {engine: "http://127.0.0.1:<VI_ENGINE_PORT>"}
  GET  /data/<name>.json      -> research / knowledge data from data/app/
  GET  /api/ping | /api/status | /api/live
  POST /api/research/refresh  -> runs server/research.py in the background
  POST /api/knowledge/update  -> runs server/knowledge.py update in the background (needs Claude Code)
  POST /api/live/start {url} · /api/live/stop {id} · /api/research/live
  POST /api/edit {clip_id, text}  (needs Claude Code) · /api/feedback {...} · /api/reveal {clip_id, title}

Ports and folders come from server/settings.py (env vars VI_ENGINE_PORT, VI_SERVER_PORT, VI_DATA, VI_EXPORT_DIR).
"""
import json, os, re, shutil, subprocess, sys, threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402
import live  # noqa: E402
import feedback  # noqa: E402
import edit as clip_edit  # noqa: E402
import live_now  # noqa: E402
import knowledge  # noqa: E402

ROOT = settings.ROOT
PORT = settings.SERVER_PORT
STATE = {k: {"running": False, "last_run": None, "error": None} for k in ("research", "knowledge")}
JOBS = {"research": [sys.executable, os.path.join(ROOT, "server", "research.py")],
        "knowledge": [sys.executable, os.path.join(ROOT, "server", "knowledge.py"), "update"]}
EMPTY = {"research": {"updated": None, "channels": [], "live": []}}
LOCK = threading.Lock()


def run(kind):
    with LOCK:
        if STATE[kind]["running"]:
            return False
        STATE[kind].update(running=True, error=None)

    def go():
        try:
            p = subprocess.run(JOBS[kind], capture_output=True, text=True, timeout=1200, cwd=ROOT)
            err = None if p.returncode == 0 else ((p.stderr or p.stdout).strip().splitlines() or ["failed"])[-1][:300]
        except Exception as e:  # timeout etc.
            err = str(e)[:300]
        STATE[kind].update(running=False, error=err, last_run=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    threading.Thread(target=go, daemon=True).start()
    return True


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=settings.APP_DIR, **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _text(self, code, body, ctype):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/app", "/app/"):
            self.send_response(302)
            self.send_header("Location", "/app/index.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/config.js":
            cfg = {"engine": settings.ENGINE_URL}
            return self._text(200, "window.VI_CONFIG = " + json.dumps(cfg) + ";\n", "application/javascript")
        if path.startswith("/api/status"):
            return self._json(200, STATE)
        if path.startswith("/api/live"):
            return self._json(200, live.listing())
        if path.startswith("/api/ping"):
            return self._json(200, {"ok": True})
        m = re.fullmatch(r"/data/(research|knowledge)\.json", path)
        if m:
            f = os.path.join(settings.APP_DATA, m.group(1) + ".json")
            try:
                with open(f) as fh:
                    return self._text(200, fh.read(), "application/json")
            except FileNotFoundError:
                return self._json(200, EMPTY.get(m.group(1), {}))
        if path.startswith("/app/"):
            self.path = self.path[len("/app"):]
            return super().do_GET()
        return self._json(404, {"error": "not found"})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return {}

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/live/start":
            url = (self._body().get("url") or "").strip()
            if not url.startswith("http"):
                return self._json(400, {"error": "paste a live stream link"})
            try:
                return self._json(200, live.start(url))
            except Exception as e:  # noqa: BLE001
                return self._json(502, {"error": str(e)[:300]})
        if path == "/api/research/live":
            try:
                live_now.main()
                return self._json(200, {"ok": True})
            except Exception as e:  # noqa: BLE001
                return self._json(502, {"error": str(e)[:200]})
        if path == "/api/edit":
            b = self._body()
            if not b.get("clip_id") or not (b.get("text") or "").strip():
                return self._json(400, {"error": "say what to change"})
            try:
                return self._json(200, clip_edit.apply(b["clip_id"], b["text"]))
            except Exception as e:  # noqa: BLE001
                return self._json(502, {"error": str(e)[:300]})
        if path == "/api/feedback":
            try:
                return self._json(200, feedback.record(self._body()))
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": str(e)[:300]})
        if path == "/api/reveal":  # Export: copy the finished clip to the export folder and show it in Finder
            b = self._body()
            cid = int(b.get("clip_id") or 0)
            src = os.path.join(settings.ENGINE_DATA, "media", "clips", str(cid), "clip.mp4")
            if not cid or not os.path.exists(src):
                return self._json(404, {"error": "clip file not rendered yet"})
            d = settings.EXPORT_DIR
            os.makedirs(d, exist_ok=True)
            slug = re.sub(r"[^a-z0-9]+", "-", (b.get("title") or "clip").lower()).strip("-")[:50]
            dst = os.path.join(d, f"{cid}-{slug}.mp4")
            for f in os.listdir(d):  # drop older copies of the same clip
                if f.startswith(f"{cid}-") and f != os.path.basename(dst):
                    os.remove(os.path.join(d, f))
            shutil.copy2(src, dst)
            subprocess.Popen(["open", "-R", dst])
            return self._json(200, {"dir": d, "file": dst})
        if path == "/api/live/stop":
            return self._json(200 if live.stop(self._body().get("id")) else 404, {"stopped": True})
        kind = {"/api/research/refresh": "research", "/api/knowledge/update": "knowledge"}.get(path)
        if not kind:
            return self._json(404, {"error": "not found"})
        started = run(kind)
        return self._json(202 if started else 409, {"started": started, "running": True})


if __name__ == "__main__":
    knowledge.seed()  # first run: write the starter principles so Knowledge House isn't empty
    print(f"VI Clips server on http://127.0.0.1:{PORT}  (engine {settings.ENGINE_URL}, data {settings.DATA})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

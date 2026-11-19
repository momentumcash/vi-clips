"""Live clipping: record a live stream in 10-minute chunks and send each finished chunk to ClipForge.

Used by vi_server.py (POST /api/live/start {url} · POST /api/live/stop {id} · GET /api/live).
Also runnable alone:  python3 server/live.py "<live url>"   (Ctrl+C stops and processes the last chunk).

yt-dlp resolves the live HLS stream (free, no key); ffmpeg copies it into data/live/<id>/chunk_NNN.mp4 without re-encoding.
When a chunk closes it is imported into ClipForge as its own source and a clip job starts (small Whisper model: faster
than real time, so the clips keep up with the stream). Recording stops on its own when the stream ends or after MAX_HOURS.
"""
import json, os, re, signal, subprocess, sys, threading, time, urllib.request
from datetime import datetime, timezone

import settings

LIVE_DIR = os.path.join(settings.APP_DATA, "live")
CF = settings.ENGINE_URL
CHUNK_S = 600
MAX_HOURS = 3
FFMPEG = settings.ffmpeg()
YTDLP = settings.ytdlp()
COOKIES = settings.COOKIES


def _auth():
    """YouTube's bot check: use the exported cookie file (data/app/yt-cookies.txt) if you made one, else read the browser directly."""
    return ["--cookies", COOKIES] if os.path.exists(COOKIES) else ["--cookies-from-browser", settings.BROWSER]
FORMAT = "312+234/301+234/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cf(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CF + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def probe(url):
    p = subprocess.run([YTDLP, "--no-update", *_auth(), "--print", "%(is_live)s\t%(title)s\t%(channel)s\t%(id)s", url],
                       capture_output=True, text=True, timeout=90)
    line = (p.stdout or "").strip().splitlines()[-1:] or [""]
    parts = line[0].split("\t")
    if len(parts) < 4:
        raise RuntimeError((p.stderr or "could not read that link").strip().splitlines()[-1][:200])
    return {"live": parts[0] == "True", "title": parts[1], "channel": parts[2], "video_id": parts[3]}


class Session:
    def __init__(self, url, info):
        self.url, self.info = url, info
        self.id = f"{info['video_id']}-{int(time.time())}"
        self.dir = os.path.join(LIVE_DIR, self.id)
        os.makedirs(self.dir, exist_ok=True)
        self.state = {"id": self.id, "url": url, "title": info["title"], "channel": info["channel"], "status": "starting",
                      "started_at": now(), "ended_at": None, "recorded_s": 0, "chunks": [], "error": None}
        self.stop_flag = threading.Event()
        self.proc = None
        self.started = time.time()
        self.next_index = 0
        self.creator_id = None
        threading.Thread(target=self._run, daemon=True).start()

    # ------------------------------------------------------------ state
    def save(self):
        self.state["recorded_s"] = int(time.time() - self.started) if self.state["status"] == "recording" else self.state["recorded_s"]
        with open(os.path.join(self.dir, "state.json"), "w") as f:
            json.dump(self.state, f, indent=1)

    def view(self):
        s = dict(self.state)
        if s["status"] == "recording":
            s["recorded_s"] = int(time.time() - self.started)
        return s

    # ------------------------------------------------------------ recording
    def _resolve(self):
        p = subprocess.run([YTDLP, "--no-update", *_auth(), "-g", "-f", FORMAT, self.url], capture_output=True, text=True, timeout=90)
        urls = [u for u in (p.stdout or "").split() if u.startswith("http")]
        if not urls:
            raise RuntimeError((p.stderr or "no stream URL").strip().splitlines()[-1][:200])
        return urls[:2]

    def _ffmpeg(self, urls):
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
        for u in urls:
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10", "-i", u]
        cmd += ["-map", "0:v:0", "-map", ("1:a:0" if len(urls) > 1 else "0:a:0?"), "-c", "copy",
                "-f", "segment", "-segment_time", str(CHUNK_S), "-reset_timestamps", "1",
                "-segment_start_number", str(self.next_index),
                "-segment_list", os.path.join(self.dir, "list.csv"), "-segment_list_type", "csv",
                os.path.join(self.dir, "chunk_%03d.mp4")]
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def _run(self):
        try:
            self._creator()
            self.state["status"] = "recording"; self.save()
            watcher = threading.Thread(target=self._watch, daemon=True); watcher.start()
            while not self.stop_flag.is_set():
                if time.time() - self.started > MAX_HOURS * 3600:
                    self.state["error"] = f"stopped at the {MAX_HOURS} h cap"
                    break
                self.proc = self._ffmpeg(self._resolve())
                while self.proc.poll() is None and not self.stop_flag.is_set():
                    if time.time() - self.started > MAX_HOURS * 3600:
                        break
                    time.sleep(2)
                if self.proc.poll() is None:  # stop / cap: let ffmpeg close the current chunk cleanly
                    self.proc.send_signal(signal.SIGINT)
                    try: self.proc.wait(20)
                    except subprocess.TimeoutExpired: self.proc.kill()
                    break
                # ffmpeg ended by itself: stream over, or the HLS link expired -> check and resume numbering
                self.next_index = self._highest_chunk() + 1
                if self.stop_flag.is_set():
                    break
                still = False  # a hiccup (bot check, network) must not end the session: ask up to 4 times
                for _ in range(4):
                    try:
                        if probe(self.url)["live"]:
                            still = True
                            break
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(15)
                if not still:
                    break
                time.sleep(3)
            self.state["status"] = "finishing"; self.save()
            time.sleep(4)
            self._import_new(final=True)
            watcher.join(timeout=1)
        except Exception as e:  # noqa: BLE001
            self.state["error"] = str(e)[:300]
        self.state["status"] = "ended"; self.state["ended_at"] = now(); self.save()

    def _highest_chunk(self):
        n = [int(m.group(1)) for f in os.listdir(self.dir) if (m := re.match(r"chunk_(\d+)\.mp4$", f))]
        return max(n) if n else -1

    # ------------------------------------------------------------ hand-off to ClipForge
    def _creator(self):
        name = self.info["channel"] or "Live"
        for c in cf("GET", "/api/creators"):
            if c["name"].lower() == name.lower():
                self.creator_id = c["id"]; return
        self.creator_id = cf("POST", "/api/creators", {"name": name, "platform": "youtube", "rights_status": "unknown",
                                                         "rights_note": "Live stream clipped with VI Clips"})["id"]

    def _finished_chunks(self, final):
        done = set()
        try:
            with open(os.path.join(self.dir, "list.csv")) as f:
                done = {line.split(",")[0].strip() for line in f if line.strip()}
        except FileNotFoundError:
            pass
        if final:  # after stop, every chunk on disk is closed
            done |= {f for f in os.listdir(self.dir) if re.match(r"chunk_\d+\.mp4$", f)}
        return sorted(done)

    def _import_new(self, final=False):
        have = {c["file"] for c in self.state["chunks"]}
        for name in self._finished_chunks(final):
            path = os.path.join(self.dir, name)
            if name in have or not os.path.exists(path) or os.path.getsize(path) < 2_000_000:
                continue
            idx = int(re.search(r"(\d+)", name).group(1))
            start = idx * CHUNK_S
            title = f"{self.info['title'][:70]} · {start // 3600}:{start % 3600 // 60:02d} in"
            entry = {"file": name, "index": idx, "source_id": None, "job_id": None, "error": None, "at": now()}
            try:
                src = cf("POST", "/api/sources/import", {"path": path, "creator_id": self.creator_id, "title": title})
                entry["source_id"] = src["id"]
                job = cf("POST", "/api/jobs", {"source_id": src["id"], "settings": {"target": "auto", "whisper_model": "small", "max_clips": 4}})
                entry["job_id"] = job["id"]
            except Exception as e:  # noqa: BLE001
                entry["error"] = str(e)[:200]
            self.state["chunks"].append(entry); self.save()

    def _watch(self):
        while self.state["status"] == "recording":
            self._import_new()
            self.save()
            time.sleep(10)

    def stop(self):
        self.stop_flag.set()


SESSIONS = {}


def start(url):
    info = probe(url)
    if not info["live"]:
        return {"live": False, **info}
    for s in SESSIONS.values():
        if s.info["video_id"] == info["video_id"] and s.state["status"] in ("starting", "recording"):
            return {"live": True, "session": s.view()}
    s = Session(url, info)
    SESSIONS[s.id] = s
    return {"live": True, "session": s.view()}


def stop(sid):
    s = SESSIONS.get(sid)
    if not s:
        return False
    s.stop()
    return True


def listing():
    return [s.view() for s in sorted(SESSIONS.values(), key=lambda s: s.started, reverse=True)]


if __name__ == "__main__":
    r = start(sys.argv[1])
    if not r["live"]:
        sys.exit("not live: " + r.get("title", ""))
    sid = r["session"]["id"]
    print("recording", sid)
    try:
        while SESSIONS[sid].state["status"] != "ended":
            time.sleep(5)
    except KeyboardInterrupt:
        stop(sid)
        while SESSIONS[sid].state["status"] != "ended":
            time.sleep(2)

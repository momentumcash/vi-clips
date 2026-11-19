"""Job runner: one heavy job at a time on a worker thread, resumable stage by stage.

Each stage writes an artifact under data/media/work/<source_id>/ (shared, model-keyed) or .../job_<id>/ (job-specific).
On restart, jobs left `running` are re-queued and resume from the first stage whose artifact is missing or invalid.
A failed stage is retried up to 3x with backoff; then the job fails with a readable error and can be retried.
"""
import glob
import json
import os
import queue
import threading
import time
import traceback

import config
import db as dbm
import media
from config import STAGES, TARGETS

MAX_ATTEMPTS = 3
SHARED_STAGES = ("probe", "extract_audio", "transcribe", "signals")
BACKOFF = (2, 6, 15)


class JobError(Exception):
    def __init__(self, status, error, detail="", fix=""):
        super().__init__(error)
        self.status, self.error, self.detail, self.fix = status, error, detail, fix


class StageFatal(Exception):
    """A stage failure that retrying cannot fix (no speech, rights denied, model refused twice…)."""


class Stopping(Exception):
    pass


# ---------------------------------------------------------------------------------------------- helpers
def load_presets():
    with open(config.PRESETS_FILE) as f:
        return json.load(f)


def preset_names():
    return list(load_presets().keys())


def cached_whisper_models():
    hub = os.path.expanduser("~/.cache/huggingface/hub")
    out = []
    names = {"Systran--faster-whisper-": "", "Systran--faster-distil-whisper-": "distil-", "mobiuslabsgmbh--faster-whisper-": ""}
    for d in sorted(glob.glob(os.path.join(hub, "models--*"))):
        b = os.path.basename(d)[len("models--"):]
        for pre, alias in names.items():
            if b.startswith(pre) and glob.glob(os.path.join(d, "snapshots", "*", "model.bin")):
                out.append(alias + b[len(pre):])
    return out


def work_dir(app, source_id):
    return app.storage.ensure_dir(f"work/{source_id}")


def job_dir(app, job):
    return app.storage.ensure_dir(f"work/{job['source_id']}/job_{job['id']}")


def load_transcript(app, source_id):
    return media.read_json(os.path.join(app.storage.path(f"work/{source_id}"), "transcript.json"))


def words_in_range(app, source_id, a, b):
    tr = load_transcript(app, source_id)
    if not tr:
        return []
    return [w for w in tr["words"] if w["end"] >= a and w["start"] <= b]


def timeline(app, s):
    wd = app.storage.path(f"work/{s['id']}")
    sig = media.read_json(os.path.join(wd, "signals.json")) or {}
    frames = []
    for p in sorted(glob.glob(os.path.join(wd, "strip", "f_*.jpg"))):
        try:
            t = float(os.path.basename(p)[2:-4]) / 1000.0
        except ValueError:
            continue
        frames.append({"t": round(t, 2), "url": app.storage.url(f"work/{s['id']}/strip/{os.path.basename(p)}")})
    dur = s.get("duration_s") or 0
    tr = load_transcript(app, s["id"])
    if tr:
        cov = 1.0
    else:
        part = os.path.join(wd, "transcript.partial.jsonl")
        last = 0.0
        if os.path.exists(part):
            with open(part) as f:
                for line in f:
                    try:
                        last = max(last, json.loads(line)["end"])
                    except (ValueError, KeyError):
                        pass
        cov = min(1.0, last / dur) if dur else 0.0
    clips = app.db.query("clips", "SELECT id,start_s,end_s,overall,status,title,rank FROM clips WHERE source_id=? ORDER BY start_s", (s["id"],))
    return {"source_id": s["id"], "duration": dur, "energy": sig.get("curve", []), "energy_unit": "dBFS (RMS)",
            "frames": frames, "scene_cuts": sig.get("scene_cuts", []), "words_coverage": round(cov, 4),
            "laughter_like": sig.get("laughter_like", []), "loud_bursts": sig.get("loud_bursts", []),
            "clips": [{"id": c["id"], "start": c["start_s"], "end": c["end_s"], "overall": c["overall"], "status": c["status"],
                       "title": c["title"], "rank": c["rank"]} for c in clips]}


def clean_partials(base):
    """Delete half-written outputs (*.part.*, *.part, interrupted uploads) under data/media. Only called at startup,
    when no child process of ours can be writing."""
    removed = []
    for d, _dirs, files in os.walk(base):
        for f in files:
            p = os.path.join(d, f)
            if ".part." in f or f.endswith(".part") or os.sep + "_incoming" + os.sep in p:
                try:
                    os.remove(p)
                    removed.append(p)
                except OSError:
                    pass
    return removed


# relative wall-time cost of each stage (measured on the E2E runs: transcribe and render dominate)
STAGE_WEIGHTS = {"verify": 0.5, "probe": 0.5, "extract_audio": 1, "transcribe": 45, "signals": 10, "detect": 8, "score": 2,
                 "dedupe": 0.5, "render": 32, "qc": 0.5}


def weighted_progress(stages):
    tot = sum(STAGE_WEIGHTS.get(s["name"], 1) for s in stages) or 1
    done = 0.0
    for s in stages:
        w = STAGE_WEIGHTS.get(s["name"], 1)
        if s["status"] in ("done", "skipped"):
            done += w
        elif s["status"] == "running":
            done += w * float(s.get("progress") or 0)
    return round(done / tot, 4)


def new_stages():
    return [{"name": n, "status": "pending", "started_at": None, "ended_at": None, "progress": 0.0, "detail": "", "error": None,
             "attempts": 0} for n in STAGES]


# ---------------------------------------------------------------------------------------------- context
class Ctx:
    def __init__(self, runner, job):
        self.runner = runner
        self.app = runner.app
        self.job = job
        self.source = self.app.db.get("sources", job["source_id"])
        self.creator = self.app.db.get("creators", self.source["creator_id"]) if self.source else None
        self.src_path = self.app.source_path(self.source) if self.source else None
        self.settings = job["settings"]
        self.wd = work_dir(self.app, job["source_id"])
        self.jd = job_dir(self.app, job)
        self.cancel = runner.cancel_flag(job["id"])
        self.stage = None
        self._last_db = 0.0
        self._last_pub = 0.0
        self.cost = 0.0
        self.llm_metas = []

    def p(self, name):
        return os.path.join(self.wd, name)

    def jp(self, name):
        return os.path.join(self.jd, name)

    def probe(self):
        return media.read_json(self.p("probe.json"))

    def check(self):
        if self.runner.stopping.is_set():
            raise Stopping()
        if self.cancel.is_set():
            raise media.Cancelled()

    def progress(self, frac, detail=None, force=False):
        st = self.stage
        if st is None:
            return
        st["progress"] = round(max(0.0, min(1.0, float(frac))), 4)
        if detail is not None:
            st["detail"] = detail
        now = time.time()
        if force:
            self._last_db = self._last_pub = 0.0
        if now - self._last_db > 1.0:
            self.runner.save_job(self.job)
            self._last_db = now
        if now - self._last_pub > 0.4:
            self.runner.publish_job(self.job)
            self._last_pub = now

    def add_llm(self, metas):
        for m in metas:
            self.llm_metas.append(m)
            self.cost += float(m.get("cost_usd") or 0)
        self.job["cost_usd"] = round((self.job.get("cost_usd") or 0) + sum(float(m.get("cost_usd") or 0) for m in metas), 5)
        self.runner.save_job(self.job)


# ---------------------------------------------------------------------------------------------- runner
class Runner:
    def __init__(self, app):
        self.app = app
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self._flags = {}
        self._lock = threading.Lock()
        self.render_slots = threading.BoundedSemaphore(3)
        self.render_q = queue.Queue()
        self.threads = []
        self.current_job = None

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        db = self.app.db
        killed = media.reap_orphans()
        if killed:
            db.event("engine", f"Stopped {len(killed)} leftover worker process(es) from the previous run", level="warn")
        removed = clean_partials(self.app.storage.base)
        if removed:
            db.event("engine", f"Removed {len(removed)} partial file(s) left by an interrupted run", level="warn")
        for j in db.query("jobs", "SELECT * FROM jobs WHERE status='running'"):
            for st in j["stages"]:
                if st["status"] == "running":
                    st["status"] = "pending"
                    st["detail"] = "resumed after engine restart"
                    st["attempts"] = max(0, int(st.get("attempts") or 0) - 1)  # an interrupted run is not a failed attempt
                    st["resumed"] = int(st.get("resumed") or 0) + 1
            db.update("jobs", j["id"], {"status": "queued", "stages": j["stages"], "updated_at": dbm.now()})
            db.event("job", f"Job #{j['id']} will resume after restart (stage {j.get('current_stage')})", ref={"job_id": j["id"]})
        for c in db.query("clips", "SELECT * FROM clips WHERE status='rendering'"):
            j = db.get("jobs", c["job_id"]) if c.get("job_id") else None
            if j and j["status"] in ("queued", "running"):
                continue  # its job's render stage will pick it up
            self.render_q.put(("render", c["id"]))
        import pipeline  # noqa: F401  load numpy/pipeline once in the main thread: parallel first imports crash numpy
        for target in (self._job_loop, self._render_loop, self._render_loop, self._render_loop, self._automation_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self.threads.append(t)

    def shutdown(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.wake.set()
        media.kill_all_children()

    def cancel_flag(self, job_id):
        with self._lock:
            return self._flags.setdefault(job_id, threading.Event())

    # ---------------------------------------------------------------- persistence
    def save_job(self, job):
        job["updated_at"] = dbm.now()
        self.app.db.update("jobs", job["id"], {k: job[k] for k in ("stages", "status", "current_stage", "cost_usd", "error", "updated_at")})

    def publish_job(self, job):
        self.app.db.bus.publish("job", self.app.job_view(dict(job)))

    # ---------------------------------------------------------------- API actions
    def create_job(self, source_id, settings):
        app = self.app
        s = app.db.get("sources", source_id)
        if not s:
            raise JobError(404, "source not found")
        p = app.source_path(s)
        if not p or not os.path.exists(p):
            raise JobError(410, "source file is missing", p or "", "re-import the file or reconnect the drive it lives on")
        if s.get("rights_status") == "denied":
            raise JobError(403, "rights denied", "this source's rights_status is 'denied'", "processing is blocked for denied sources")
        active = app.db.query("jobs", "SELECT * FROM jobs WHERE source_id=? AND status IN ('queued','running')", (source_id,))
        if active:
            raise JobError(409, "already processing", f"job #{active[0]['id']} is {active[0]['status']}", "wait for it or cancel it")
        g = app.settings()
        st = {"target": g["automation"].get("default_target", "auto"), "caption_preset": g.get("default_caption_preset", "bold_entertainment"),
              "max_clips": g.get("max_clips", 8), "min_score": g["automation"].get("min_score", 60),
              "whisper_model": g.get("whisper_model", "small"), "progress_bar": g["render"].get("progress_bar", True),
              "punch_in": g["render"].get("punch_in", False)}
        user = {k: v for k, v in (settings or {}).items() if v not in (None, "")}
        if "whisper_model" not in user and (s.get("duration_s") or 1e9) <= float(g.get("short_source_max_min", 30)) * 60 \
                and g.get("whisper_model_short") in cached_whisper_models():
            st["whisper_model"] = g["whisper_model_short"]  # better proper nouns for final renders on short sources
        st.update(user)
        if st["target"] not in TARGETS:
            raise JobError(400, "invalid target", st["target"], f"use one of {', '.join(TARGETS)}")
        if st["caption_preset"] not in preset_names():
            raise JobError(400, "unknown caption preset", st["caption_preset"], f"use one of {', '.join(preset_names())}")
        if st["whisper_model"] not in cached_whisper_models():
            raise JobError(400, "whisper model not cached", st["whisper_model"],
                           f"use one of {', '.join(cached_whisper_models())} (ClipForge never downloads models silently)")
        st["max_clips"] = max(1, min(30, int(st["max_clips"])))
        st["min_score"] = max(0, min(100, float(st["min_score"])))
        now = dbm.now()
        jid = app.db.insert("jobs", {"source_id": source_id, "kind": "process", "settings": st, "stages": new_stages(),
                                     "status": "queued", "current_stage": None, "cost_usd": 0, "created_at": now, "updated_at": now})
        app.db.update("sources", source_id, {"status": "processing"})
        app.db.event("job", f"Queued processing for “{s['title']}” (target {st['target']})", ref={"job_id": jid, "source_id": source_id})
        j = app.db.get("jobs", jid)
        self.publish_job(j)
        self.wake.set()
        return j

    def retry(self, job_id):
        j = self.app.db.get("jobs", job_id)
        if not j:
            raise JobError(404, "job not found")
        if j["status"] not in ("failed", "cancelled"):
            raise JobError(409, f"job is {j['status']}", "", "only failed or cancelled jobs can be retried")
        for st in j["stages"]:
            if st["status"] in ("failed", "running"):
                st.update(status="pending", attempts=0, error=None, progress=0.0, started_at=None, ended_at=None, detail="retry requested")
        j.update(status="queued", error=None)
        self.cancel_flag(job_id).clear()
        self.save_job(j)
        self.app.db.update("sources", j["source_id"], {"status": "processing"})
        self.app.db.event("job", f"Job #{job_id} retry queued", ref={"job_id": job_id})
        self.publish_job(j)
        self.wake.set()
        return j

    def cancel(self, job_id):
        j = self.app.db.get("jobs", job_id)
        if not j:
            raise JobError(404, "job not found")
        if j["status"] not in ("queued", "running"):
            raise JobError(409, f"job is {j['status']}", "", "only queued or running jobs can be cancelled")
        self.cancel_flag(job_id).set()
        if j["status"] == "queued":
            j["status"] = "cancelled"
            self.save_job(j)
            self._finish_source(j)
            self.publish_job(j)
        self.app.db.event("job", f"Job #{job_id} cancel requested", ref={"job_id": job_id})
        return self.app.db.get("jobs", job_id)

    # ---------------------------------------------------------------- job loop
    def _job_loop(self):
        while not self.stopping.is_set():
            rows = self.app.db.query("jobs", "SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")
            if not rows:
                self.wake.wait(2.0)
                self.wake.clear()
                continue
            try:
                self._run_job(rows[0])
            except Exception:  # noqa: BLE001 - never let the worker die
                traceback.print_exc()
                time.sleep(1)

    def _artifact_ok(self, ctx, name):
        import pipeline
        try:
            return pipeline.VALID[name](ctx)
        except Exception:  # noqa: BLE001
            return False

    def _run_job(self, job):
        import pipeline
        app = self.app
        if self.cancel_flag(job["id"]).is_set():
            job["status"] = "cancelled"
            self.save_job(job)
            return
        job["status"] = "running"
        job["error"] = None
        self.current_job = job["id"]
        self.save_job(job)
        app.db.update("sources", job["source_id"], {"status": "processing"})
        ctx = Ctx(self, job)
        invalidate = False
        try:
            for st in job["stages"]:
                name = st["name"]
                ctx.check()
                if st["status"] in ("done", "skipped") and not invalidate:
                    if self._artifact_ok(ctx, name):
                        continue
                    invalidate = True  # artifact gone/stale: redo this stage and everything after it
                if invalidate and st["status"] in ("done", "skipped"):
                    st.update(status="pending", progress=0.0, started_at=None, ended_at=None, detail="re-running: upstream artifact changed")
                if (st["status"] == "pending" and not invalidate and name in SHARED_STAGES and int(st.get("attempts") or 0) == 0
                        and self._artifact_ok(ctx, name)):
                    # shared per-source artifact from an earlier job on the same source (same whisper model) → reuse it
                    now = dbm.now()
                    st.update(status="done", progress=1.0, started_at=now, ended_at=now, detail="reused from an earlier run on this source")
                    self.save_job(job)
                    self.publish_job(job)
                    continue
                self._run_stage(ctx, st, getattr(pipeline, "stage_" + name))
            job["status"] = "done"
            job["current_stage"] = None
            self.save_job(job)
            n = app.db.scalar("SELECT COUNT(*) FROM clips WHERE job_id=? AND status='rendered'", (job["id"],))
            app.db.event("job", f"Done: {n} clip(s) ready from “{ctx.source['title']}”" + (f" · LLM ${job['cost_usd']:.3f}" if job.get("cost_usd") else ""),
                         ref={"job_id": job["id"], "source_id": job["source_id"]})
        except Stopping:
            self.save_job(job)  # stays 'running' → resumed on next start
            return
        except media.Cancelled:
            for st in job["stages"]:
                if st["status"] == "running":
                    st.update(status="pending", detail="cancelled")
            job["status"] = "cancelled"
            self.save_job(job)
            app.db.event("job", f"Job #{job['id']} cancelled", level="warn", ref={"job_id": job["id"]})
        except Exception as e:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = job.get("error") or f"{type(e).__name__}: {e}"
            self.save_job(job)
            app.db.event("job", f"Job #{job['id']} failed — {job['error']}", level="error", ref={"job_id": job["id"]})
        finally:
            self.current_job = None
            self._finish_source(job)
            self.publish_job(job)

    def _finish_source(self, job):
        st = {"done": "processed", "failed": "failed", "cancelled": "ready"}.get(job["status"])
        if st:
            self.app.db.update("sources", job["source_id"], {"status": st})

    def _run_stage(self, ctx, st, fn):
        job = ctx.job
        while True:
            ctx.check()
            # keep the first start time across engine restarts / retries so wall times include the whole stage
            st.update(status="running", started_at=st.get("started_at") or dbm.now(), ended_at=None, error=None, progress=0.0)
            st["attempts"] = int(st.get("attempts") or 0) + 1
            job["current_stage"] = st["name"]
            ctx.stage = st
            self.save_job(job)
            self.publish_job(job)
            try:
                detail = fn(ctx)
                ctx.check()
                st.update(status="skipped" if detail == "skipped" or (isinstance(detail, str) and detail.startswith("skipped")) else "done",
                          progress=1.0, ended_at=dbm.now())
                if detail:
                    st["detail"] = detail
                self.save_job(job)
                self.publish_job(job)
                return
            except (Stopping, media.Cancelled):
                raise
            except Exception as e:  # noqa: BLE001
                if self.stopping.is_set():
                    raise Stopping()
                fatal = isinstance(e, StageFatal)
                msg = str(e) or type(e).__name__
                if not fatal:
                    traceback.print_exc()
                st.update(error=msg, ended_at=dbm.now())
                if fatal or st["attempts"] >= MAX_ATTEMPTS:
                    st["status"] = "failed"
                    job["error"] = f"{st['name']}: {msg}"
                    self.save_job(job)
                    raise
                wait = BACKOFF[min(st["attempts"] - 1, len(BACKOFF) - 1)]
                st.update(status="pending", detail=f"attempt {st['attempts']} failed — retrying in {wait}s: {msg[:200]}")
                self.save_job(job)
                self.publish_job(job)
                self.app.db.event("job", f"Job #{job['id']} {st['name']} attempt {st['attempts']} failed: {msg[:160]}", level="warn",
                                  ref={"job_id": job["id"]})
                for _ in range(wait * 10):
                    ctx.check()
                    time.sleep(0.1)

    # ---------------------------------------------------------------- clip-level work (re-render, variants)
    def _render_loop(self):
        import pipeline
        while not self.stopping.is_set():
            try:
                kind, cid = self.render_q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if kind == "render":
                    pipeline.render_one(self.app, cid, slots=self.render_slots, stopping=self.stopping)
                elif kind == "variants":
                    pipeline.make_variants(self.app, cid, self)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                if not self.stopping.is_set():
                    self.app.db.update("clips", cid, {"status": "failed", "error": f"{type(e).__name__}: {e}", "updated_at": dbm.now()})
                    self.app.db.event("clip", f"Clip #{cid} {kind} failed: {e}", level="error", ref={"clip_id": cid})
                    self.app.db.bus.publish("clip", self.app.clip_view(self.app.db.get("clips", cid)))

    def request_render(self, clip_id):
        c = self.app.db.get("clips", clip_id)
        if not c:
            raise JobError(404, "clip not found")
        if c["status"] == "rendering":
            raise JobError(409, "already rendering")
        j = self.app.db.get("jobs", c["job_id"]) if c.get("job_id") else None
        if j and j["status"] in ("queued", "running") and j.get("current_stage") in (None, "verify", "probe", "extract_audio",
                                                                                     "transcribe", "signals", "detect", "score", "dedupe", "render"):
            raise JobError(409, "job still running", f"job #{j['id']} is at {j.get('current_stage')}", "wait for the job to finish")
        self.app.db.update("clips", clip_id, {"status": "rendering", "error": None, "updated_at": dbm.now()})
        self.render_q.put(("render", clip_id))
        c = self.app.db.get("clips", clip_id)
        self.app.db.bus.publish("clip", self.app.clip_view(dict(c)))
        return c

    def request_variants(self, clip_id):
        c = self.app.db.get("clips", clip_id)
        if not c:
            raise JobError(404, "clip not found")
        if c.get("variant_of"):
            raise JobError(400, "clip is already a variant", f"variant of #{c['variant_of']}", "ask for variants on the original clip")
        n = self.app.db.scalar("SELECT COUNT(*) FROM clips WHERE variant_of=?", (clip_id,))
        if n >= 3:
            raise JobError(409, "variants already exist", f"{n} variants", "delete or reject them first")
        self.render_q.put(("variants", clip_id))
        self.app.db.event("clip", f"Generating alternate hook openings for clip #{clip_id}", ref={"clip_id": clip_id})
        return {"status": "queued", "clip_id": clip_id, "detail": "variants appear as new clips with variant_of set (SSE 'clip' events)"}

    def patch_clip(self, clip_id, body):
        import pipeline
        return pipeline.patch_clip(self.app, clip_id, body)

    # ---------------------------------------------------------------- automation (folder monitoring)
    def _automation_loop(self):
        from sources import folder as src_folder
        last = {}
        while not self.stopping.is_set():
            time.sleep(5)
            try:
                g = self.app.settings()["automation"]
                every = max(1, int(g.get("scan_interval_min") or 60)) * 60
                for c in self.app.db.query("creators", "SELECT * FROM creators WHERE monitoring=1 AND source_method='folder'"):
                    if time.time() - last.get(c["id"], 0) < every:
                        continue
                    last[c["id"]] = time.time()
                    src_folder.scan(self.app, c)
                    self.app.db.update("creators", c["id"], {"last_scan_at": dbm.now()})
            except Exception:  # noqa: BLE001
                traceback.print_exc()

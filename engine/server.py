#!/usr/bin/env python3
"""ClipForge engine — stdlib HTTP server: JSON API, SSE stream, static UI, media with Range.

Run:  $PY engine/server.py            (binds 127.0.0.1:$VI_ENGINE_PORT, default 8870)
"""
import datetime as _dt
import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from config import DATA, UI, VERSION, ROOT, EXPORT_RIGHTS, RIGHTS, PLATFORMS, SOURCE_METHODS  # noqa: E402
import db as dbm  # noqa: E402
import storage as storagem  # noqa: E402
import media  # noqa: E402
import jobs as jobsm  # noqa: E402
import exporter  # noqa: E402
import integrations  # noqa: E402
from sources import local as src_local, folder as src_folder, ytdlp_authorized as src_ytdlp  # noqa: E402
from sources import youtube_api as src_yt, twitch_api as src_tw  # noqa: E402


class ApiError(Exception):
    def __init__(self, status, error, detail="", fix=""):
        super().__init__(error)
        self.status, self.error, self.detail, self.fix = status, error, detail, fix


class App:
    def __init__(self, data_dir=None, start_runner=True):
        config.ensure_dirs()
        self.db = dbm.DB(os.path.join(data_dir or DATA, "clipforge.db"))
        self.storage = storagem.LocalStorage(os.path.join(data_dir or DATA, "media"))
        self.runner = jobsm.Runner(self)
        if start_runner:
            self.runner.start()

    # ------------------------------------------------------------------ settings
    def settings(self):
        out = json.loads(json.dumps(config.DEFAULT_SETTINGS))
        for k in list(out):
            v = self.db.get_setting(k)
            if v is not None:
                if isinstance(out[k], dict) and isinstance(v, dict):
                    out[k].update(v)
                else:
                    out[k] = v
        return out

    # ------------------------------------------------------------------ views
    def creator_view(self, c):
        if not c:
            return None
        c["source_count"] = self.db.scalar("SELECT COUNT(*) FROM sources WHERE creator_id=?", (c["id"],))
        c["clip_count"] = self.db.scalar(
            "SELECT COUNT(*) FROM clips JOIN sources s ON s.id=clips.source_id WHERE s.creator_id=? AND clips.status!='rejected'",
            (c["id"],))
        c["export_allowed"] = c.get("rights_status") in EXPORT_RIGHTS
        return c

    def source_path(self, s):
        if s.get("file_key") and not s["file_key"].startswith("origin/"):
            return self.storage.path(s["file_key"])
        return s.get("origin_ref")

    def source_view(self, s, deep=False):
        if not s:
            return None
        cr = self.db.get("creators", s["creator_id"]) if s.get("creator_id") else None
        s["creator_name"] = cr["name"] if cr else None
        s["thumb_url"] = self.storage.url(s["thumb_key"]) if s.get("thumb_key") else None
        s["video_url"] = f"/media/origin/{s['id']}" if s.get("origin") != "url" or s.get("file_key") else None
        if s.get("file_key") and not s["file_key"].startswith("origin/"):
            s["video_url"] = self.storage.url(s["file_key"])
        p = self.source_path(s)
        s["file_present"] = bool(p and os.path.exists(p))
        s["export_allowed"] = s.get("rights_status") in EXPORT_RIGHTS
        counts = {r["status"]: r["n"] for r in self.db.query(
            "clips", "SELECT status, COUNT(*) AS n FROM clips WHERE source_id=? GROUP BY status", (s["id"],))}
        s["clip_counts"] = counts
        last = self.db.query("jobs", "SELECT * FROM jobs WHERE source_id=? ORDER BY id DESC LIMIT 1", (s["id"],))
        s["last_job"] = self.job_view(last[0]) if last else None
        if deep:
            s["jobs"] = [self.job_view(j) for j in self.db.query("jobs", "SELECT * FROM jobs WHERE source_id=? ORDER BY id DESC", (s["id"],))]
        return s

    def job_view(self, j):
        if not j:
            return None
        j["progress"] = jobsm.weighted_progress(j.get("stages") or [])
        return j

    def clip_view(self, c):
        if not c:
            return None
        c["duration"] = round((c["end_s"] or 0) - (c["start_s"] or 0), 3)
        c["video_url"] = self.storage.url(c["file_key"]) if c.get("file_key") and self.storage.exists(c["file_key"]) else None
        c["thumb_url"] = self.storage.url(c["thumb_key"]) if c.get("thumb_key") and self.storage.exists(c["thumb_key"]) else None
        s = self.db.get("sources", c["source_id"])
        c["source_title"] = s["title"] if s else None
        cr = self.db.get("creators", s["creator_id"]) if s and s.get("creator_id") else None
        c["creator_name"] = cr["name"] if cr else None
        c["creator_id"] = cr["id"] if cr else None
        c["rights_status"] = s.get("rights_status") if s else None
        c["export_allowed"] = c["rights_status"] in EXPORT_RIGHTS
        c["score_label"] = "ClipForge score — editorial estimate, not a view prediction"
        c["render_stale"] = False
        if c.get("file_key") and c.get("render_sig"):
            try:
                import render as renderm
                j = self.db.get("jobs", c["job_id"]) if c.get("job_id") else None
                st = (j or {}).get("settings") or {"progress_bar": True}
                c["render_stale"] = renderm.signature(c, c.get("caption_preset"), st) != c["render_sig"]
            except Exception:  # noqa: BLE001
                pass
        return c


# ====================================================================================== HTTP handler
ROUTES = []


def route(method, pattern):
    rx = re.compile("^" + re.sub(r":(\w+)", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


def _int(v, name):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"{name} must be an integer", f"got {v!r}", f"pass a numeric {name}")


def _need(obj, *keys):
    for k in keys:
        if obj.get(k) in (None, ""):
            raise ApiError(400, f"missing field '{k}'", "", f"include '{k}' in the JSON body")


# ---------------------------------------------------------------- meta
@route("GET", "/api/ping")
def ping(app, h, q, body):
    return {"ok": True, "version": VERSION, "root": ROOT, "pid": os.getpid()}


@route("GET", "/api/overview")
def overview(app, h, q, body):
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    clips_today = app.db.scalar(
        "SELECT COUNT(*) FROM clips WHERE status IN ('rendered','approved','exported') AND substr(updated_at,1,10)=?", (today,))
    top = app.db.query("clips", "SELECT * FROM clips WHERE status IN ('rendered','approved','exported') ORDER BY overall DESC LIMIT 6")
    acts = app.db.query("events", "SELECT * FROM events ORDER BY id DESC LIMIT 20")
    return {
        "clips_today": clips_today,
        "creators_monitored": app.db.scalar("SELECT COUNT(*) FROM creators WHERE monitoring=1"),
        "queue": {"queued": app.db.scalar("SELECT COUNT(*) FROM jobs WHERE status='queued'"),
                  "running": app.db.scalar("SELECT COUNT(*) FROM jobs WHERE status='running'")},
        "totals": {"creators": app.db.scalar("SELECT COUNT(*) FROM creators"),
                   "sources": app.db.scalar("SELECT COUNT(*) FROM sources"),
                   "clips_rendered": app.db.scalar("SELECT COUNT(*) FROM clips WHERE status IN ('rendered','approved','exported')")},
        "top_clips": [app.clip_view(c) for c in top],
        "activity": acts,
    }


# ---------------------------------------------------------------- creators
CREATOR_FIELDS = ("name", "platform", "channel_url", "monitoring", "categories", "source_method", "folder_path",
                  "rights_status", "rights_note", "vocab")


def _validate_creator(f, partial=False):
    if not partial:
        _need(f, "name")
    if "platform" in f and f["platform"] not in PLATFORMS:
        raise ApiError(400, "invalid platform", f"{f['platform']!r}", f"use one of {', '.join(PLATFORMS)}")
    if "source_method" in f and f["source_method"] not in SOURCE_METHODS:
        raise ApiError(400, "invalid source_method", f"{f['source_method']!r}", f"use one of {', '.join(SOURCE_METHODS)}")
    if "rights_status" in f and f["rights_status"] not in RIGHTS:
        raise ApiError(400, "invalid rights_status", f"{f['rights_status']!r}", f"use one of {', '.join(RIGHTS)}")
    if f.get("folder_path"):
        f["folder_path"] = os.path.expanduser(f["folder_path"])
        if not os.path.isdir(f["folder_path"]):
            raise ApiError(400, "folder not found", f["folder_path"], "give an existing folder path")
    if "monitoring" in f:
        f["monitoring"] = 1 if f["monitoring"] else 0
    if "categories" in f and not isinstance(f["categories"], list):
        raise ApiError(400, "categories must be a list")
    if "vocab" in f:
        if not isinstance(f["vocab"], list) or not all(isinstance(v, str) for v in f["vocab"]):
            raise ApiError(400, "vocab must be a list of strings", "", 'e.g. ["Lynyrd Skynyrd", "Meyer Sound"]')
        f["vocab"] = [v.strip()[:60] for v in f["vocab"] if v.strip()][:60]


@route("GET", "/api/creators")
def creators_list(app, h, q, body):
    return [app.creator_view(c) for c in app.db.query("creators", "SELECT * FROM creators ORDER BY name COLLATE NOCASE")]


@route("POST", "/api/creators")
def creators_create(app, h, q, body):
    f = {k: body[k] for k in CREATOR_FIELDS if k in body}
    _validate_creator(f)
    f.setdefault("platform", "local")
    f.setdefault("source_method", "folder" if f.get("folder_path") else "upload")
    f.setdefault("rights_status", "unknown")
    f.setdefault("categories", [])
    f["created_at"] = dbm.now()
    cid = app.db.insert("creators", f)
    app.db.event("creator", f"Creator added: {f['name']} ({f['rights_status']})", ref={"creator_id": cid})
    return app.creator_view(app.db.get("creators", cid))


@route("PATCH", "/api/creators/:id")
def creators_patch(app, h, q, body, id):
    cid = _int(id, "id")
    if not app.db.get("creators", cid):
        raise ApiError(404, "creator not found")
    f = {k: body[k] for k in CREATOR_FIELDS if k in body}
    _validate_creator(f, partial=True)
    app.db.update("creators", cid, f)
    if "rights_status" in f and body.get("apply_to_sources", True):
        # rights are copied to sources at import; changing the creator's rights updates sources that still mirror it
        app.db.execute("UPDATE sources SET rights_status=? WHERE creator_id=?", (f["rights_status"], cid))
        app.db.event("rights", f"Rights for creator #{cid} set to {f['rights_status']}", ref={"creator_id": cid})
    return app.creator_view(app.db.get("creators", cid))


@route("DELETE", "/api/creators/:id")
def creators_delete(app, h, q, body, id):
    cid = _int(id, "id")
    if not app.db.get("creators", cid):
        raise ApiError(404, "creator not found")
    n = app.db.scalar("SELECT COUNT(*) FROM sources WHERE creator_id=?", (cid,))
    if n:
        raise ApiError(409, "creator has sources", f"{n} source(s) belong to this creator",
                       "re-assign or remove those sources first (ClipForge never deletes your original files)")
    app.db.delete("creators", cid)
    return {"ok": True}


@route("POST", "/api/creators/:id/scan")
def creators_scan(app, h, q, body, id):
    cid = _int(id, "id")
    c = app.db.get("creators", cid)
    if not c:
        raise ApiError(404, "creator not found")
    m = c.get("source_method")
    if m == "folder":
        res = src_folder.scan(app, c)
    elif m == "youtube_api":
        res = src_yt.scan(app, c)
    elif m == "twitch_api":
        res = src_tw.scan(app, c)
    else:
        res = {"status": "blocked", "reason": f"source_method '{m}' has nothing to scan",
               "fix": "set source_method to 'folder' with a folder_path, or use import/upload"}
    app.db.update("creators", cid, {"last_scan_at": dbm.now()})
    return res


# ---------------------------------------------------------------- sources
@route("GET", "/api/sources")
def sources_list(app, h, q, body):
    if q.get("creator_id"):
        rows = app.db.query("sources", "SELECT * FROM sources WHERE creator_id=? ORDER BY id DESC", (_int(q["creator_id"], "creator_id"),))
    else:
        rows = app.db.query("sources", "SELECT * FROM sources ORDER BY id DESC")
    return [app.source_view(s) for s in rows]


@route("POST", "/api/sources/import")
def sources_import(app, h, q, body):
    _need(body, "path", "creator_id")
    try:
        s = src_local.import_path(app, os.path.expanduser(body["path"]), _int(body["creator_id"], "creator_id"),
                                  title=body.get("title"), copy=bool(body.get("copy")))
    except src_local.ImportErr as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)
    return app.source_view(s)


@route("POST", "/api/sources/upload")
def sources_upload(app, h, q, body):
    name = h.headers.get("X-Filename")
    cid = h.headers.get("X-Creator-Id")
    if not name or not cid:
        raise ApiError(400, "missing upload headers", "X-Filename and X-Creator-Id are required", "send both headers with the raw file body")
    try:
        s = src_local.upload(app, h.rfile, int(h.headers.get("Content-Length") or 0), urllib.parse.unquote(name), _int(cid, "X-Creator-Id"))
    except src_local.ImportErr as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)
    return app.source_view(s)


@route("POST", "/api/sources/url")
def sources_url(app, h, q, body):
    _need(body, "url", "creator_id")
    try:
        s = src_ytdlp.import_url(app, body["url"], _int(body["creator_id"], "creator_id"))
    except src_local.ImportErr as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)
    return app.source_view(s)


@route("GET", "/api/sources/:id")
def sources_get(app, h, q, body, id):
    s = app.db.get("sources", _int(id, "id"))
    if not s:
        raise ApiError(404, "source not found")
    return app.source_view(s, deep=True)


@route("PATCH", "/api/sources/:id")
def sources_patch(app, h, q, body, id):
    sid = _int(id, "id")
    s = app.db.get("sources", sid)
    if not s:
        raise ApiError(404, "source not found")
    f = {}
    if "title" in body:
        f["title"] = str(body["title"])[:300]
    if "rights_status" in body:
        if body["rights_status"] not in RIGHTS:
            raise ApiError(400, "invalid rights_status", "", f"use one of {', '.join(RIGHTS)}")
        f["rights_status"] = body["rights_status"]
    if "creator_id" in body:
        if not app.db.get("creators", _int(body["creator_id"], "creator_id")):
            raise ApiError(404, "creator not found")
        f["creator_id"] = int(body["creator_id"])
    app.db.update("sources", sid, f)
    return app.source_view(app.db.get("sources", sid))


# ---------------------------------------------------------------- deletes (DB rows + ClipForge's own media only)
def _rm_keys(app, *keys):
    for k in keys:
        try:
            p = app.storage.path(k)  # refuses anything outside data/media
        except ValueError:
            continue
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            os.remove(p)


def _delete_clip(app, c):
    if c["status"] == "rendering":
        raise ApiError(409, "clip is rendering", f"clip #{c['id']}", "wait for the render to finish")
    _rm_keys(app, f"clips/{c['id']}", f"work/{c['source_id']}/clip_{c['id']}")
    app.db.execute("DELETE FROM metrics WHERE clip_id=?", (c["id"],))
    app.db.execute("UPDATE clips SET variant_of=NULL WHERE variant_of=?", (c["id"],))
    app.db.delete("clips", c["id"])


def _delete_job(app, j):
    if j["status"] in ("queued", "running"):
        raise ApiError(409, f"job is {j['status']}", f"job #{j['id']}", "cancel it first")
    clips = app.db.query("clips", "SELECT * FROM clips WHERE job_id=?", (j["id"],))
    busy = [c["id"] for c in clips if c["status"] == "rendering"]
    if busy:
        raise ApiError(409, "clips still rendering", ", ".join(map(str, busy)), "wait for the renders to finish")
    for c in clips:
        _delete_clip(app, c)
    _rm_keys(app, f"work/{j['source_id']}/job_{j['id']}")
    app.db.delete("jobs", j["id"])


@route("DELETE", "/api/clips/:id")
def clips_delete(app, h, q, body, id):
    c = app.db.get("clips", _int(id, "id"))
    if not c:
        raise ApiError(404, "clip not found")
    _delete_clip(app, c)
    app.db.event("clip", f"Deleted clip #{c['id']} (ClipForge's render and data only)", ref={"clip_id": c["id"]})
    app.db.bus.publish("clip_deleted", {"id": c["id"]})
    return {"ok": True, "deleted": {"clip": c["id"]}}


@route("DELETE", "/api/jobs/:id")
def jobs_delete(app, h, q, body, id):
    j = app.db.get("jobs", _int(id, "id"))
    if not j:
        raise ApiError(404, "job not found")
    n = app.db.scalar("SELECT COUNT(*) FROM clips WHERE job_id=?", (j["id"],))
    _delete_job(app, j)
    app.db.event("job", f"Deleted job #{j['id']} and its {n} clip(s)", ref={"job_id": j["id"]})
    return {"ok": True, "deleted": {"job": j["id"], "clips": n}}


@route("DELETE", "/api/sources/:id")
def sources_delete(app, h, q, body, id):
    s = app.db.get("sources", _int(id, "id"))
    if not s:
        raise ApiError(404, "source not found")
    jobs_ = app.db.query("jobs", "SELECT * FROM jobs WHERE source_id=?", (s["id"],))
    active = [j["id"] for j in jobs_ if j["status"] in ("queued", "running")]
    if active:
        raise ApiError(409, "source is being processed", f"job(s) {active}", "cancel the job first")
    for j in jobs_:
        _delete_job(app, j)
    for c in app.db.query("clips", "SELECT * FROM clips WHERE source_id=?", (s["id"],)):
        _delete_clip(app, c)
    # only ClipForge's own copies/artifacts; an imported original (origin_ref) is never touched
    _rm_keys(app, f"work/{s['id']}", f"sources/{s['id']}", f"uploads/{s['id']}")
    app.db.delete("sources", s["id"])
    app.db.event("source", f"Removed source #{s['id']} \u201c{s['title']}\u201d from ClipForge (original file untouched)",
                 ref={"source_id": s["id"]})
    return {"ok": True, "deleted": {"source": s["id"], "jobs": len(jobs_)}, "original_file_untouched": s.get("origin_ref")}


@route("GET", "/api/sources/:id/timeline")
def sources_timeline(app, h, q, body, id):
    s = app.db.get("sources", _int(id, "id"))
    if not s:
        raise ApiError(404, "source not found")
    return jobsm.timeline(app, s)


# ---------------------------------------------------------------- jobs
@route("POST", "/api/jobs")
def jobs_create(app, h, q, body):
    _need(body, "source_id")
    try:
        j = app.runner.create_job(_int(body["source_id"], "source_id"), body.get("settings") or {})
    except jobsm.JobError as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)
    return app.job_view(j)


@route("GET", "/api/jobs")
def jobs_list(app, h, q, body):
    args, where = [], []
    if q.get("status"):
        sts = q["status"].split(",")
        where.append("status IN (%s)" % ",".join("?" * len(sts)))
        args += sts
    if q.get("source_id"):
        where.append("source_id=?")
        args.append(_int(q["source_id"], "source_id"))
    sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT 200"
    out = []
    for j in app.db.query("jobs", sql, tuple(args)):
        j = app.job_view(j)
        s = app.db.get("sources", j["source_id"])
        j["source_title"] = s["title"] if s else None
        out.append(j)
    return out


@route("GET", "/api/jobs/:id")
def jobs_get(app, h, q, body, id):
    j = app.db.get("jobs", _int(id, "id"))
    if not j:
        raise ApiError(404, "job not found")
    j = app.job_view(j)
    s = app.db.get("sources", j["source_id"])
    j["source_title"] = s["title"] if s else None
    return j


@route("POST", "/api/jobs/:id/retry")
def jobs_retry(app, h, q, body, id):
    try:
        return app.job_view(app.runner.retry(_int(id, "id")))
    except jobsm.JobError as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)


@route("POST", "/api/jobs/:id/cancel")
def jobs_cancel(app, h, q, body, id):
    try:
        return app.job_view(app.runner.cancel(_int(id, "id")))
    except jobsm.JobError as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)


# ---------------------------------------------------------------- events
@route("GET", "/api/events")
def events_list(app, h, q, body):
    since = q.get("since")
    if since and since.isdigit():
        return app.db.query("events", "SELECT * FROM events WHERE id>? ORDER BY id ASC LIMIT 500", (int(since),))
    if since:
        return app.db.query("events", "SELECT * FROM events WHERE ts>? ORDER BY id ASC LIMIT 500", (since,))
    return list(reversed(app.db.query("events", "SELECT * FROM events ORDER BY id DESC LIMIT 100")))


# ---------------------------------------------------------------- clips
@route("GET", "/api/clips")
def clips_list(app, h, q, body):
    where, args = [], []
    if q.get("source_id"):
        where.append("c.source_id=?")
        args.append(_int(q["source_id"], "source_id"))
    if q.get("job_id"):
        where.append("c.job_id=?")
        args.append(_int(q["job_id"], "job_id"))
    if q.get("creator_id"):
        where.append("s.creator_id=?")
        args.append(_int(q["creator_id"], "creator_id"))
    st = q.get("status")
    if st and st != "all":
        sts = st.split(",")
        where.append("c.status IN (%s)" % ",".join("?" * len(sts)))
        args += sts
    elif not st:
        where.append("c.status!='rejected'")
    if q.get("min_score"):
        where.append("c.overall>=?")
        args.append(float(q["min_score"]))
    if q.get("max_dur"):
        where.append("(c.end_s-c.start_s)<=?")
        args.append(float(q["max_dur"]))
    if q.get("min_dur"):
        where.append("(c.end_s-c.start_s)>=?")
        args.append(float(q["min_dur"]))
    if q.get("q"):
        where.append("(c.title LIKE ? OR c.description LIKE ? OR c.transcript_excerpt LIKE ? OR c.hook LIKE ?)")
        like = f"%{q['q']}%"
        args += [like] * 4
    order = {"score": "c.overall DESC", "time": "c.source_id DESC, c.start_s ASC", "duration": "(c.end_s-c.start_s) ASC",
             "rank": "c.rank ASC", "new": "c.id DESC"}.get(q.get("sort") or "score", "c.overall DESC")
    sql = ("SELECT c.* FROM clips c LEFT JOIN sources s ON s.id=c.source_id" + (" WHERE " + " AND ".join(where) if where else "")
           + f" ORDER BY {order} LIMIT 500")
    return [app.clip_view(c) for c in app.db.query("clips", sql, tuple(args))]


@route("GET", "/api/clips/:id")
def clips_get(app, h, q, body, id):
    c = app.db.get("clips", _int(id, "id"))
    if not c:
        raise ApiError(404, "clip not found")
    c = app.clip_view(c)
    c["words"] = jobsm.words_in_range(app, c["source_id"], c["start_s"] - 5, c["end_s"] + 5)
    c["variants"] = [x["id"] for x in app.db.query("clips", "SELECT id FROM clips WHERE variant_of=?", (c["id"],))]
    c["metrics"] = app.db.query("metrics", "SELECT * FROM metrics WHERE clip_id=? ORDER BY id DESC", (c["id"],))
    return c


@route("PATCH", "/api/clips/:id")
def clips_patch(app, h, q, body, id):
    try:
        return app.clip_view(app.runner.patch_clip(_int(id, "id"), body))
    except jobsm.JobError as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)


@route("POST", "/api/clips/:id/render")
def clips_render(app, h, q, body, id):
    try:
        return app.clip_view(app.runner.request_render(_int(id, "id")))
    except jobsm.JobError as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)


@route("POST", "/api/clips/:id/rehook")
def clips_rehook(app, h, q, body, id):
    """Fast path for hook-text edits (seconds). Falls back to a normal background render when it can't."""
    import pipeline
    cid = _int(id, "id")
    view = pipeline.rehook_clip(app, cid)
    if view is not None:
        return dict(view, fast=True)
    try:
        return dict(app.clip_view(app.runner.request_render(cid)), fast=False)
    except jobsm.JobError as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)


@route("POST", "/api/clips/:id/rescore")
def clips_rescore(app, h, q, body, id):
    import pipeline
    try:
        return app.clip_view(pipeline.rescore(app, _int(id, "id")))
    except jobsm.JobError as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)


@route("POST", "/api/clips/:id/variants")
def clips_variants(app, h, q, body, id):
    try:
        return app.runner.request_variants(_int(id, "id"))
    except jobsm.JobError as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)


@route("POST", "/api/clips/:id/metrics")
def clips_metrics(app, h, q, body, id):
    cid = _int(id, "id")
    if not app.db.get("clips", cid):
        raise ApiError(404, "clip not found")
    _need(body, "platform")
    f = {"clip_id": cid, "platform": str(body["platform"])[:40], "recorded_at": body.get("recorded_at") or dbm.now(), "source": "manual"}
    for k in ("views", "likes", "comments", "shares"):
        if body.get(k) not in (None, ""):
            f[k] = _int(body[k], k)
    if body.get("avg_watch_pct") not in (None, ""):
        f["avg_watch_pct"] = float(body["avg_watch_pct"])
    mid = app.db.insert("metrics", f)
    return app.db.get("metrics", mid)


# ---------------------------------------------------------------- export / analytics
@route("POST", "/api/export")
def export(app, h, q, body):
    ids = body.get("clip_ids") or []
    if not ids:
        raise ApiError(400, "no clips selected", "", "pass clip_ids")
    try:
        return exporter.export(app, [_int(i, "clip_id") for i in ids], body.get("dir"))
    except exporter.ExportBlocked as e:
        raise ApiError(e.status, e.error, e.detail, e.fix)


@route("GET", "/api/exports")
def exports_list(app, h, q, body):
    return app.db.query("exports", "SELECT * FROM exports ORDER BY id DESC LIMIT 100")


@route("GET", "/api/analytics")
def analytics(app, h, q, body):
    return exporter.analytics(app)


# ---------------------------------------------------------------- settings / integrations / publish
@route("GET", "/api/settings")
def settings_get(app, h, q, body):
    s = app.settings()
    s["integrations"] = integrations.key_status()
    s["storage_status"] = storagem.status()
    s["whisper_models_cached"] = jobsm.cached_whisper_models()
    s["presets"] = jobsm.preset_names()
    return s


@route("PATCH", "/api/settings")
def settings_patch(app, h, q, body):
    allowed = set(config.DEFAULT_SETTINGS)
    bad = [k for k in body if k not in allowed]
    if bad:
        raise ApiError(400, "unknown setting", ", ".join(bad), f"settable keys: {', '.join(sorted(allowed))}")
    cur = app.settings()
    for k, v in body.items():
        if isinstance(cur.get(k), dict) and isinstance(v, dict):
            merged = dict(cur[k])
            merged.update(v)
            v = merged
        if k in ("whisper_model", "whisper_model_short") and v not in jobsm.cached_whisper_models():
            raise ApiError(400, "whisper model not cached", v, f"use one of {', '.join(jobsm.cached_whisper_models())}")
        if k == "llm" and v.get("provider") not in ("claude_cli", "anthropic_api"):
            raise ApiError(400, "unknown llm provider", str(v.get("provider")), "use claude_cli or anthropic_api")
        app.db.set_setting(k, v)
    return settings_get(app, h, q, {})


@route("GET", "/api/integrations")
def integrations_list(app, h, q, body):
    return integrations.list_all()


@route("POST", "/api/publish/:clip_id")
def publish(app, h, q, body, clip_id):
    cid = _int(clip_id, "clip_id")
    if not app.db.get("clips", cid):
        raise ApiError(404, "clip not found")
    if not body.get("platform"):
        raise ApiError(400, "missing platform", "", f"pass platform: one of {', '.join(integrations.PUBLISHERS)}")
    res = integrations.publish(body.get("platform"), cid)
    raise ApiError(501, "publishing blocked", res["detail"], res["fix"])


@route("GET", "/api/presets")
def presets(app, h, q, body):
    return jobsm.load_presets()


# ====================================================================================== handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ClipForge/" + VERSION
    app = None

    def log_message(self, fmt, *args):  # quiet access log; errors go to the events table / stderr
        pass

    # CORS for the VI Clips app only: the app runs as a local file (Origin "null") or on a localhost port and calls
    # this engine directly. Any other website's origin gets no CORS header, so the browser keeps blocking it.
    _CORS_OK = ("null", "http://127.0.0.1", "http://localhost")

    def end_headers(self):
        origin = self.headers.get("Origin") if self.headers else None
        if origin and (origin == "null" or any(origin == o or origin.startswith(o + ":") for o in self._CORS_OK[1:])):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename, X-Creator-Id, Range")
            self.send_header("Access-Control-Expose-Headers", "Content-Range, Accept-Ranges, Content-Length")
        super().end_headers()

    def _send_json(self, status, obj):
        data = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _dispatch(self, method):
        u = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(u.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if path == "/api/stream":
                return self._sse()
            if path.startswith("/media/"):
                return self._media(path[len("/media/"):])
            if path.startswith("/api/"):
                for m, rx, fn in ROUTES:
                    if m != method:
                        continue
                    mt = rx.match(path)
                    if mt:
                        body = {}
                        if method in ("POST", "PATCH", "PUT", "DELETE") and fn is not sources_upload:
                            n = int(self.headers.get("Content-Length") or 0)
                            if n:
                                raw = self.rfile.read(n)
                                try:
                                    body = json.loads(raw or b"{}")
                                except ValueError:
                                    raise ApiError(400, "body is not valid JSON")
                                if not isinstance(body, dict):
                                    raise ApiError(400, "body must be a JSON object")
                        res = fn(self.app, self, q, body, **mt.groupdict())
                        return self._send_json(200, res)
                raise ApiError(404, "no such endpoint", f"{method} {path}", "see prompts/CONTRACT.md for the API")
            if method in ("GET", "HEAD"):
                return self._static(path)
            raise ApiError(405, "method not allowed")
        except ApiError as e:
            self._send_json(e.status, {"error": e.error, "detail": e.detail, "fix": e.fix})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            try:
                self._send_json(500, {"error": "internal error", "detail": f"{type(e).__name__}: {e}", "fix": "check data/logs/engine.log"})
            except Exception:
                pass

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------------------------------------------------------- static + media
    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        p = os.path.normpath(os.path.join(UI, path.lstrip("/")))
        if not p.startswith(os.path.normpath(UI)):
            raise ApiError(403, "forbidden")
        if os.path.isdir(p):
            p = os.path.join(p, "index.html")
        if not os.path.isfile(p):
            if not os.path.exists(os.path.join(UI, "index.html")):
                raise ApiError(404, "UI not built yet", f"{UI}/index.html is missing", "the studio UI lives in ui/")
            raise ApiError(404, "not found", path)
        self._file(p, cache="no-cache")

    def _media(self, key):
        if key.startswith("origin/"):
            sid = _int(key.split("/", 1)[1], "source id")
            s = self.app.db.get("sources", sid)
            if not s:
                raise ApiError(404, "source not found")
            p = self.app.source_path(s)
        else:
            try:
                p = self.app.storage.path(key)
            except ValueError:
                raise ApiError(403, "forbidden")
        if not p or not os.path.isfile(p):
            raise ApiError(404, "media not found", key)
        self._file(p)

    def _file(self, path, cache="max-age=60"):
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith((".mov", ".MOV")):
            ctype = "video/quicktime"
        rng = self.headers.get("Range")
        a, b = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if not m or (m.group(1) == "" and m.group(2) == ""):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if m.group(1) == "":
                a = max(0, size - int(m.group(2)))
            else:
                a = int(m.group(1))
                if m.group(2):
                    b = min(int(m.group(2)), size - 1)
            if a >= size or a > b:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        n = b - a + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(n))
        self.send_header("Cache-Control", cache)
        if rng:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(a)
            left = n
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # ---------------------------------------------------------------- SSE
    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.app.db.bus.subscribe()
        try:
            hello = {"type": "hello", "ts": dbm.now(), "data": {
                "version": VERSION,
                "jobs": [self.app.job_view(j) for j in self.app.db.query("jobs", "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id")],
                "last_event_id": self.app.db.scalar("SELECT MAX(id) FROM events") or 0}}
            self.wfile.write(b"retry: 3000\n" + self._sse_frame(hello))
            self.wfile.flush()
            last = time.time()
            while not self.app.runner.stopping.is_set():
                try:
                    msg = q.get(timeout=1.0)
                    self.wfile.write(self._sse_frame(msg))
                    self.wfile.flush()
                    last = time.time()
                except queue.Empty:
                    if time.time() - last > 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.db.bus.unsubscribe(q)
            self.close_connection = True

    @staticmethod
    def _sse_frame(msg):
        return f"event: {msg['type']}\ndata: {json.dumps(msg, default=str)}\n\n".encode()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return  # client went away (video scrubbing, closed tab) - not an engine error
        super().handle_error(request, client_address)


def serve(host=None, port=None, data_dir=None):
    app = App(data_dir=data_dir)
    Handler.app = app
    srv = Server((host or config.HOST, port or config.PORT), Handler)

    def _stop(signum, frame):
        app.runner.shutdown()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    app.db.event("engine", f"Engine started (v{VERSION}, pid {os.getpid()})")
    integrations.warm()
    print(f"ClipForge engine on http://{srv.server_address[0]}:{srv.server_address[1]}  data={app.db.path}", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        app.runner.shutdown()
        media.kill_all_children()
    return app


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--data", default=None)
    a = ap.parse_args()
    if a.data:
        os.environ["CLIPFORGE_DATA"] = a.data
    serve(a.host, a.port, a.data)

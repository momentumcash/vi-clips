"""Export (copy rendered clips + .srt + .json to the export dir (VI_EXPORT_DIR, default ~/Movies/VI Clips)/<date>/) and real-only analytics."""
import datetime as _dt
import json
import os
import re
import shutil

import db as dbm
from config import EXPORT_DIR, EXPORT_RIGHTS


class ExportBlocked(Exception):
    def __init__(self, status, error, detail="", fix=""):
        super().__init__(error)
        self.status, self.error, self.detail, self.fix = status, error, detail, fix


def _ts(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)) % 1000:03d}"


def srt(captions):
    out = []
    for i, c in enumerate(captions or [], 1):
        out.append(f"{i}\n{_ts(c['start'])} --> {_ts(c['end'])}\n{c['text']}\n")
    return "\n".join(out)


def _slug(s):
    s = re.sub(r"[^\w\- ]+", "", s or "clip").strip().replace(" ", "-")
    return s[:50] or "clip"


def _free(path):
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{base}-{n}{ext}"
        n += 1
    return path


def export(app, clip_ids, dir_=None):
    clips = []
    blocked = []
    for cid in clip_ids:
        c = app.db.get("clips", cid)
        if not c:
            raise ExportBlocked(404, "clip not found", f"id {cid}")
        c = app.clip_view(c)
        if c["status"] not in ("rendered", "approved", "exported") or not c.get("file_key") or not app.storage.exists(c["file_key"]):
            blocked.append({"clip_id": cid, "reason": f"not rendered (status {c['status']})"})
            continue
        if not (c.get("qc") or {}).get("pass"):
            blocked.append({"clip_id": cid, "reason": "QC did not pass"})
            continue
        if c["rights_status"] not in EXPORT_RIGHTS:
            blocked.append({"clip_id": cid, "reason": f"rights_status is '{c['rights_status']}'"})
            continue
        clips.append(c)
    if blocked:
        rights = [b for b in blocked if "rights" in b["reason"]]
        raise ExportBlocked(403 if rights else 409, "export blocked",
                            "; ".join(f"clip {b['clip_id']}: {b['reason']}" for b in blocked),
                            "set the creator/source rights to owned, licensed or permission_granted" if rights
                            else "render the clip (and pass QC) before exporting")
    day = _dt.date.today().isoformat()
    out_dir = os.path.expanduser(dir_) if dir_ else os.path.join(EXPORT_DIR, day)
    os.makedirs(out_dir, exist_ok=True)
    files = []
    for c in clips:
        stem = f"s{c['source_id']}-{c['rank'] or 0:02d}_{_slug(c['title'])}_c{c['id']}"
        mp4 = _free(os.path.join(out_dir, stem + ".mp4"))
        shutil.copy2(app.storage.path(c["file_key"]), mp4)
        stem_out = os.path.splitext(mp4)[0]
        with open(stem_out + ".srt", "w") as f:
            f.write(srt(c.get("captions")))
        meta = {k: c.get(k) for k in ("id", "title", "description", "hook", "payoff", "category", "overall", "scores", "reasons",
                                       "score_basis", "start_s", "end_s", "duration", "source_title", "creator_name",
                                       "rights_status", "concerns", "caption_preset", "qc")}
        meta["attribution"] = f"Source: {c['creator_name']} — {c['source_title']}"
        meta["score_note"] = "ClipForge score — editorial estimate, not a view prediction"
        with open(stem_out + ".json", "w") as f:
            json.dump(meta, f, indent=2)
        files.append({"clip_id": c["id"], "mp4": mp4, "srt": stem_out + ".srt", "json": stem_out + ".json"})
        app.db.update("clips", c["id"], {"status": "exported", "updated_at": dbm.now()})
    eid = app.db.insert("exports", {"clip_ids": [c["id"] for c in clips], "dir": out_dir, "files": files, "created_at": dbm.now()})
    app.db.event("export", f"Exported {len(clips)} clip(s) to {out_dir}", ref={"export_id": eid, "dir": out_dir})
    app.db.bus.publish("export", {"id": eid, "dir": out_dir, "files": files})
    return app.db.get("exports", eid)


def _band(d):
    return "<30s" if d < 30 else ("30-60s" if d < 60 else "60s+")


def analytics(app):
    db = app.db
    by_status = {r["status"]: r["n"] for r in db.query("clips", "SELECT status, COUNT(*) AS n FROM clips GROUP BY status")}
    rows = db.query("clips", """
        SELECT m.*, c.overall, c.category, c.caption_preset, c.title, (c.end_s-c.start_s) AS dur, cr.name AS creator
        FROM metrics m JOIN clips c ON c.id=m.clip_id LEFT JOIN sources s ON s.id=c.source_id
        LEFT JOIN creators cr ON cr.id=s.creator_id ORDER BY m.id DESC""")
    groups = {"creator": {}, "category": {}, "duration": {}, "preset": {}}
    for r in rows:
        for g, key in (("creator", r["creator"]), ("category", r["category"]), ("duration", _band(r["dur"] or 0)),
                       ("preset", r["caption_preset"])):
            a = groups[g].setdefault(key or "—", {"clips": 0, "views": 0, "likes": 0})
            a["clips"] += 1
            a["views"] += r["views"] or 0
            a["likes"] += r["likes"] or 0
    pairs = [{"clip_id": r["clip_id"], "title": r["title"], "predicted_score": r["overall"], "views": r["views"],
              "avg_watch_pct": r["avg_watch_pct"], "platform": r["platform"]} for r in rows]
    return {
        "exports": db.scalar("SELECT COUNT(*) FROM exports"),
        "clips_exported": db.scalar("SELECT COUNT(*) FROM clips WHERE status='exported'"),
        "clips_by_status": by_status,
        "metrics_count": len(rows),
        "by": groups,
        "predicted_vs_actual": pairs,
        "note": "Only real data: metrics are entered manually (no platform is connected)." if not rows else "",
    }

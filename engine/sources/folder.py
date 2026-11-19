"""Watched folder: import every video in the creator's folder that isn't imported yet (referenced in place)."""
import os

from config import VIDEO_EXT
from sources.local import import_path, ImportErr


def scan(app, creator, auto_process=None):
    folder = creator.get("folder_path")
    if not folder or not os.path.isdir(folder):
        return {"status": "blocked", "reason": "folder_path missing or not found", "fix": "set an existing folder on the creator"}
    known = {r["origin_ref"] for r in app.db.query("sources", "SELECT origin_ref FROM sources WHERE creator_id=?", (creator["id"],))}
    added, errors = [], []
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if name.startswith(".") or not os.path.isfile(p) or not name.lower().endswith(VIDEO_EXT) or p in known:
            continue
        try:
            s = import_path(app, p, creator["id"])
            added.append(s["id"])
        except ImportErr as e:
            errors.append({"file": name, "error": e.error, "detail": e.detail})
    settings = app.settings()
    auto = settings["automation"]["auto_process"] if auto_process is None else auto_process
    jobs = []
    if auto and added:
        limit = int(settings["automation"].get("daily_limit") or 20)
        for sid in added[:limit]:
            jobs.append(app.runner.create_job(sid, {})["id"])
    app.db.event("scan", f"Scanned {os.path.basename(folder)}: {len(added)} new video(s)", ref={"creator_id": creator["id"]})
    return {"status": "ok", "added": added, "errors": errors, "jobs": jobs}

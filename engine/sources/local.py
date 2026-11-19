"""Local files: import by path (referenced in place by default — never moved) and raw-body uploads (copied)."""
import os
import re

import db as dbm
import media
from config import VIDEO_EXT


class ImportErr(Exception):
    def __init__(self, status, error, detail="", fix=""):
        super().__init__(error)
        self.status, self.error, self.detail, self.fix = status, error, detail, fix


def _creator(app, creator_id):
    c = app.db.get("creators", creator_id)
    if not c:
        raise ImportErr(404, "creator not found", f"id {creator_id}", "create the creator first (POST /api/creators)")
    return c


def make_source_thumb(app, sid, path, pr):
    key = f"sources/{sid}/thumb.jpg"
    out = app.storage.path(key)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t = min(max(1.0, (pr.get("duration") or 10) * 0.1), max(0.0, (pr.get("duration") or 1) - 0.5))
    chain = "scale=640:360:force_original_aspect_ratio=decrease:force_divisible_by=2"
    sdr = media.sdr_filter(pr)
    if sdr:
        chain += "," + sdr
    chain += ",pad=640:360:(ow-iw)/2:(oh-ih)/2:color=black"
    tmp = out + ".part.jpg"
    media.ffmpeg(["-ss", f"{t:.2f}", "-i", path, "-map", "0:v:0", "-frames:v", "1", "-vf", chain, "-q:v", "4", tmp])
    os.replace(tmp, out)
    return key


def fingerprint(path):
    """size + SHA-1 of the first and last MiB — identifies the same file at a different path without reading it all."""
    import hashlib
    size = os.path.getsize(path)
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > (2 << 20):
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    return f"{size}:{h.hexdigest()[:20]}"


def find_duplicate(app, fp, creator_id):
    """Same file (by fingerprint) already imported for this creator, at any path."""
    rows = app.db.query("sources", "SELECT * FROM sources WHERE fingerprint=? AND creator_id=? ORDER BY id LIMIT 1", (fp, creator_id))
    if not rows:
        return None
    app.db.event("source", f"Same file already imported as source #{rows[0]['id']} — reusing it", ref={"source_id": rows[0]["id"]})
    return dict(rows[0], already_imported=True)


def create_source(app, creator, path, origin, origin_ref, title=None, file_key=None):
    try:
        pr = media.probe(path)
    except media.MediaError as e:
        raise ImportErr(422, "not a readable video", str(e), "pick a video file ffmpeg can open (mp4/mov/mkv)")
    if not pr.get("duration"):
        raise ImportErr(422, "could not read duration", path, "re-export or remux the file")
    sid = app.db.insert("sources", {
        "creator_id": creator["id"], "title": title or os.path.splitext(os.path.basename(path))[0],
        "origin": origin, "origin_ref": origin_ref, "file_key": file_key,
        "duration_s": round(pr["duration"], 3), "width": pr["width"], "height": pr["height"], "fps": pr["fps"],
        "has_audio": 1 if pr["has_audio"] else 0, "status": "ready", "rights_status": creator.get("rights_status") or "unknown",
        "fingerprint": fingerprint(path), "created_at": dbm.now()})
    if not file_key:
        app.db.update("sources", sid, {"file_key": f"origin/{sid}"})
    try:
        app.db.update("sources", sid, {"thumb_key": make_source_thumb(app, sid, path, pr)})
    except media.MediaError as e:
        app.db.event("source", f"Thumbnail failed for source #{sid}: {e}", level="warn", ref={"source_id": sid})
    app.db.event("source", f"Imported “{title or os.path.basename(path)}” ({pr['duration']/60:.1f} min, {pr['width']}x{pr['height']})"
                 + ("" if pr["has_audio"] else " — no decodable audio"), ref={"source_id": sid, "creator_id": creator["id"]})
    return app.db.get("sources", sid)


def import_path(app, path, creator_id, title=None, copy=False):
    c = _creator(app, creator_id)
    path = os.path.abspath(path)
    if os.path.isdir(path):
        raise ImportErr(400, "that's a folder, not a video", path,
                        "import one video file, or set this folder as the creator's watched folder (folder_path) and scan it")
    if not os.path.isfile(path):
        raise ImportErr(404, "file not found", path, "check the path (drag the file into Terminal to copy its path)")
    if not path.lower().endswith(VIDEO_EXT):
        raise ImportErr(415, "not a video file", os.path.basename(path), f"supported: {', '.join(VIDEO_EXT)}")
    existing = app.db.query("sources", "SELECT * FROM sources WHERE origin_ref=? AND creator_id=?", (path, creator_id))
    if existing:
        return dict(existing[0], already_imported=True)
    dup = find_duplicate(app, fingerprint(path), creator_id)
    if dup:
        return dup
    if copy:
        s = create_source(app, c, path, "upload", path, title)
        key = f"uploads/{s['id']}/{os.path.basename(path)}"
        app.storage.put_file(path, key)
        app.db.update("sources", s["id"], {"file_key": key})
        return app.db.get("sources", s["id"])
    return create_source(app, c, path, "folder", path, title)


def _safe_name(name):
    name = os.path.basename(name).strip() or "upload.mp4"
    return re.sub(r"[^\w.\- ()]+", "_", name)[:120]


def upload(app, rfile, length, filename, creator_id):
    c = _creator(app, creator_id)
    if length <= 0:
        raise ImportErr(411, "empty upload", "Content-Length missing or 0", "send the file as the raw request body")
    name = _safe_name(filename)
    if not name.lower().endswith(VIDEO_EXT):
        raise ImportErr(415, "not a video file", name, f"supported: {', '.join(VIDEO_EXT)}")
    tmp_key = f"uploads/_incoming/{os.getpid()}_{dbm.now().replace(':', '')}_{name}"
    tmp = app.storage.path(tmp_key)
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    left = length
    with open(tmp, "wb") as f:
        while left > 0:
            chunk = rfile.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        os.remove(tmp)
        raise ImportErr(400, "upload interrupted", f"{left} bytes missing", "retry the upload")
    dup = find_duplicate(app, fingerprint(tmp), creator_id)
    if dup:
        os.remove(tmp)  # our own temp copy inside data/
        return dup
    try:
        s = create_source(app, c, tmp, "upload", name)
    except ImportErr:
        os.remove(tmp)
        raise
    key = f"uploads/{s['id']}/{name}"
    dst = app.storage.path(key)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(tmp, dst)  # inside data/ only
    app.db.update("sources", s["id"], {"file_key": key, "origin_ref": name})
    return app.db.get("sources", s["id"])

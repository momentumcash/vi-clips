"""URL import through yt-dlp — ONLY for creators whose rights are owned / licensed / permission_granted.
Anything else is refused with 403 and the reason. The download lands in data/media/uploads/<source_id>/."""
import os
import shutil
import subprocess

import db as dbm
import media
from config import EXPORT_RIGHTS
from sources.local import ImportErr, create_source


def ytdlp_bin():
    return shutil.which("yt-dlp") or ("/opt/homebrew/bin/yt-dlp" if os.path.exists("/opt/homebrew/bin/yt-dlp") else None)


def status():
    b = ytdlp_bin()
    return {"name": "ytdlp_authorized", "kind": "discovery", "status": "ready" if b else "blocked",
            "needs": [] if b else ["yt-dlp on PATH"],
            "doc": "Downloads only for creators with rights owned / licensed / permission_granted."}


def check_rights(creator):
    r = creator.get("rights_status")
    if r not in EXPORT_RIGHTS:
        raise ImportErr(403, "rights not cleared for download",
                        f"creator “{creator['name']}” has rights_status '{r}'",
                        "set the creator's rights to owned, licensed or permission_granted (with a note of who granted it) first")


def import_url(app, url, creator_id):
    c = app.db.get("creators", creator_id)
    if not c:
        raise ImportErr(404, "creator not found")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ImportErr(400, "not a URL", url, "pass an http(s) URL")
    check_rights(c)
    b = ytdlp_bin()
    if not b:
        raise ImportErr(503, "yt-dlp not installed", "", "install yt-dlp")
    existing = app.db.query("sources", "SELECT * FROM sources WHERE origin_ref=? AND creator_id=?", (url, creator_id))
    if existing:
        return existing[0]
    stamp = dbm.now().replace(":", "").replace(".", "")
    tmpdir = app.storage.ensure_dir(f"uploads/_incoming/url_{stamp}")
    cmd = [b, "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
           "--ffmpeg-location", media.FF, "-o", os.path.join(tmpdir, "%(title).80s.%(ext)s"), url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        raise ImportErr(504, "download timed out", url, "try again or download it yourself and import the file")
    files = [f for f in os.listdir(tmpdir) if not f.endswith(".part")]
    if r.returncode != 0 or not files:
        raise ImportErr(502, "yt-dlp failed", (r.stderr or r.stdout).strip()[-400:], "check the URL is reachable and public to you")
    path = os.path.join(tmpdir, files[0])
    s = create_source(app, c, path, "url", url, title=os.path.splitext(files[0])[0])
    key = f"uploads/{s['id']}/{files[0]}"
    dst = app.storage.path(key)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(path, dst)
    shutil.rmtree(tmpdir, ignore_errors=True)  # our own temp dir inside data/
    app.db.update("sources", s["id"], {"file_key": key})
    return app.db.get("sources", s["id"])

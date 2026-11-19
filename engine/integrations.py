"""Integration registry: discovery sources + publishers + LLM + storage, each with ready/blocked and what's missing.
Secret values are never returned — only which variables are set or missing."""
import os
import shutil

from publish.tiktok import TikTok
from publish.youtube import YouTube
from publish.instagram import Instagram
from sources import youtube_api, twitch_api, ytdlp_authorized

PUBLISHERS = {"tiktok": TikTok(), "youtube": YouTube(), "instagram": Instagram()}
ENV_KEYS = ["ANTHROPIC_API_KEY", "CLIPFORGE_LLM", "YOUTUBE_API_KEY", "TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET",
            "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_ACCESS_TOKEN", "YOUTUBE_OAUTH_CLIENT_ID",
            "YOUTUBE_OAUTH_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN", "INSTAGRAM_BUSINESS_ACCOUNT_ID", "INSTAGRAM_ACCESS_TOKEN",
            "S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]


def key_status():
    return {k: ("set" if os.environ.get(k) else "missing") for k in ENV_KEYS}


_LOGIN = {"t": 0.0, "ok": None, "detail": ""}
_LOGIN_LOCK = __import__("threading").Lock()
LOGIN_TTL = 600


def _check_login(cli):
    import getpass
    import json as _json
    import subprocess
    import time
    env = dict(os.environ)
    env.setdefault("USER", getpass.getuser())
    env.setdefault("LOGNAME", env["USER"])
    try:
        r = subprocess.run([cli, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
                            "--strict-mcp-config", "--setting-sources", "", "ok"], capture_output=True, text=True, timeout=90,
                           env=env, cwd="/tmp", stdin=subprocess.DEVNULL)
        j = _json.loads(r.stdout)
        ok = not j.get("is_error")
        detail = "logged in" if ok else str(j.get("result"))[:200]
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"login check failed: {e}"[:200]
    _LOGIN.update(t=time.time(), ok=ok, detail=detail)


def claude_login_ok(cli):
    """`claude -p ok` proves the CLI is logged in (a binary on PATH is not enough). Cached 10 min; a stale cache is
    served while a background refresh runs; only the very first check (warmed at engine start) is waited for."""
    import threading
    import time
    if _LOGIN["ok"] is None:
        with _LOGIN_LOCK:
            if _LOGIN["ok"] is None:
                _check_login(cli)
    elif time.time() - _LOGIN["t"] > LOGIN_TTL and _LOGIN_LOCK.acquire(blocking=False):
        def bg():
            try:
                _check_login(cli)
            finally:
                _LOGIN_LOCK.release()
        threading.Thread(target=bg, daemon=True).start()
    return _LOGIN["ok"], _LOGIN["detail"]


def warm():
    import threading
    cli = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if os.path.exists(cli):
        threading.Thread(target=claude_login_ok, args=(cli,), daemon=True).start()


def llm_status():
    cli = shutil.which("claude") or (os.path.expanduser("~/.local/bin/claude") if os.path.exists(os.path.expanduser("~/.local/bin/claude")) else None)
    ok, detail = claude_login_ok(cli) if cli else (False, "claude CLI not found")
    out = [{"name": "claude_cli", "kind": "llm", "status": "ready" if ok else "blocked",
            "needs": [] if ok else ["claude CLI on PATH, logged in (run `claude` once and sign in)"], "detail": detail,
            "doc": "Uses `claude -p` with your Claude Code login (checked with `claude -p ok`, cached 10 min); cost is reported per job."},
           {"name": "anthropic_api", "kind": "llm", "status": "ready" if os.environ.get("ANTHROPIC_API_KEY") else "blocked",
            "needs": [] if os.environ.get("ANTHROPIC_API_KEY") else ["ANTHROPIC_API_KEY"],
            "doc": "Direct Messages API (stdlib urllib), used when ANTHROPIC_API_KEY is set and the provider is anthropic_api."}]
    return out


def list_all():
    out = [{"name": "local_files", "kind": "discovery", "status": "ready", "needs": [], "doc": "Import a path, upload, or watch a folder."},
           youtube_api.status(), twitch_api.status(), ytdlp_authorized.status()]
    out += [p.status() for p in PUBLISHERS.values()]
    out += llm_status()
    s3_missing = [k for k in ("S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY") if not os.environ.get(k)]
    out.append({"name": "s3_storage", "kind": "storage", "status": "blocked", "needs": s3_missing or ["S3 upload implementation"],
                "doc": "Local storage under data/media is in use; S3 is an interface only."})
    return out


def publish(platform, clip_id):
    p = PUBLISHERS.get(platform or "")
    if not p:
        return {"status": "blocked", "detail": f"unknown or missing platform {platform!r}",
                "fix": f"pass platform: one of {', '.join(PUBLISHERS)} — note none are connected in this build"}
    return p.publish(clip_id)

"""Shared settings for the VI Clips app server. Everything can be overridden with environment variables.

  VI_ENGINE_PORT   clip engine port            (default 8870)
  VI_SERVER_PORT   this app server's port      (default 8885)
  VI_DATA          where all runtime data goes (default <repo>/data)
  VI_EXPORT_DIR    where exported clips go     (default ~/Movies/VI Clips)
  VI_BROWSER       browser yt-dlp reads cookies from for live clipping when no cookie file exists (default chrome)
"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_PORT = int(os.environ.get("VI_ENGINE_PORT") or 8870)
SERVER_PORT = int(os.environ.get("VI_SERVER_PORT") or 8885)
ENGINE_URL = f"http://127.0.0.1:{ENGINE_PORT}"
DATA = os.path.expanduser(os.environ.get("VI_DATA") or os.path.join(ROOT, "data"))
APP_DATA = os.path.join(DATA, "app")          # feedback.json, knowledge.json, research.json, live/
ENGINE_DATA = os.environ.get("CLIPFORGE_DATA") or os.path.join(DATA, "engine")  # the engine's DB + media
EXPORT_DIR = os.path.expanduser(os.environ.get("VI_EXPORT_DIR") or "~/Movies/VI Clips")
DETECT_PROMPT = os.path.join(ROOT, "engine", "prompts", "detect.md")
COOKIES = os.path.join(APP_DATA, "yt-cookies.txt")
BROWSER = os.environ.get("VI_BROWSER") or "chrome"
APP_DIR = os.path.join(ROOT, "app")


def ffmpeg():
    """Homebrew ffmpeg if installed, else the one bundled with imageio-ffmpeg (in the venv)."""
    found = shutil.which("ffmpeg") or next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg") if os.path.exists(p)), None)
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def ytdlp():
    return shutil.which("yt-dlp") or next((p for p in ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp") if os.path.exists(p)), "yt-dlp")


os.makedirs(APP_DATA, exist_ok=True)

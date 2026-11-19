"""ClipForge engine — paths and constants shared by every module."""
import os

VERSION = "0.1.0"
ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
DATA = (os.environ.get("CLIPFORGE_DATA")
        or os.path.join(os.path.expanduser(os.environ.get("VI_DATA") or os.path.join(ROOT, "data")), "engine"))
UI = os.path.join(ROOT, "studio")
FONTS = os.path.join(ENGINE, "fonts")
PROMPTS = os.path.join(ENGINE, "prompts")
PRESETS_FILE = os.path.join(ENGINE, "presets.json")
HOST = os.environ.get("CLIPFORGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("VI_ENGINE_PORT") or os.environ.get("CLIPFORGE_PORT") or "8870")
EXPORT_DIR = os.path.expanduser(os.environ.get("VI_EXPORT_DIR") or "~/Movies/VI Clips")

STAGES = ["verify", "probe", "extract_audio", "transcribe", "signals", "detect", "score", "dedupe", "render", "qc"]
EXPORT_RIGHTS = ("owned", "licensed", "permission_granted")
RIGHTS = ("owned", "licensed", "permission_granted", "pending", "unknown", "denied")
PLATFORMS = ("local", "youtube", "twitch", "kick", "tiktok", "other")
SOURCE_METHODS = ("upload", "folder", "youtube_api", "twitch_api", "ytdlp_authorized")
CATEGORIES = ("funny", "reaction", "argument", "gameplay", "emotional", "revelation", "opinion", "explainer", "one_liner", "story")
SCORE_KEYS = ("hook", "emotion", "curiosity", "entertainment", "standalone", "pacing", "clarity", "replay", "share", "open_frame")
WEIGHTS = {"hook": .18, "standalone": .14, "entertainment": .12, "emotion": .10, "curiosity": .10,
           "pacing": .08, "clarity": .08, "replay": .07, "share": .07, "open_frame": .06}
# target bands in seconds; QC + snapping allow BAND_TOLERANCE either side (pads/payoff tail)
TARGETS = {"15-30": (15.0, 30.0), "30-60": (30.0, 60.0), "60-90": (60.0, 90.0), "auto": (15.0, 90.0)}
BAND_TOLERANCE = 1.0
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".ts")

DEFAULT_SETTINGS = {
    "automation": {"scan_interval_min": 60, "daily_limit": 20, "min_score": 60, "default_target": "auto", "auto_process": False},
    "llm": {"provider": "claude_cli", "model": ""},
    "whisper_model": "small",
    "whisper_model_short": "large-v3-turbo",   # used when the source is ≤ short_source_max_min and no model is chosen
    "short_source_max_min": 30,
    "default_caption_preset": "bold_entertainment",
    "max_clips": 8,
    "storage": {"backend": "local"},
    "render": {"progress_bar": True, "punch_in": False},
}


def ensure_dirs():
    for d in (DATA, os.path.join(DATA, "media"), os.path.join(DATA, "bin"), os.path.join(DATA, "logs")):
        os.makedirs(d, exist_ok=True)

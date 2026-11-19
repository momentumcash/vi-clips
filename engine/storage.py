"""Media storage abstraction. Keys are '/'-separated relative paths ('clips/12/clip.mp4').

LocalStorage keeps everything under data/media/. S3Storage exists so the interface is real, but it is not
configured on this machine and says exactly what it would need.
"""
import os
import shutil

from config import DATA


class NotConfigured(Exception):
    def __init__(self, what, needs):
        super().__init__(f"{what} is not configured; set {', '.join(needs)}")
        self.needs = needs


class Storage:
    name = "base"

    def path(self, key):
        raise NotImplementedError

    def url(self, key):
        return f"/media/{key}" if key else None


class LocalStorage(Storage):
    name = "local"

    def __init__(self, base=None):
        self.base = base or os.path.join(DATA, "media")
        os.makedirs(self.base, exist_ok=True)

    def path(self, key):
        p = os.path.normpath(os.path.join(self.base, key))
        if not p.startswith(os.path.normpath(self.base) + os.sep):
            raise ValueError("key escapes storage root")
        return p

    def ensure_dir(self, key):
        p = self.path(key)
        os.makedirs(p, exist_ok=True)
        return p

    def exists(self, key):
        return bool(key) and os.path.exists(self.path(key))

    def put_file(self, src, key):
        """Copy (never move) a file into storage."""
        dst = self.path(key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return key

    def size(self, key):
        return os.path.getsize(self.path(key))


class S3Storage(Storage):
    name = "s3"
    NEEDS = ["S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"]

    def __init__(self):
        missing = [k for k in self.NEEDS if not os.environ.get(k)]
        # Even with keys present this build has no S3 upload path wired (boto3 is not in the runtime contract).
        raise NotConfigured("S3 storage", missing or ["an S3 client implementation (not built in this version)"])


def status():
    missing = [k for k in S3Storage.NEEDS if not os.environ.get(k)]
    return {"backend": "local", "local_root": os.path.join(DATA, "media"),
            "s3": {"status": "blocked", "needs": missing or ["S3 upload implementation"],
                   "detail": "Local storage is in use. S3 is an interface only in this build."}}
